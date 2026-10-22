"""推送附带的合并转发消息：图片之后再发一条，内容为本次内容的链接。

B 站 / 抖音都适用；合并转发比直接发裸链接更不容易被风控吞掉，
也便于群成员点击跳转。
"""

from __future__ import annotations

from typing import Any

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from .models import Platform, LiveAction, ContentKind, PushContent

NICKNAME_PREFIX = "【"
NICKNAME_SUFFIX = "】"

# 内容自带链接为空时的兜底模板
LINK_FALLBACKS: dict[tuple[Platform, ContentKind], str] = {
    (Platform.BILIBILI, ContentKind.LIVE): "https://live.bilibili.com/{}",
    (Platform.BILIBILI, ContentKind.VIDEO): "https://www.bilibili.com/video/{}",
    (Platform.BILIBILI, ContentKind.DYNAMIC): "https://t.bilibili.com/{}",
    (Platform.BILIBILI, ContentKind.ARTICLE): "https://t.bilibili.com/{}",
    (Platform.BILIBILI, ContentKind.MUSIC): "https://t.bilibili.com/{}",
    (Platform.DOUYIN, ContentKind.VIDEO): "https://www.douyin.com/video/{}",
    (Platform.DOUYIN, ContentKind.LIVE): "https://live.douyin.com/{}",
}

FORWARD_PLATFORMS = (Platform.BILIBILI, Platform.DOUYIN)


def normalize_url(value: object) -> str:
    url = str(value or "").strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        return f"https://{url[7:]}"
    return url


def link_url(content: PushContent) -> str:
    """本次推送的跳转链接，缺失时按平台 + 内容类型兜底。"""

    url = normalize_url(content.url)
    if url:
        return url
    template = LINK_FALLBACKS.get((content.platform, content.kind), "")
    if not template or not content.id:
        return ""
    return template.format(content.id)


def forward_nickname(content: PushContent) -> str:
    """合并转发标题里的来源：``【平台】博主名称``。"""

    label = content.platform.label
    name = (content.author_name or "").strip()
    return f"{NICKNAME_PREFIX}{label}{NICKNAME_SUFFIX}{name}"


def _forward_info(content: PushContent) -> tuple[str, str] | None:
    """返回 ``(昵称, 链接)``；下播通知等场景不发合并转发。"""

    if content.platform not in FORWARD_PLATFORMS:
        return None
    if content.kind is ContentKind.LIVE and content.live_action is LiveAction.STOP:
        return None
    url = link_url(content)
    if not url:
        return None
    return forward_nickname(content), url


def link_forward_nodes(
    content: PushContent, self_id: int
) -> list[dict[str, Any]] | None:
    """``send_group_forward_msg`` 使用的节点列表。"""

    info = _forward_info(content)
    if info is None:
        return None
    nickname, url = info
    return [
        {
            "type": "node",
            "data": {
                "user_id": str(self_id),
                "nickname": nickname,
                "content": url,
            },
        }
    ]


def link_forward_message(content: PushContent, self_id: int) -> Message | None:
    """直接放进 ``send_group_msg`` 的 node 消息（转发 API 不可用时的兜底）。"""

    info = _forward_info(content)
    if info is None:
        return None
    nickname, url = info
    node = MessageSegment.node_custom(
        int(self_id), nickname, Message([MessageSegment.text(url)])
    )
    return Message([node])
