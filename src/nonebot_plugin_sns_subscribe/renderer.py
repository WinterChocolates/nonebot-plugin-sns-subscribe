"""卡片渲染：Jinja2 模板 + Playwright 截图。

模板变量：
``content``（PushContent）、``font_css``、``accent_color``、``gradient_color``、
``theme_background``、``platform``、``platform_label``、``render_kind``。

自定义模板必须包含 ``id="sns-card"``、``id="bili-card"`` 或 ``id="douyin-card"``
三者之一，截图只截该元素。
"""

from __future__ import annotations

import re
import sys
import base64
import asyncio
import mimetypes
from pathlib import Path
from contextlib import suppress
from urllib.parse import urlparse

from jinja2 import StrictUndefined, select_autoescape
from nonebot import logger
from jinja2.sandbox import SandboxedEnvironment
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Browser, Playwright, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .paths import builtin_template_path
from .config import plugin_config
from .models import Platform, ContentKind, PushContent, RenderSelection
from .browser_launch import BrowserUnavailableError, browser_candidates

ALLOWED_IMAGE_HOST_SUFFIXES = (
    ".hdslb.com",
    ".biliimg.com",
    ".bilibili.com",
    ".douyinpic.com",
    ".douyincdn.com",
    ".douyinstatic.com",
    ".byteimg.com",
    ".pstatp.com",
    ".douyin.com",
    "p.qlogo.cn",
)
CARD_ID_PATTERN = re.compile(r"""id\s*=\s*["'](sns-card|bili-card|douyin-card)["']""")
COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
RENDER_DEVICE_SCALE_FACTOR = 2

# 内容类型 -> 可自定义的模板种类
TEMPLATE_KINDS: dict[Platform, dict[ContentKind, str]] = {
    Platform.BILIBILI: {
        ContentKind.DYNAMIC: "dynamic",
        ContentKind.VIDEO: "video",
        ContentKind.ARTICLE: "dynamic",
        ContentKind.MUSIC: "dynamic",
        ContentKind.LIVE: "live",
    },
    Platform.DOUYIN: {
        ContentKind.DYNAMIC: "video",
        ContentKind.VIDEO: "video",
        ContentKind.ARTICLE: "video",
        ContentKind.MUSIC: "video",
        ContentKind.LIVE: "live",
    },
}
CUSTOMIZABLE_KINDS: dict[Platform, tuple[str, ...]] = {
    Platform.BILIBILI: ("dynamic", "video", "live"),
    Platform.DOUYIN: ("video", "live"),
}


def render_kind(platform: Platform, kind: ContentKind) -> str:
    return TEMPLATE_KINDS[platform][kind]


def find_card_id(source: str) -> str | None:
    match = CARD_ID_PATTERN.search(source)
    return match.group(1) if match else None


def validate_template_source(source: str) -> str:
    """校验自定义模板并返回卡片元素 id。"""

    if not source.strip():
        raise ValueError("模板内容不能为空")
    if len(source) > 512 * 1024:
        raise ValueError("模板内容不能超过 512 KiB")
    card_id = find_card_id(source)
    if card_id is None:
        raise ValueError(
            '模板必须包含 id="sns-card"（也兼容 id="bili-card" / id="douyin-card"）'
        )
    return card_id


