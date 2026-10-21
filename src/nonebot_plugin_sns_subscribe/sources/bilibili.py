"""B 站数据源：基于 httpx 的官方 Web 接口。

接口与字段解析参考 ``nonebot-plugin-bilibili``。
"""

from __future__ import annotations

import re
import asyncio
from typing import Any
from urllib.parse import urlparse, unquote_plus

import httpx

from .base import SourceError, NoLiveRoomError
from ..config import plugin_config
from ..models import (
    CHINA_TIMEZONE,
    Platform,
    LiveAction,
    ContentKind,
    PushContent,
    AuthorProfile,
    RichTextSegment,
)
from ..database import repository

# 动态类型 -> 内容类型
DYNAMIC_KIND_MAP = {
    "DYNAMIC_TYPE_AV": ContentKind.VIDEO,
    "DYNAMIC_TYPE_MUSIC": ContentKind.MUSIC,
    "DYNAMIC_TYPE_ARTICLE": ContentKind.ARTICLE,
}
CHINA_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
REQUEST_ATTEMPTS = 3
REQUEST_RETRY_DELAY = 0.5
UID_IN_URL = re.compile(r"(?:space\.bilibili\.com/|^)(\d{1,15})")


class BilibiliSource:
    """B 站 Web 接口封装。"""

    platform = Platform.BILIBILI

    API = "https://api.bilibili.com"
    LIVE_API = "https://api.live.bilibili.com"
    PASSPORT_API = "https://passport.bilibili.com"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    # ----------------------------------------------------------------- client
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=plugin_config.sns_request_timeout,
                        follow_redirects=True,
                        headers={
                            "User-Agent": plugin_config.sns_user_agent,
                            "Referer": "https://www.bilibili.com/",
                        },
                    )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def invalidate(self) -> None:
        """Cookie 每次请求都会重新读取，这里保留为显式失效钩子。"""

    async def cookie_string(self) -> str:
        value = await repository.get_setting("bilibili_cookie")
        if value:
            return value
        configured = plugin_config.sns_bilibili_cookie
        return configured.get_secret_value().strip() if configured else ""

    async def _cookies(self) -> dict[str, str]:
        raw = await self.cookie_string()
        result: dict[str, str] = {}
        for item in raw.split(";"):
            if "=" not in item:
                continue
            key, value = item.strip().split("=", 1)
            if key:
                result[key] = value
        return result

    async def has_cookie(self) -> bool:
        return bool(await self.cookie_string())

    # ------------------------------------------------------------------ http
    async def _request_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        use_cookie: bool = True,
    ) -> tuple[dict[str, Any], httpx.Response]:
        client = await self._get_client()
        cookies = await self._cookies() if use_cookie else None
        response: httpx.Response | None = None
        for attempt in range(REQUEST_ATTEMPTS):
            try:
                response = await client.get(url, params=params, cookies=cookies)
                break
            except httpx.TransportError as exc:
                if attempt == REQUEST_ATTEMPTS - 1:
                    raise SourceError(
                        f"连接 B 站失败，已重试 {REQUEST_ATTEMPTS} 次：{exc}"
                    ) from exc
                await asyncio.sleep(REQUEST_RETRY_DELAY * (2**attempt))
        if response is None:  # pragma: no cover - 循环内要么成功要么抛出
            raise SourceError("连接 B 站失败")
        if response.status_code == 412:
            raise SourceError("B 站风控拒绝了请求，请先在 Web 后台完成扫码登录")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SourceError(f"B 站接口返回 HTTP {response.status_code}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise SourceError("B 站接口返回了无法解析的数据") from exc
        if not isinstance(payload, dict):
            raise SourceError("B 站接口返回了无法识别的数据")
        code = int(payload.get("code", 0))
        if code != 0:
            message = payload.get("message") or payload.get("msg") or "未知错误"
            raise SourceError(f"B 站接口错误 {code}: {message}")
        return payload, response

    async def _get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        use_cookie: bool = True,
    ) -> dict[str, Any]:
        payload, _ = await self._request_json(url, params, use_cookie=use_cookie)
        return payload

    # --------------------------------------------------------------- profile
    async def get_profile(self, uid: str) -> AuthorProfile:
        payload = await self._get_json(
            f"{self.API}/x/web-interface/card",
            {"mid": int(uid), "photo": "true"},
        )
        card = (payload.get("data") or {}).get("card") or {}
        name = str(card.get("name") or uid)
        return AuthorProfile(
            platform=self.platform,
            author_id=str(uid),
            name=name,
            avatar=self._normalize_image_url(card.get("face")),
            url=f"https://space.bilibili.com/{uid}",
        )

    async def resolve(self, target: str) -> AuthorProfile:
        match = UID_IN_URL.search(target.strip())
        if match is None:
            raise SourceError(
                "请提供 UID（纯数字）或 https://space.bilibili.com/<uid> 链接"
            )
        return await self.get_profile(match.group(1))

    # -------------------------------------------------------------- dynamics
    async def fetch_posts(self, author_id: str) -> list[PushContent]:
        if not await self.has_cookie():
            raise SourceError("尚未配置 B 站 Cookie，动态轮询已跳过")
        payload = await self._get_json(
            f"{self.API}/x/polymer/web-dynamic/v1/feed/space",
            {
                "host_mid": int(author_id),
                "offset": "",
                "timezone_offset": -480,
                "features": "itemOpusStyle",
            },
        )
        items = (payload.get("data") or {}).get("items") or []
        return [
            self._parse_dynamic(item, author_id)
            for item in items
            if isinstance(item, dict) and item.get("id_str")
        ]

    async def get_dynamic(self, dynamic_id: str) -> PushContent:
        payload = await self._get_json(
            f"{self.API}/x/polymer/web-dynamic/v1/detail",
            {
                "id": dynamic_id,
                "timezone_offset": -480,
                "features": "itemOpusStyle",
            },
        )
        item = (payload.get("data") or {}).get("item")
        if not item:
            raise SourceError("未找到该动态")
        author = (item.get("modules") or {}).get("module_author") or {}
        return self._parse_dynamic(item, str(author.get("mid") or "0"))

    def _parse_dynamic(self, item: dict[str, Any], author_id: str) -> PushContent:
        modules = item.get("modules") or {}
        author = modules.get("module_author") or {}
        dynamic = modules.get("module_dynamic") or {}
        major = dynamic.get("major") or {}
        desc = dynamic.get("desc") or {}
        dynamic_type = str(item.get("type") or "")
        kind = DYNAMIC_KIND_MAP.get(dynamic_type, ContentKind.DYNAMIC)

        title = ""
        cover = ""
        images: list[str] = []
        dimensions: list[tuple[int, int]] = []
        jump_url = ""
        opus_text = ""
        rich_text: list[RichTextSegment] = []
        for key in ("archive", "article", "music", "opus", "draw", "common"):
            data = major.get(key)
            if not isinstance(data, dict):
                continue
            title = str(data.get("title") or data.get("desc") or title)
            jump_url = str(data.get("jump_url") or jump_url)
            images, dimensions = self._image_assets(data)
            cover = (images[0] if images else self._first_cover(data)) or cover
            if key == "opus":
                summary = data.get("summary") or {}
                if isinstance(summary, dict):
                    opus_text, rich_text = self._parse_rich_text(summary)
            break

        dynamic_id = str(item.get("id_str"))
        text = str(desc.get("text") or "") if isinstance(desc, dict) else ""
        if not rich_text and isinstance(desc, dict):
            desc_text, rich_text = self._parse_rich_text(desc)
            text = desc_text or text
        text = opus_text or text
        if not text:
            text = str((major.get("article") or {}).get("desc") or title)
        if jump_url.startswith("//"):
            jump_url = f"https:{jump_url}"
        url = jump_url or f"https://t.bilibili.com/{dynamic_id}"

        forward: PushContent | None = None
        original = item.get("orig")
        if dynamic_type == "DYNAMIC_TYPE_FORWARD" and isinstance(original, dict):
            original_author = (original.get("modules") or {}).get("module_author") or {}
            forward = self._parse_dynamic(
                original, str(original_author.get("mid") or "0")
            )

        return PushContent(
            platform=self.platform,
            id=dynamic_id,
            author_id=str(author.get("mid") or author_id),
            kind=kind,
            title=title or self._kind_title(kind),
            text=text,
            url=url,
            author_name=str(author.get("name") or author_id),
            author_avatar=self._normalize_image_url(author.get("face")),
            cover=cover,
            images=images,
            image_dimensions=dimensions,
            rich_text=rich_text,
            published_at=int(author.get("pub_ts") or 0),
            forward=forward,
            raw=item,
        )

    @classmethod
    def _parse_rich_text(
        cls, container: dict[str, Any]
    ) -> tuple[str, list[RichTextSegment]]:
        nodes = container.get("rich_text_nodes") or []
        if not isinstance(nodes, list):
            nodes = []
        segments: list[RichTextSegment] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_text = str(node.get("orig_text") or node.get("text") or "")
            emoji = node.get("emoji") or {}
            emoji_url = ""
            if isinstance(emoji, dict):
                emoji_url = cls._normalize_image_url(
                    emoji.get("icon_url") or emoji.get("url")
                )
            if node_text or emoji_url:
                segments.append(RichTextSegment(text=node_text, emoji_url=emoji_url))
        plain = "".join(segment.text for segment in segments)
        return plain or str(container.get("text") or ""), segments

    @classmethod
    def _first_cover(cls, data: dict[str, Any]) -> str:
        cover = data.get("cover")
        if isinstance(cover, str):
            return cls._normalize_image_url(cover)
        covers = data.get("covers")
        if isinstance(covers, list) and covers:
            first = covers[0]
            value = first.get("url") if isinstance(first, dict) else first
            return cls._normalize_image_url(value)
        return ""

    @classmethod
    def _image_assets(
        cls, data: dict[str, Any]
    ) -> tuple[list[str], list[tuple[int, int]]]:
        candidates = data.get("pics") or data.get("items") or []
        if not isinstance(candidates, list):
            return [], []
        urls: list[str] = []
        dimensions: list[tuple[int, int]] = []
        for item in candidates:
            value = item
            width = 0
            height = 0
            if isinstance(item, dict):
                value = item.get("url") or item.get("src") or item.get("cover")
                try:
                    width = max(0, int(item.get("width") or 0))
                    height = max(0, int(item.get("height") or 0))
                except (TypeError, ValueError):
                    width = height = 0
            url = cls._normalize_image_url(value)
            if url and url not in urls:
                urls.append(url)
                dimensions.append((width, height))
        return urls, dimensions

    @staticmethod
    def _normalize_image_url(value: object) -> str:
        url = str(value or "").strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("http://"):
            return f"https://{url[7:]}"
        return url

    @staticmethod
    def _kind_title(kind: ContentKind) -> str:
        return {
            ContentKind.DYNAMIC: "发布了新动态",
            ContentKind.VIDEO: "投稿了新视频",
            ContentKind.MUSIC: "发布了新音乐",
            ContentKind.ARTICLE: "发布了新专栏",
            ContentKind.LIVE: "直播通知",
        }[kind]

    # ------------------------------------------------------------------ video
    async def get_video(self, identifier: str) -> PushContent:
        query: dict[str, Any]
        value = identifier.strip()
        if value.upper().startswith("BV"):
            query = {"bvid": value}
        elif value.isdigit():
            query = {"aid": int(value)}
        else:
            raise SourceError("视频号必须是 AV 数字号或 BV 号")
        payload = await self._get_json(f"{self.API}/x/web-interface/view", query)
        data = payload.get("data") or {}
        owner = data.get("owner") or {}
        bvid = str(data.get("bvid") or value)
        return PushContent(
            platform=self.platform,
            id=bvid,
            author_id=str(owner.get("mid") or "0"),
            kind=ContentKind.VIDEO,
            title=str(data.get("title") or "视频投稿"),
            text=str(data.get("desc") or ""),
            url=f"https://www.bilibili.com/video/{bvid}",
            author_name=str(owner.get("name") or "未知 UP 主"),
            author_avatar=self._normalize_image_url(owner.get("face")),
            cover=self._normalize_image_url(data.get("pic")),
            published_at=int(data.get("pubdate") or 0),
            raw=data,
        )

    # ------------------------------------------------------------------- live
    async def fetch_live(self, author_id: str) -> PushContent | None:
        try:
            return await self.get_live_by_uid(author_id)
        except NoLiveRoomError:
            return None

    async def get_live_by_uid(
        self, uid: str, action: LiveAction | None = None
    ) -> PushContent:
        payload = await self._get_json(
            f"{self.LIVE_API}/room/v1/Room/getRoomInfoOld", {"mid": int(uid)}
        )
        room = payload.get("data") or {}
        room_id = int(room.get("roomid") or 0)
        if not room_id:
            raise NoLiveRoomError("该用户没有直播间")
        return await self.get_live_by_room(
            str(room_id), action=action, fallback_uid=uid
        )

    async def get_live_by_room(
        self,
        room_id: str,
        action: LiveAction | None = None,
        fallback_uid: str = "0",
    ) -> PushContent:
        payload = await self._get_json(
            f"{self.LIVE_API}/room/v1/Room/get_info",
            {"room_id": int(room_id)},
        )
        room_info = payload.get("data") or {}
        uid = str(room_info.get("uid") or fallback_uid)
        try:
            profile = await self.get_profile(uid)
        except SourceError:
            profile = AuthorProfile(
                platform=self.platform,
                author_id=uid,
                name=uid,
                url=f"https://space.bilibili.com/{uid}",
            )
        actual_action = action
        if actual_action is None:
            actual_action = (
                LiveAction.START
                if int(room_info.get("live_status") or 0) == 1
                else LiveAction.STOP
            )
        title = str(room_info.get("title") or "直播间")
        cover = str(
            room_info.get("user_cover")
            or room_info.get("cover")
            or room_info.get("keyframe")
            or ""
        )
        return PushContent(
            platform=self.platform,
            id=str(room_info.get("room_id") or room_id),
            author_id=uid,
            kind=ContentKind.LIVE,
            title=("开播了：" if actual_action is LiveAction.START else "直播结束：")
            + title,
            text=str(
                room_info.get("area_name") or room_info.get("parent_area_name") or ""
            ),
            url=f"https://live.bilibili.com/{room_info.get('room_id') or room_id}",
            author_name=profile.name,
            author_avatar=profile.avatar,
            cover=self._normalize_image_url(cover),
            published_at=self._parse_live_time(room_info.get("live_time")),
            live_action=actual_action,
            raw=room_info,
        )

    @staticmethod
    def _parse_live_time(value: object) -> int:
        if isinstance(value, (int, float)):
            return max(0, int(value))
        text = str(value or "").strip()
        if not text or text.startswith("0000-00-00"):
            return 0
        if text.isdigit():
            return int(text)
        try:
            from datetime import datetime

            # strptime 不支持时区参数，下一行显式补齐东八区
            parsed = datetime.strptime(text, CHINA_TIME_FORMAT)
        except ValueError:
            return 0
        return int(parsed.replace(tzinfo=CHINA_TIMEZONE).timestamp())

    # ------------------------------------------------------------------ login
    async def create_qr_login(self) -> tuple[str, str]:
        payload = await self._get_json(
            f"{self.PASSPORT_API}/x/passport-login/web/qrcode/generate",
            use_cookie=False,
        )
        data = payload.get("data") or {}
        return str(data.get("qrcode_key") or ""), str(data.get("url") or "")

    async def poll_qr_login(self, key: str) -> tuple[int, str, str]:
        payload, response = await self._request_json(
            f"{self.PASSPORT_API}/x/passport-login/web/qrcode/poll",
            {"qrcode_key": key},
            use_cookie=False,
        )
        data = payload.get("data") or {}
        code = int(data.get("code", -1))
        message = str(data.get("message") or "")
        cookie = ""
        if code == 0:
            cookie = self._login_cookie(str(data.get("url") or ""), response)
        return code, message, cookie

    @staticmethod
    def _login_cookie(callback_url: str, response: httpx.Response) -> str:
        allowed = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")
        values: dict[str, str] = {}
        parsed = urlparse(callback_url)
        for source in (parsed.query, parsed.fragment):
            for item in source.split("&"):
                if "=" not in item:
                    continue
                raw_key, raw_value = item.split("=", 1)
                name = unquote_plus(raw_key)
                if name in allowed:
                    values[name] = (
                        raw_value if name == "SESSDATA" else unquote_plus(raw_value)
                    )
        for name in allowed:
            response_value = response.cookies.get(name)
            if response_value:
                values[name] = response_value
        cookie_pattern = re.compile(
            rf"(?:^|[,;]\s*)({'|'.join(map(re.escape, allowed))})=([^;,\s]+)"
        )
        for header in response.headers.get_list("set-cookie"):
            for name, response_value in cookie_pattern.findall(header):
                values[name] = response_value  # 需合并多来源 Cookie
        return "; ".join(f"{name}={values[name]}" for name in allowed if name in values)


bilibili_source = BilibiliSource()
