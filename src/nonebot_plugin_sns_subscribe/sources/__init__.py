"""平台数据源集合。"""

from __future__ import annotations

from .base import SourceError, AuthorSource, NoLiveRoomError
from .douyin import DouyinSource, DouyinLoginState, DouyinLoginSession, douyin_source
from ..models import Platform
from .bilibili import BilibiliSource, bilibili_source

__all__ = [
    "AuthorSource",
    "BilibiliSource",
    "DouyinLoginSession",
    "DouyinLoginState",
    "DouyinSource",
    "NoLiveRoomError",
    "SourceError",
    "bilibili_source",
    "douyin_source",
    "get_source",
]


def get_source(platform: Platform) -> AuthorSource:
    if platform is Platform.BILIBILI:
        return bilibili_source
    return douyin_source