class CardRenderer:
    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._browser_lock = asyncio.Lock()
        self._browser_label = ""
        self._environment = SandboxedEnvironment(
            autoescape=select_autoescape(("html", "xml")),
            undefined=StrictUndefined,
        )

    # ------------------------------------------------------------------ 浏览器
    @property
    def browser_label(self) -> str:
        return self._browser_label or "尚未启动"

    async def _launch_browser(self, playwright: Playwright) -> Browser:
        errors: list[str] = []
        for candidate in browser_candidates():
            try:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage"],
                    **candidate.launch_kwargs(),
                )
            except Exception as exc:  # 换下一个候选浏览器
                errors.append(f"{candidate.label}（{type(exc).__name__}）")
                continue
            self._browser_label = candidate.label
            logger.info("SNS subscription: 卡片渲染使用 {}", candidate.label)
            return browser
        raise BrowserUnavailableError("、".join(errors))

    async def _get_browser(self) -> Browser:
        if self._browser is not None:
            return self._browser
        async with self._browser_lock:
            if self._browser is None:
                playwright = await async_playwright().start()
                try:
                    self._browser = await self._launch_browser(playwright)
                except Exception:
                    with suppress(Exception):
                        await playwright.stop()
                    raise
                self._playwright = playwright
        return self._browser

    async def prepare(self) -> bool:
        """启动时预热浏览器，缺失时按配置自动安装。"""

        try:
            await self._get_browser()
        except Exception as first_error:  # 浏览器缺失/不可启动
            await self.close()
            if not plugin_config.sns_auto_install_browser:
                logger.opt(exception=first_error).error(
                    "SNS subscription: 没有可用的浏览器且已禁用自动安装；"
                    "请安装 Chrome / Edge，设置 SNS_BROWSER_CHANNEL，"
                    "或执行 `playwright install chromium`"
                )
                return False
        else:
            return True
        logger.info(
            "SNS subscription: 系统浏览器不可用，开始安装 Playwright Chromium……"
        )
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
        except Exception:
            logger.exception("SNS subscription: 无法启动 Chromium 安装器")
            return False
        if process.returncode != 0:
            logger.error(
                "SNS subscription: Chromium 安装失败（退出码 {}）：{}",
                process.returncode,
                output.decode(errors="replace").strip()[-2000:],
            )
            return False
        try:
            await self._get_browser()
        except Exception:
            await self.close()
            logger.exception(
                "SNS subscription: Chromium 下载完成但无法启动；"
                "Linux 下请执行 `playwright install --with-deps chromium`"
            )
            return False
        logger.info("SNS subscription: Playwright Chromium 已就绪")
        return True

    async def close(self) -> None:
        if self._browser is not None:
            with suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    async def reset(self) -> None:
        """丢弃当前 Chromium 进程，下次渲染时重新启动。"""

        async with self._browser_lock:
            browser, playwright = self._browser, self._playwright
            self._browser = None
            self._playwright = None
            if browser is not None:
                with suppress(Exception):
                    await browser.close()
            if playwright is not None:
                with suppress(Exception):
                    await playwright.stop()

    # ------------------------------------------------------------------ 模板
    @staticmethod
    def _font_css(font_path: str | None) -> str:
        if not font_path:
            return ""
        path = Path(font_path)
        if not path.is_file():
            return ""
        mime = mimetypes.guess_type(path.name)[0] or "font/ttf"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        font_format = {
            ".otf": "opentype",
            ".ttf": "truetype",
            ".ttc": "truetype",
            ".woff": "woff",
            ".woff2": "woff2",
        }.get(path.suffix.lower(), "truetype")
        return (
            "@font-face{font-family:'SnsCustom';"
            f"src:url(data:{mime};base64,{encoded}) format('{font_format}');"
            "font-display:block}"
            "body,body *{font-family:'SnsCustom','Microsoft YaHei',"
            "sans-serif !important}"
        )

    def template_source(self, content: PushContent, selection: RenderSelection) -> str:
        if selection.template_path:
            return Path(selection.template_path).read_text(encoding="utf-8")
        kind = render_kind(content.platform, content.kind)
        path = builtin_template_path(content.platform.value, kind)
        if not path.is_file():
            path = builtin_template_path(content.platform.value, "video")
        return path.read_text(encoding="utf-8")

    def _theme_css(
        self, content: PushContent, selection: RenderSelection
    ) -> tuple[str, str, str, str]:
        accent = (
            selection.accent_color
            if selection.accent_color
            and COLOR_PATTERN.fullmatch(selection.accent_color)
            else content.platform.accent
        )
        gradient = (
            selection.gradient_color
            if selection.gradient_color
            and COLOR_PATTERN.fullmatch(selection.gradient_color)
            else ""
        )
        background = (
            f"linear-gradient(135deg, {accent} 0%, {gradient} 100%)"
            if gradient
            else accent
        )
        variables = (
            f"--sns-accent:{accent};--sns-accent-2:{gradient or accent};"
            f"--sns-theme:{background};"
            f"--bili-accent:{accent};--bili-accent-2:{gradient or accent};"
            f"--bili-theme:{background};"
            f"--douyin-accent:{accent};--douyin-accent-2:{gradient or accent};"
            f"--douyin-theme:{background}"
        )
        return accent, gradient, background, variables

    # ------------------------------------------------------------------ 渲染
    async def render(
        self,
        content: PushContent,
        selection: RenderSelection,
        template_source: str | None = None,
    ) -> bytes:
        template_text = (
            template_source
            if template_source is not None
            else self.template_source(content, selection)
        )
        template = self._environment.from_string(template_text)
        accent, gradient, background, variables = self._theme_css(content, selection)
        theme_css = f":root{{{variables}}}"
        font_css = self._font_css(selection.font_path)
        html = template.render(
            content=content,
            font_css=font_css + theme_css,
            theme_css=theme_css,
            accent_color=accent,
            gradient_color=gradient,
            theme_background=background,
            platform=content.platform.value,
            platform_label=content.platform.label,
            render_kind=render_kind(content.platform, content.kind),
        )
        if font_css and font_css not in html:
            style = f"<style>{font_css}{theme_css}</style>"
            html = html.replace("</head>", f"{style}</head>", 1)
            if style not in html:
                html = style + html
        card_id = find_card_id(html) or "sns-card"

        try:
            return await self._screenshot_html(html, card_id)
        except PlaywrightError:
            logger.warning("SNS subscription: Chromium 渲染失败，重启浏览器后重试一次")
            await self.reset()
            return await self._screenshot_html(html, card_id)

    async def _screenshot_html(self, html: str, card_id: str) -> bytes:
        browser = await self._get_browser()
        if not browser.is_connected():
            raise PlaywrightError("browser is not connected")
        context = await browser.new_context(
            viewport={"width": plugin_config.sns_render_width, "height": 900},
            device_scale_factor=RENDER_DEVICE_SCALE_FACTOR,
            java_script_enabled=False,
        )
        page = await context.new_page()

        async def route_handler(route: object) -> None:
            request = route.request  # type: ignore[attr-defined]
            host = (urlparse(request.url).hostname or "").lower()
            allowed = any(
                host == suffix or host.endswith(suffix)
                for suffix in ALLOWED_IMAGE_HOST_SUFFIXES
            )
            if request.resource_type == "image" and allowed:
                await route.continue_()  # type: ignore[attr-defined]
            else:
                await route.abort()  # type: ignore[attr-defined]

        await page.route("**/*", route_handler)
        try:
            await page.set_content(
                html,
                wait_until="domcontentloaded",
                timeout=plugin_config.sns_render_timeout,
            )
            with suppress(PlaywrightTimeoutError):
                await page.wait_for_load_state(
                    "networkidle", timeout=plugin_config.sns_render_timeout
                )
            with suppress(PlaywrightTimeoutError):
                await page.wait_for_function(
                    "Array.from(document.images).every((image) => image.complete)",
                    timeout=plugin_config.sns_render_timeout,
                )
            with suppress(PlaywrightTimeoutError):
                await page.evaluate("document.fonts.ready")
            card = page.locator(f"#{card_id}")
            await card.wait_for(
                state="visible", timeout=plugin_config.sns_render_timeout
            )
            return await card.screenshot(type="png")
        finally:
            await context.close()


CJK_FONT_HINTS = (
    "cjk",
    "wqy",
    "yahei",
    "simhei",
    "simsun",
    "msyh",
    "han",
    "zenhei",
    "uming",
    "ukai",
    "pingfang",
    "sourcehan",
    "notosanssc",
    "notoserifsc",
)


def _heuristic_cjk_font() -> bool:
    """按文件名猜测系统是否装有中文字体。"""

    candidates = (
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path.home() / ".fonts",
    )
    for root in candidates:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*"):
                if path.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                    continue
                if any(hint in path.name.lower() for hint in CJK_FONT_HINTS):
                    return True
        except OSError:
            continue
    return False


async def has_cjk_font() -> bool:
    """检测渲染环境是否带中文字体（Linux 容器常见缺失）。"""

    if sys.platform.startswith(("win", "darwin")):
        return True
    try:
        process = await asyncio.create_subprocess_exec(
            "fc-list",
            ":lang=zh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return _heuristic_cjk_font()
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except TimeoutError:
        with suppress(Exception):
            process.kill()
        return True
    if process.returncode != 0:
        return _heuristic_cjk_font()
    return bool(stdout.strip())


renderer = CardRenderer()
