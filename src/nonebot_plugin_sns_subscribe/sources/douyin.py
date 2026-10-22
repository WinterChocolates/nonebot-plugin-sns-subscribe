"""抖音数据源：用 Playwright 持久化浏览器抓取 Web 接口响应。

抖音 Web 接口需要 ``a_bogus`` 等签名参数，直接在页面里让抖音自己的前端发请求、
插件旁路读取响应，是最稳定的做法（参考 ``nonebot-plugin-douyin-notify``）。
"""

from __future__ import annotations

import re
import sys
import json
import time
import asyncio
import secrets
from enum import Enum
from pathlib import Path
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from collections.abc import Mapping

from nonebot import logger
from playwright.async_api import (
    Page,
    Response,
    Playwright,
    BrowserContext,
    async_playwright,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .base import SourceError
from ..paths import BROWSER_PROFILE_DIR
from ..config import plugin_config
from ..models import (
    Platform,
    Snapshot,
    LiveAction,
    ContentKind,
    PushContent,
    AuthorProfile,
    clean_title,
)
from ..database import repository
from .douyin_parser import DouyinVideo, DouyinSnapshot, parse_snapshot
from ..browser_launch import (
    BrowserCandidate,
    browser_candidates,
    browser_user_agent,
)

PROFILE_ENDPOINT = "/aweme/v1/web/user/profile/other/"
POST_ENDPOINT = "/aweme/v1/web/aweme/post/"
DETAIL_ENDPOINT = "/aweme/v1/web/aweme/detail/"
LIVE_ENTER_ENDPOINT = "/webcast/room/web/enter/"
LOGIN_PAGE_URL = "https://www.douyin.com/"
SEC_USER_ID_PATTERN = re.compile(r"MS4wLjABAAAA[\w\-.]+")
USER_URL_PATTERN = re.compile(r"douyin\.com/user/([\w\-.]+)")
LIVE_RID_PATTERN = re.compile(r"live\.douyin\.com/(\d+)")
SECONDARY_VERIFICATION_MARKERS = ("验证", "verify", "captcha", "安全校验")
SNAPSHOT_CACHE_TTL = 60


class DouyinLoginState(str, Enum):
    WAITING = "waiting"
    SUCCESS = "success"
    EXPIRED = "expired"
    VERIFICATION = "verification"


@dataclass(slots=True)
class DouyinLoginSession:
    key: str
    image: bytes
    url: str
    state: DouyinLoginState


@dataclass(slots=True)
class _LoginRuntime:
    page: Page
    last_state: DouyinLoginState
    expires_at: float


def normalize_douyin_url(value: str) -> str:
    """把用户输入规范成可访问的抖音页面 URL。"""

    text = value.strip()
    if not text:
        raise SourceError("请提供抖音主页链接、分享链接或 sec_user_id")
    if SEC_USER_ID_PATTERN.fullmatch(text):
        return f"https://www.douyin.com/user/{text}"
    if text.startswith(("https://", "http://")):
        return text
    if text.startswith(("v.douyin.com", "www.douyin.com", "live.douyin.com")):
        return f"https://{text}"
    raise SourceError("无法识别的抖音地址，请使用抖音 App 的分享链接或用户主页链接")


def sec_user_id_from_url(url: str) -> str:
    match = USER_URL_PATTERN.search(url)
    return match.group(1) if match else ""


def web_rid_from_url(url: str) -> str:
    match = LIVE_RID_PATTERN.search(url)
    return match.group(1) if match else ""


COOKIE_DOMAIN = ".douyin.com"
SESSION_COOKIE_NAMES = frozenset({"sessionid", "sessionid_ss"})


def parse_cookie_string(raw: str) -> list[dict[str, object]]:
    """把用户粘贴的内容解析成 Playwright Cookie。

    支持三种常见格式：

    - DevTools 的 ``a=b; c=d``（可带 ``Cookie:`` 前缀，可多行）
    - Cookie-Editor 等扩展导出的 JSON 数组 ``[{"name":..,"value":..}]``
    - 简单 JSON 对象 ``{"sessionid": "...", ...}``
    """

    text = (raw or "").strip()
    if not text:
        return []
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    cookies: list[dict[str, object]] = []
    seen: set[str] = set()

    def add(name: object, value: object) -> None:
        key = str(name).strip()
        if not key or key in seen:
            return
        seen.add(key)
        cookies.append(
            {"name": key, "value": str(value), "domain": COOKIE_DOMAIN, "path": "/"}
        )

    if text.startswith(("[", "{")):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, Mapping) and item.get("name") is not None:
                    add(item["name"], item.get("value", ""))
            if cookies:
                return cookies
        elif isinstance(data, Mapping):
            for name, value in data.items():
                add(name, value)
            return cookies

    normalized = text.replace("\r", ";").replace("\n", ";")
    for segment in normalized.split(";"):
        piece = segment.strip()
        if "=" not in piece:
            continue
        name, _, value = piece.partition("=")
        add(name, value)
    return cookies


