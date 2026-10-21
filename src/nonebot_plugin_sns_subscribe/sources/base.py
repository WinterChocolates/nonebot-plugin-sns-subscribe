"""数据源协议。"""

from __future__ import annotations

from typing import Protocol

from ..models import Platform, PushContent, AuthorProfile


class SourceError(RuntimeError):
    """数据源不可用或返回异常数据。"""


class NoLiveRoomError(SourceError):
    """该作者没有直播间。"""


class AuthorSource(Protocol):
    """一个平台的数据源实现。"""

    platform: Platform

    async def resolve(self, target: str) -> AuthorProfile:
        """把用户输入（UID、主页链接等）解析成作者资料。"""
        ...

    async def fetch_posts(self, author_id: str) -> list[PushContent]:
        """拉取该作者最新的投稿/动态，按时间倒序。"""
        ...

    async def fetch_live(self, author_id: str) -> PushContent | None:
        """查询直播状态；没有直播间时返回 ``None``。"""
        ...

    async def close(self) -> None: ...
