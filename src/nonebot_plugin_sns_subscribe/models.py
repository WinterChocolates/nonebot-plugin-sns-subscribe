"""平台无关的内容模型。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any
from datetime import datetime, timezone, timedelta
from dataclasses import field, dataclass

CHINA_TIMEZONE = timezone(timedelta(hours=8))
_HASHTAG_PATTERN = re.compile(r"#[^\s#]+")


class Platform(str, Enum):
    """支持的订阅平台。"""

    BILIBILI = "bilibili"
    DOUYIN = "douyin"

    @property
    def label(self) -> str:
        return "B站" if self is Platform.BILIBILI else "抖音"

    @property
    def accent(self) -> str:
        return "#fb7299" if self is Platform.BILIBILI else "#fe2c55"

    @property
    def gradient(self) -> str:
        return "#a78bfa" if self is Platform.BILIBILI else "#20d5ec"


class ContentKind(str, Enum):
    DYNAMIC = "dynamic"
    VIDEO = "video"
    ARTICLE = "article"
    MUSIC = "music"
    LIVE = "live"

    @property
    def label(self) -> str:
        return {
            ContentKind.DYNAMIC: "动态",
            ContentKind.VIDEO: "视频",
            ContentKind.ARTICLE: "专栏",
            ContentKind.MUSIC: "音频",
            ContentKind.LIVE: "直播",
        }[self]


class LiveAction(str, Enum):
    START = "start"
    STOP = "stop"


class AtAllKind(str, Enum):
    ALL = "all"
    DYNAMIC = "dynamic"
    VIDEO = "video"
    ARTICLE = "article"
    MUSIC = "music"
    LIVE = "live"


@dataclass(slots=True, frozen=True)
class RichTextSegment:
    text: str
    emoji_url: str = ""


@dataclass(slots=True)
class PushContent:
    """一次待推送的内容（动态/视频/直播…）。"""

    platform: Platform
    id: str
    author_id: str
    kind: ContentKind
    title: str
    url: str
    author_name: str
    text: str = ""
    author_avatar: str = ""
    cover: str = ""
    images: list[str] = field(default_factory=list)
    image_dimensions: list[tuple[int, int]] = field(default_factory=list)
    rich_text: list[RichTextSegment] = field(default_factory=list)
    published_at: int = 0
    live_action: LiveAction | None = None
    forward: PushContent | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        action = self.live_action.value if self.live_action else "publish"
        return f"{self.kind.value}:{action}:{self.id}"

    @property
    def namespace(self) -> str:
        return "live" if self.kind is ContentKind.LIVE else "post"

    @property
    def filter_text(self) -> str:
        parts = [part for part in (self.title, self.text, self.author_name) if part]
        if self.forward is not None:
            parts.append(self.forward.filter_text)
        return "\n".join(parts)

    @property
    def published_text(self) -> str:
        if not self.published_at:
            return ""
        return datetime.fromtimestamp(self.published_at, CHINA_TIMEZONE).strftime(
            "%Y-%m-%d %H:%M"
        )


@dataclass(slots=True, frozen=True)
class AuthorProfile:
    """订阅作者的基础资料。"""

    platform: Platform
    author_id: str
    name: str
    avatar: str = ""
    url: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(slots=True)
class Subscription:
    platform: Platform
    group_id: int
    author_id: str
    author_name: str = ""
    avatar: str = ""
    group_name: str = ""
    group_avatar: str = ""

    @property
    def display_name(self) -> str:
        return self.author_name or self.author_id


@dataclass(slots=True, frozen=True)
class RenderSelection:
    template_path: str | None = None
    font_path: str | None = None
    accent_color: str | None = None
    gradient_color: str | None = None


@dataclass(slots=True)
class Snapshot:
    """数据源返回的一次账号快照。"""

    profile: AuthorProfile
    posts: list[PushContent] = field(default_factory=list)
    live: PushContent | None = None


def clean_title(value: str, fallback: str = "发布了新内容") -> str:
    """去掉抖音话题标签，保留可读标题。"""

    cleaned = " ".join(_HASHTAG_PATTERN.sub("", value or "").split())
    return cleaned or fallback
