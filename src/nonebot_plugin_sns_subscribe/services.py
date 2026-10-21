"""命令与 Web 后台共用的业务操作。"""

from __future__ import annotations

import io
import time
import asyncio
from typing import Any

import qrcode

from .models import (
    Platform,
    LiveAction,
    ContentKind,
    PushContent,
    AuthorProfile,
)
from .sources import SourceError, get_source, douyin_source, bilibili_source
from .database import repository

PLATFORM_ALIASES: dict[str, Platform] = {
    "bili": Platform.BILIBILI,
    "bilibili": Platform.BILIBILI,
    "b站": Platform.BILIBILI,
    "哔哩": Platform.BILIBILI,
    "哔哩哔哩": Platform.BILIBILI,
    "dy": Platform.DOUYIN,
    "douyin": Platform.DOUYIN,
    "抖音": Platform.DOUYIN,
}
KIND_ALIASES: dict[str, ContentKind] = {
    "dynamic": ContentKind.DYNAMIC,
    "动态": ContentKind.DYNAMIC,
    "video": ContentKind.VIDEO,
    "视频": ContentKind.VIDEO,
    "article": ContentKind.ARTICLE,
    "专栏": ContentKind.ARTICLE,
    "music": ContentKind.MUSIC,
    "音频": ContentKind.MUSIC,
    "live": ContentKind.LIVE,
    "直播": ContentKind.LIVE,
}


def parse_platform(value: str) -> Platform:
    platform = PLATFORM_ALIASES.get(value.strip().lower())
    if platform is None:
        raise ValueError("平台必须是 bilibili/bili（B站）或 douyin/dy（抖音）")
    return platform


def parse_kind(value: str) -> ContentKind:
    kind = KIND_ALIASES.get(value.strip().lower())
    if kind is None:
        raise ValueError("类型必须是 dynamic、video、live（B站还支持 article、music）")
    return kind


def mask_cookie(value: str) -> str:
    if not value:
        return "未配置"
    names = [item.split("=", 1)[0].strip() for item in value.split(";") if "=" in item]
    return "已配置：" + ", ".join(names)


def shorten_author_id(author_id: str, limit: int = 14) -> str:
    if len(author_id) <= limit:
        return author_id
    return f"{author_id[:limit]}…"


# --------------------------------------------------------------------- 订阅
async def subscribe(
    platform: Platform, target: str, group_id: int, group_name: str = ""
) -> tuple[AuthorProfile, bool]:
    profile = await get_source(platform).resolve(target)
    added = await repository.add_subscription(profile, group_id, group_name)
    return profile, added


async def unsubscribe(platform: Platform, group_id: int, author_id: str) -> bool:
    return await repository.remove_subscription(platform, group_id, author_id)


async def resolve_author(platform: Platform, target: str) -> AuthorProfile:
    return await get_source(platform).resolve(target)


# --------------------------------------------------------------------- 内容
def sample_content(platform: Platform, kind: ContentKind) -> PushContent:
    is_live = kind is ContentKind.LIVE
    title = {
        ContentKind.DYNAMIC: "示例动态标题",
        ContentKind.VIDEO: "示例视频标题",
        ContentKind.ARTICLE: "示例专栏标题",
        ContentKind.MUSIC: "示例音频标题",
        ContentKind.LIVE: "示例直播间标题",
    }[kind]
    if is_live:
        domain = "bilibili" if platform is Platform.BILIBILI else "douyin"
        url = f"https://live.{domain}.com/"
    elif platform is Platform.BILIBILI:
        url = "https://www.bilibili.com/"
    else:
        url = "https://www.douyin.com/"
    return PushContent(
        platform=platform,
        id="preview",
        author_id="0",
        kind=kind,
        title=(f"开播了：{title}" if is_live else title),
        text="这是一条用于预览模板、字体与配色的示例内容。",
        url=url,
        author_name="示例 UP 主" if platform is Platform.BILIBILI else "示例创作者",
        author_avatar="",
        cover="",
        published_at=int(time.time()) if is_live else 0,
        live_action=LiveAction.START if is_live else None,
    )


async def manual_content(
    platform: Platform, kind: ContentKind, identifier: str
) -> PushContent:
    value = identifier.strip()
    if platform is Platform.BILIBILI:
        if kind is ContentKind.DYNAMIC:
            return await bilibili_source.get_dynamic(value)
        if kind is ContentKind.VIDEO:
            return await bilibili_source.get_video(value)
        if kind is ContentKind.LIVE:
            if not value.isdigit() or int(value) <= 0:
                raise SourceError("直播间号必须是正整数")
            return await bilibili_source.get_live_by_room(value)
        raise SourceError("B 站手动推送仅支持 dynamic、video、live")

    if not value:
        raise SourceError("请填写抖音作者主页链接或 sec_user_id")
    profile = await douyin_source.resolve(value)
    if kind is ContentKind.LIVE:
        content = await douyin_source.fetch_live(profile.author_id)
        if content is None or content.live_action is not LiveAction.START:
            raise SourceError(f"{profile.name} 当前未开播")
        return content
    posts = await douyin_source.fetch_posts(profile.author_id)
    if not posts:
        raise SourceError(f"{profile.name} 暂无作品")
    return posts[0]


async def preview_content(
    platform: Platform, kind: ContentKind, identifier: str = ""
) -> PushContent:
    if not identifier.strip():
        return sample_content(platform, kind)
    return await manual_content(platform, kind, identifier)


# --------------------------------------------------------------------- 登录
def make_qr_png(url: str) -> bytes:
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image: Any = qr.make_image(fill_color="black", back_color="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


async def wait_bilibili_login(key: str, timeout: int = 180) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, message, cookie = await bilibili_source.poll_qr_login(key)
        if code == 0 and cookie:
            await repository.set_setting("bilibili_cookie", cookie)
            return cookie
        if code == 86038:
            raise TimeoutError("二维码已失效，请重新生成")
        if code not in {86101, 86090}:
            raise RuntimeError(message or f"扫码登录失败：{code}")
        await asyncio.sleep(2)
    raise TimeoutError("等待扫码超时")


async def save_cookie(platform: Platform, cookie: str) -> dict[str, object]:
    """保存 Cookie；抖音会立即注入运行中的浏览器。"""

    if platform is Platform.BILIBILI:
        await repository.set_setting("bilibili_cookie", cookie.strip())
        bilibili_source.invalidate()
        return {
            "hint": mask_cookie(cookie.strip()),
            "count": 1 if cookie.strip() else 0,
            "has_session": bool(cookie.strip()),
            "live": False,
            "names": [],
        }
    result = await douyin_source.apply_cookie(cookie)
    return {"hint": mask_cookie(cookie.strip()), **result}