async def ensure_chromium() -> None:
    """确保 Playwright Chromium 可用，首次运行自动安装。"""

    if not plugin_config.sns_auto_install_browser:
        raise SourceError("未找到可用的 Chromium，请执行 `playwright install chromium`")
    logger.info("SNS subscription: 未找到 Playwright Chromium，开始自动安装……")
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "playwright",
            "install",
            "chromium",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
    except Exception as exc:
        raise SourceError(
            "无法启动 Chromium 安装器，请手动执行 `playwright install chromium`"
        ) from exc
    if process.returncode != 0:
        detail = output.decode(errors="replace").strip()[-800:]
        raise SourceError(f"Chromium 安装失败（退出码 {process.returncode}）：{detail}")
    logger.info("SNS subscription: Playwright Chromium 安装完成")


class DouyinSource:
    """抖音数据源。"""

    platform = Platform.DOUYIN

    def __init__(self, profile_dir: Path = BROWSER_PROFILE_DIR) -> None:
        self.profile_dir = profile_dir
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._context_closed = True
        self._context_started_at = 0.0
        self._active_pages = 0
        self._start_lock = asyncio.Lock()
        self._cache: dict[str, tuple[float, DouyinSnapshot]] = {}
        self._logins: dict[str, _LoginRuntime] = {}
        self._browser_label = ""

    # ------------------------------------------------------------- 浏览器生命周期
    @property
    def browser_label(self) -> str:
        return self._browser_label or "尚未启动"

    def invalidate(self) -> None:
        self._cache.clear()

    async def close(self) -> None:
        for key in list(self._logins):
            await self._end_login_runtime(key)
        async with self._start_lock:
            await self._dispose()

    async def _dispose(self) -> None:
        context, playwright = self._context, self._playwright
        self._context = None
        self._playwright = None
        self._context_closed = True
        self._context_started_at = 0.0
        self._active_pages = 0
        self._logins.clear()
        if context is not None:
            with suppress(Exception):
                await context.close()
        if playwright is not None:
            with suppress(Exception):
                await playwright.stop()

    def _maintenance_due(self) -> bool:
        return bool(
            self._context is not None
            and not self._context_closed
            and self._context_started_at
            and not self._logins
            and self._active_pages == 0
            and time.monotonic() - self._context_started_at
            >= plugin_config.sns_douyin_browser_restart_interval
        )

    async def _ensure_context(self) -> BrowserContext:
        if (
            self._context is not None
            and not self._context_closed
            and not self._maintenance_due()
        ):
            return self._context
        async with self._start_lock:
            if (
                self._context is not None
                and not self._context_closed
                and not self._maintenance_due()
            ):
                return self._context
            await self._dispose()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            playwright = await async_playwright().start()
            kwargs: dict[str, object] = {
                "headless": plugin_config.sns_douyin_headless,
                "locale": "zh-CN",
                "viewport": {"width": 1440, "height": 900},
                "args": ["--disable-dev-shm-usage"],
            }
            user_agent = browser_user_agent()
            if user_agent:
                kwargs["user_agent"] = user_agent
            candidates = browser_candidates()
            try:
                context = await self._launch_context(playwright, kwargs, candidates)
            except Exception:
                with suppress(Exception):
                    await playwright.stop()
                raise
            self._playwright = playwright
            self._context = context
            self._context_closed = False
            self._context_started_at = time.monotonic()
            context.on("close", lambda _: self._mark_context_closed(context))
            await self._apply_cookie(context)
            return context

    async def _launch_context(
        self,
        playwright: Playwright,
        base_kwargs: dict[str, object],
        candidates: list[BrowserCandidate],
    ) -> BrowserContext:
        """按优先级尝试系统 Chrome / Edge，最后回退 Playwright Chromium。"""

        errors: list[str] = []
        for candidate in candidates:
            try:
                context = await playwright.chromium.launch_persistent_context(
                    str(self.profile_dir),
                    **base_kwargs,  # type: ignore[arg-type]
                    **candidate.launch_kwargs(),
                )
            except Exception as exc:  # 换下一个候选浏览器
                errors.append(f"{candidate.label}（{type(exc).__name__}）")
                continue
            self._browser_label = candidate.label
            logger.info("SNS subscription: 抖音抓取使用 {}", candidate.label)
            return context

        bundled = next(
            (item for item in candidates if not item.uses_system_browser), None
        )
        if bundled is None:
            raise SourceError("没有可用的浏览器：" + "、".join(errors))
        # 系统浏览器全部失败且 Playwright Chromium 缺失时安装后重试一次
        await ensure_chromium()
        context = await playwright.chromium.launch_persistent_context(
            str(self.profile_dir),
            **base_kwargs,  # type: ignore[arg-type]
            **bundled.launch_kwargs(),
        )
        self._browser_label = bundled.label
        logger.info("SNS subscription: 抖音抓取使用 {}", bundled.label)
        return context

    def _mark_context_closed(self, context: BrowserContext) -> None:
        if self._context is context:
            self._context_closed = True
            self._active_pages = 0

    async def cookie_string(self) -> str:
        value = await repository.get_setting("douyin_cookie")
        if value:
            return value
        configured = plugin_config.sns_douyin_cookie
        return configured.get_secret_value().strip() if configured else ""

    async def _apply_cookie(self, context: BrowserContext) -> int:
        cookies = parse_cookie_string(await self.cookie_string())
        if not cookies:
            return 0
        with suppress(Exception):
            await context.add_cookies(cookies)
            names = {str(cookie["name"]) for cookie in cookies}
            if names & SESSION_COOKIE_NAMES:
                logger.info(
                    "SNS subscription: 已向抖音浏览器注入 {} 个 Cookie（含登录态）",
                    len(cookies),
                )
            else:
                logger.warning(
                    "SNS subscription: 抖音 Cookie 缺少 sessionid，浏览器仍是未登录状态"
                )
            return len(cookies)
        return 0

    async def apply_cookie(self, raw: str) -> dict[str, object]:
        """保存 Cookie 并立即注入正在运行的浏览器（无需重启插件）。"""

        await repository.set_setting("douyin_cookie", raw.strip())
        self._cache.clear()
        cookies = parse_cookie_string(raw)
        names = [str(cookie["name"]) for cookie in cookies]
        live = self._context is not None and not self._context_closed
        if not cookies:
            if live and self._context is not None:
                with suppress(Exception):
                    await self._context.clear_cookies()
            return {"count": 0, "live": live, "has_session": False, "names": []}
        if live and self._context is not None:
            with suppress(Exception):
                await self._context.add_cookies(cookies)
            logger.info(
                "SNS subscription: 已向运行中的抖音浏览器注入 {} 个 Cookie",
                len(cookies),
            )
        return {
            "count": len(cookies),
            "live": live,
            "has_session": bool(set(names) & SESSION_COOKIE_NAMES),
            "names": names,
        }

    async def stored_has_session(self) -> bool:
        """数据库里保存的 Cookie 是否包含登录态字段。"""

        names = {
            str(cookie["name"])
            for cookie in parse_cookie_string(await self.cookie_string())
        }
        return bool(names & SESSION_COOKIE_NAMES)

    async def browser_has_session(self) -> bool:
        """浏览器上下文中是否真的存在登录态 Cookie。"""

        if self._context is None or self._context_closed:
            return False
        with suppress(Exception):
            cookies = await self._context.cookies("https://www.douyin.com/")
            names = {str(cookie.get("name", "")) for cookie in cookies}
            return bool(names & SESSION_COOKIE_NAMES)
        return False

    async def verify_session(self, target: str = "") -> dict[str, object]:
        """检查 Cookie 是否注入浏览器；给了 target 就真实读取一次作品列表。"""

        result: dict[str, object] = {
            "has_session": False,
            "cookie_count": 0,
            "target": target,
            "profile": "",
            "works": None,
            "error": "",
        }
        page: Page | None = None
        try:
            page = await self._new_page()
        except SourceError as exc:
            result["error"] = str(exc)
            return result
        try:
            cookies = await page.context.cookies("https://www.douyin.com/")
            names = {str(cookie.get("name", "")) for cookie in cookies}
            result["cookie_count"] = len(cookies)
            result["has_session"] = bool(names & SESSION_COOKIE_NAMES)
        finally:
            with suppress(Exception):
                await page.close()

        if not target:
            return result
        try:
            profile = await self.resolve(target)
            snapshot = await self._snapshot(profile.author_id)
            result["profile"] = profile.name
            result["works"] = len(snapshot.posts)
        except SourceError as exc:
            result["error"] = str(exc)
        except Exception as exc:  # 诊断信息尽量返回给界面
            result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    async def save_cookies(self) -> str:
        """把当前浏览器 Cookie 写入数据库，便于重启后复用。"""

        if self._context is None:
            return ""
        cookies = await self._context.cookies("https://www.douyin.com/")
        pairs = [
            f"{cookie['name']}={cookie['value']}"
            for cookie in cookies
            if cookie.get("name") and cookie.get("value")
        ]
        raw = "; ".join(pairs)
        if raw:
            await repository.set_setting("douyin_cookie", raw)
            self._cache.clear()
        return raw

    async def has_cookie(self) -> bool:
        return bool(await self.cookie_string())

    async def _new_page(self) -> Page:
        last_error: Exception | None = None
        for attempt in range(2):
            context: BrowserContext | None = None
            try:
                context = await self._ensure_context()
                page = await asyncio.wait_for(
                    context.new_page(),
                    timeout=plugin_config.sns_douyin_page_timeout / 1000,
                )
                if page.is_closed():
                    raise RuntimeError("Chromium 返回了已关闭的页面")
                self._active_pages += 1
                page.on(
                    "close",
                    lambda _, page_context=context: self._mark_page_closed(
                        page_context
                    ),
                )
                return page
            except Exception as exc:  # 浏览器可能以任意方式崩溃
                last_error = exc
                if context is not None and self._context is context:
                    async with self._start_lock:
                        if self._context is context:
                            await self._dispose()
                if attempt == 0:
                    logger.warning(
                        "SNS subscription: 抖音浏览器不可用（{}: {}），正在重启",
                        type(exc).__name__,
                        str(exc)[:200],
                    )
        detail = f"{type(last_error).__name__}: {str(last_error)[:300]}"
        raise SourceError(
            "Chromium 浏览器进程不可用，插件自动重启后仍未恢复"
            f"（{detail}）；请检查可用内存与 /dev/shm 容量"
        )

    def _mark_page_closed(self, context: BrowserContext) -> None:
        if self._context is context:
            self._active_pages = max(0, self._active_pages - 1)

    # ------------------------------------------------------------------ 快照
    async def _fetch_snapshot(
        self, sec_user_id: str, *, fallback_web_rid: str = ""
    ) -> DouyinSnapshot:
        if not sec_user_id or sec_user_id == "self":
            raise SourceError("sec_user_id 无效")
        page = await self._new_page()
        loop = asyncio.get_running_loop()
        profile_future: asyncio.Future[Mapping[str, object]] = loop.create_future()
        post_future: asyncio.Future[Mapping[str, object]] = loop.create_future()
        tasks: set[asyncio.Task[None]] = set()
        statuses: dict[str, int] = {}
        failures: dict[str, str] = {}

        async def inspect(response: Response) -> None:
            parsed = urlparse(response.url)
            target: asyncio.Future[Mapping[str, object]] | None = None
            if parsed.path == PROFILE_ENDPOINT:
                target = profile_future
                statuses["profile"] = response.status
            elif parsed.path == POST_ENDPOINT:
                response_sec_user_id = parse_qs(parsed.query).get("sec_user_id", [""])[
                    0
                ]
                if response_sec_user_id and response_sec_user_id != sec_user_id:
                    return
                target = post_future
                statuses["post"] = response.status
            if target is None or target.done():
                return
            try:
                payload = await asyncio.wait_for(
                    response.json(),
                    timeout=plugin_config.sns_douyin_page_timeout / 1000,
                )
            except Exception as exc:  # 单个响应解析失败不影响整体
                digest = await self._response_digest(response, exc)
                failures[parsed.path] = digest
                if parsed.path == POST_ENDPOINT:
                    logger.warning(
                        "SNS subscription: 抖音作品列表响应无法解析（HTTP {}）：{}",
                        response.status,
                        digest,
                    )
                return
            if not isinstance(payload, Mapping):
                failures[parsed.path] = f"payload={type(payload).__name__}"
                return
            if not target.done():
                target.set_result(payload)

        def schedule(response: Response) -> None:
            task = asyncio.create_task(inspect(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on("response", schedule)
        try:
            try:
                await page.goto(
                    f"https://www.douyin.com/user/{sec_user_id}",
                    wait_until="domcontentloaded",
                    timeout=plugin_config.sns_douyin_page_timeout,
                )
            except PlaywrightTimeoutError:
                if page.url == "about:blank":
                    raise
            await page.wait_for_timeout(plugin_config.sns_douyin_page_settle_ms)
            with suppress(Exception):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.wait(
                {profile_future, post_future},
                timeout=plugin_config.sns_douyin_page_timeout / 1000,
                return_when=asyncio.ALL_COMPLETED,
            )
            if not profile_future.done():
                status = statuses.get("profile")
                suffix = f"（HTTP {status}）" if status is not None else ""
                raise SourceError(
                    f"抖音用户资料接口未返回数据{suffix}：页面可能未加载完成、"
                    "登录已失效或网络受限，请稍后重试"
                )
            if not post_future.done():
                raise SourceError(
                    await self._post_error_message(
                        statuses.get("post"), failures.get(POST_ENDPOINT, "")
                    )
                )
            dom_web_rid = fallback_web_rid
            if not dom_web_rid:
                with suppress(Exception):
                    links = await page.locator(
                        'a[href*="live.douyin.com/"]'
                    ).evaluate_all(
                        "elements => elements.map(e => e.href).filter(Boolean)"
                    )
                    dom_web_rid = next(
                        (rid for link in links if (rid := web_rid_from_url(link))), ""
                    )
            return parse_snapshot(
                profile_future.result(),
                post_future.result(),
                fallback_sec_user_id=sec_user_id,
                fallback_web_rid=dom_web_rid,
            )
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"获取抖音数据失败：{type(exc).__name__}") from exc
        finally:
            page.remove_listener("response", schedule)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            with suppress(Exception):
                await page.close()

    @staticmethod
    async def _response_digest(response: Response, error: Exception) -> str:
        """尽力读取响应正文片段，便于定位空响应 / 风控页面。"""

        snippet = ""
        with suppress(Exception):
            text = await asyncio.wait_for(response.text(), timeout=5)
            snippet = " ".join(str(text).split())[:160]
        detail = type(error).__name__
        if snippet:
            detail += f"：{snippet}"
        return detail

    async def _post_error_message(self, status: int | None, digest: str) -> str:
        if status is None:
            reason = "页面没有触发作品列表接口"
        elif not digest:
            reason = "接口返回了空响应"
        elif "json" in digest.lower() or "expecting" in digest.lower():
            reason = "接口返回的不是 JSON（通常是风控验证页）"
        else:
            reason = "接口响应无法解析"
        detail = f"（HTTP {status}）" if status is not None else ""
        if not await self.has_cookie():
            hint = "当前未配置抖音 Cookie，请先在 Web 后台「账号登录」扫码登录"
        elif not await self.browser_has_session():
            hint = (
                "Cookie 已保存但浏览器里没有生效（缺少 sessionid 或已过期），"
                "请重新扫码登录，或重新粘贴包含 sessionid 的完整 Cookie"
            )
        else:
            hint = (
                "浏览器已是登录态但接口仍返回空，通常是抖音风控，"
                "请重新登录并在登录页完成滑块/验证后再试"
            )
        return (
            f"抖音作品列表接口未就绪{detail}：{reason}；{hint}；"
            "也可以在后台「账号登录 → 打开抖音登录页」里浏览一次该主页后再试"
        )

    async def _snapshot(
        self, sec_user_id: str, *, fallback_web_rid: str = ""
    ) -> DouyinSnapshot:
        cached = self._cache.get(sec_user_id)
        now = time.monotonic()
        if (
            cached is not None
            and now - cached[0] < SNAPSHOT_CACHE_TTL
            and (not fallback_web_rid or cached[1].web_rid)
        ):
            return cached[1]
        snapshot = await asyncio.wait_for(
            self._fetch_snapshot(sec_user_id, fallback_web_rid=fallback_web_rid),
            timeout=plugin_config.sns_douyin_operation_timeout,
        )
        self._cache[sec_user_id] = (now, snapshot)
        return snapshot

    # -------------------------------------------------------------- 数据源接口
    async def resolve(self, target: str) -> AuthorProfile:
        url = normalize_douyin_url(target)
        direct = sec_user_id_from_url(url)
        if direct:
            return self._profile(await self._snapshot(direct))
        sec_user_id = await self._resolve_identity(url)
        web_rid = web_rid_from_url(url)
        return self._profile(
            await self._snapshot(sec_user_id, fallback_web_rid=web_rid)
        )

    async def _resolve_identity(self, url: str) -> str:
        page = await self._new_page()
        loop = asyncio.get_running_loop()
        identity: asyncio.Future[str] = loop.create_future()
        tasks: set[asyncio.Task[None]] = set()

        async def inspect(response: Response) -> None:
            parsed = urlparse(response.url)
            if parsed.path not in {DETAIL_ENDPOINT, LIVE_ENTER_ENDPOINT}:
                return
            if identity.done():
                return
            try:
                payload = await asyncio.wait_for(
                    response.json(),
                    timeout=plugin_config.sns_douyin_page_timeout / 1000,
                )
            except Exception:  # 单个响应解析失败不影响整体
                return
            sec_user_id = self._identity_from_payload(parsed.path, payload)
            if sec_user_id and not identity.done():
                identity.set_result(sec_user_id)

        def schedule(response: Response) -> None:
            task = asyncio.create_task(inspect(response))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        page.on("response", schedule)
        try:
            try:
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=plugin_config.sns_douyin_page_timeout,
                )
            except PlaywrightTimeoutError:
                if page.url == "about:blank":
                    raise
            await page.wait_for_timeout(plugin_config.sns_douyin_page_settle_ms)
            resolved = sec_user_id_from_url(page.url)
            if not resolved and identity.done():
                resolved = identity.result()
            if not resolved:
                links = await page.locator('a[href*="/user/"]').evaluate_all(
                    "elements => elements.map(e => e.href).filter(Boolean)"
                )
                for link in links:
                    candidate = sec_user_id_from_url(link)
                    if candidate and candidate != "self":
                        resolved = candidate
                        break
            if not resolved or resolved == "self":
                raise SourceError("无法从该链接识别抖音作者，请使用用户主页链接")
            return resolved
        except SourceError:
            raise
        except Exception as exc:
            raise SourceError(f"打开抖音链接失败：{type(exc).__name__}") from exc
        finally:
            page.remove_listener("response", schedule)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            with suppress(Exception):
                await page.close()

    @staticmethod
    def _identity_from_payload(path: str, payload: Mapping[str, object]) -> str:
        if path == DETAIL_ENDPOINT:
            detail = payload.get("aweme_detail") or payload.get("awemeDetail") or {}
            author = detail.get("author", {}) if isinstance(detail, Mapping) else {}
            if isinstance(author, Mapping):
                return str(author.get("sec_uid") or author.get("secUid") or "")
        if path == LIVE_ENTER_ENDPOINT:
            data = payload.get("data", {})
            rooms = data.get("data", []) if isinstance(data, Mapping) else []
            room = rooms[0] if isinstance(rooms, list) and rooms else {}
            owner = room.get("owner", {}) if isinstance(room, Mapping) else {}
            if isinstance(owner, Mapping):
                return str(owner.get("sec_uid") or owner.get("secUid") or "")
        return ""

    async def fetch_posts(self, author_id: str) -> list[PushContent]:
        snapshot = await self._snapshot(author_id)
        profile = self._profile(snapshot)
        return [self._video_content(profile, video) for video in snapshot.videos]

    async def fetch_live(self, author_id: str) -> PushContent | None:
        snapshot = await self._snapshot(author_id)
        return self._live_content(self._profile(snapshot), snapshot)

    async def fetch_snapshot(self, author_id: str) -> Snapshot:
        snapshot = await self._snapshot(author_id)
        profile = self._profile(snapshot)
        return Snapshot(
            profile=profile,
            posts=[self._video_content(profile, video) for video in snapshot.videos],
            live=self._live_content(profile, snapshot),
        )

    # ------------------------------------------------------------------ 转换
    @staticmethod
    def _profile(snapshot: DouyinSnapshot) -> AuthorProfile:
        return AuthorProfile(
            platform=Platform.DOUYIN,
            author_id=snapshot.sec_user_id,
            name=snapshot.nickname,
            avatar=snapshot.avatar_url,
            url=f"https://www.douyin.com/user/{snapshot.sec_user_id}",
            extra={"web_rid": snapshot.web_rid},
        )

    @staticmethod
    def _video_content(profile: AuthorProfile, video: DouyinVideo) -> PushContent:
        return PushContent(
            platform=Platform.DOUYIN,
            id=video.aweme_id,
            author_id=profile.author_id,
            kind=ContentKind.VIDEO,
            title=clean_title(video.description, "发布了新视频"),
            url=f"https://www.douyin.com/video/{video.aweme_id}",
            author_name=profile.name,
            author_avatar=profile.avatar,
            cover=video.cover_url,
            published_at=video.create_time,
        )

    @staticmethod
    def _live_content(profile: AuthorProfile, snapshot: DouyinSnapshot) -> PushContent:
        live = snapshot.live
        action = LiveAction.START if live.is_live else LiveAction.STOP
        url = f"https://live.douyin.com/{live.web_rid}" if live.web_rid else profile.url
        title = live.title or ("正在直播" if live.is_live else "直播已结束")
        if not live.is_live:
            title = "直播结束：" + title
        return PushContent(
            platform=Platform.DOUYIN,
            id=live.room_id if live.is_live else (live.web_rid or "0"),
            author_id=profile.author_id,
            kind=ContentKind.LIVE,
            title=title,
            url=url,
            author_name=profile.name,
            author_avatar=profile.avatar,
            # 抖音直播间常常解析不到封面，此时直接用博主头像兜底
            cover=live.cover_url or profile.avatar,
            live_action=action,
        )

    # ------------------------------------------------------------------ 登录
    async def _is_authenticated(self, page: Page) -> bool:
        try:
            cookies = await page.context.cookies("https://www.douyin.com/")
        except Exception:  # 浏览器可能已关闭
            return False
        names = {str(cookie.get("name", "")) for cookie in cookies}
        return bool(names & SESSION_COOKIE_NAMES)

    async def _screenshot(self, page: Page) -> bytes:
        try:
            return await page.screenshot(type="png", full_page=False)
        except Exception:  # 截图失败不影响登录流程
            return b""

    async def _click_login_entry(self, page: Page) -> bool:
        selectors = (
            'button:has-text("登录")',
            'a:has-text("登录")',
            '[role="button"]:has-text("登录")',
            'text="登录"',
        )
        for selector in selectors:
            locator = page.locator(selector)
            for index in range(min(await locator.count(), 8)):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_visible():
                        await candidate.click(timeout=3000)
                        return True
                except Exception:  # 逐个尝试选择器
                    continue
        return False

    async def begin_login(self) -> DouyinLoginSession:
        if self._logins:
            raise SourceError("已有抖音登录会话进行中，请等待其完成或关闭")
        page = await self._new_page()
        try:
            try:
                await page.goto(
                    LOGIN_PAGE_URL,
                    wait_until="domcontentloaded",
                    timeout=plugin_config.sns_douyin_page_timeout,
                )
            except PlaywrightTimeoutError:
                if page.url == "about:blank":
                    raise
            await page.wait_for_timeout(plugin_config.sns_douyin_page_settle_ms)
            if await self._is_authenticated(page):
                await self.save_cookies()
                image = await self._screenshot(page)
                with suppress(Exception):
                    await page.close()
                return DouyinLoginSession("", image, page.url, DouyinLoginState.SUCCESS)
            await self._click_login_entry(page)
            await page.wait_for_timeout(1500)
            key = secrets.token_urlsafe(18)
            self._logins[key] = _LoginRuntime(
                page=page,
                last_state=DouyinLoginState.WAITING,
                expires_at=time.monotonic() + plugin_config.sns_douyin_login_timeout,
            )
            return DouyinLoginSession(
                key, await self._screenshot(page), page.url, DouyinLoginState.WAITING
            )
        except Exception:
            with suppress(Exception):
                await page.close()
            raise

    async def _login_runtime(self, key: str) -> _LoginRuntime:
        runtime = self._logins.get(key)
        if runtime is None:
            raise SourceError("登录会话不存在或已结束，请重新发起登录")
        if runtime.expires_at <= time.monotonic():
            runtime.last_state = DouyinLoginState.EXPIRED
        return runtime

    async def _end_login_runtime(self, key: str) -> None:
        runtime = self._logins.pop(key, None)
        if runtime is None:
            return
        with suppress(Exception):
            await runtime.page.close()

    async def login_status(self, key: str) -> DouyinLoginSession:
        runtime = await self._login_runtime(key)
        page = runtime.page
        if runtime.last_state is DouyinLoginState.EXPIRED:
            image = await self._screenshot(page)
            await self._end_login_runtime(key)
            return DouyinLoginSession(key, image, page.url, DouyinLoginState.EXPIRED)
        if page.is_closed():
            runtime.last_state = DouyinLoginState.EXPIRED
            await self._end_login_runtime(key)
            return DouyinLoginSession(key, b"", "", DouyinLoginState.EXPIRED)
        if await self._is_authenticated(page):
            await self.save_cookies()
            image = await self._screenshot(page)
            await self._end_login_runtime(key)
            return DouyinLoginSession(key, image, page.url, DouyinLoginState.SUCCESS)
        image = await self._screenshot(page)
        return DouyinLoginSession(key, image, page.url, runtime.last_state)

    async def login_action(
        self,
        key: str,
        action: str,
        *,
        x: float | None = None,
        y: float | None = None,
        text: str = "",
        key_name: str = "",
    ) -> DouyinLoginSession:
        runtime = await self._login_runtime(key)
        page = runtime.page
        if action == "click":
            if x is None or y is None:
                raise SourceError("点击操作缺少坐标")
            await page.mouse.click(x, y)
        elif action == "type":
            if not text:
                raise SourceError("输入内容不能为空")
            await page.keyboard.type(text, delay=60)
        elif action == "press":
            await page.keyboard.press(key_name or "Enter")
        elif action == "refresh":
            await page.reload(wait_until="domcontentloaded")
        elif action == "screenshot":
            pass
        else:
            raise SourceError("不支持的登录操作")
        await page.wait_for_timeout(800)
        return await self.login_status(key)

    async def end_login(self, key: str) -> None:
        await self._end_login_runtime(key)


douyin_source = DouyinSource()
