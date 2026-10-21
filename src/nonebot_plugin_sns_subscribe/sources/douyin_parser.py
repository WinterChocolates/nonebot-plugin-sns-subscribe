"""抖音接口响应解析（字段兼容 web / webapp 两种命名）。"""

from __future__ import annotations

import json
from typing import Any
from dataclasses import field, dataclass
from collections.abc import Mapping


class DouyinParseError(RuntimeError):
    """响应结构不符合预期。"""


@dataclass(slots=True, frozen=True)
class DouyinVideo:
    aweme_id: str
    description: str
    create_time: int
    cover_url: str = ""
    is_pinned: bool = False


@dataclass(slots=True, frozen=True)
class DouyinLive:
    is_live: bool = False
    room_id: str = "0"
    web_rid: str = ""
    title: str = ""
    cover_url: str = ""


@dataclass(slots=True, frozen=True)
class DouyinSnapshot:
    sec_user_id: str
    nickname: str
    avatar_url: str = ""
    web_rid: str = ""
    videos: list[DouyinVideo] = field(default_factory=list)
    live: DouyinLive = field(default_factory=DouyinLive)
    raw_profile: Mapping[str, Any] = field(default_factory=dict, repr=False)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _value(data: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = data.get(name)
        if value is not None:
            return value
    return default


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _url(value: Any) -> str:
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://")) else ""
    data = _mapping(value)
    urls = _value(data, "url_list", "urlList", default=[])
    if isinstance(urls, list):
        return next((item for item in urls if isinstance(item, str) and item), "")
    return ""


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return _mapping(parsed)


def _room_from_user(user: Mapping[str, Any]) -> Mapping[str, Any]:
    room_data = _json_mapping(_value(user, "room_data", "roomData", default={}))
    nested = _mapping(_value(room_data, "room", "data", default={}))
    return nested or room_data


def parse_videos(
    payload: Mapping[str, Any], *, expected_sec_user_id: str = ""
) -> list[DouyinVideo]:
    raw_items = _value(payload, "aweme_list", "awemeList", default=[])
    if not isinstance(raw_items, list):
        return []
    items: list[DouyinVideo] = []
    for raw in raw_items:
        aweme = _mapping(raw)
        aweme_id = _string(_value(aweme, "aweme_id", "awemeId"))
        if not aweme_id:
            continue
        author = _mapping(aweme.get("author"))
        author_sec_user_id = _string(_value(author, "sec_uid", "secUid"))
        if (
            expected_sec_user_id
            and author_sec_user_id
            and author_sec_user_id != expected_sec_user_id
        ):
            continue
        video = _mapping(aweme.get("video"))
        cover = _value(video, "cover", "origin_cover", "originCover", default={})
        items.append(
            DouyinVideo(
                aweme_id=aweme_id,
                description=_string(_value(aweme, "desc", "description")),
                create_time=_integer(_value(aweme, "create_time", "createTime")),
                cover_url=_url(cover),
                is_pinned=bool(_value(aweme, "is_top", "isTop", default=False)),
            )
        )
    return sorted(
        items, key=lambda item: (item.create_time, item.aweme_id), reverse=True
    )


def parse_profile(
    payload: Mapping[str, Any],
    *,
    fallback_sec_user_id: str = "",
    fallback_web_rid: str = "",
) -> tuple[str, str, str, DouyinLive, Mapping[str, Any]]:
    container = _mapping(payload.get("data")) or payload
    user = _mapping(container.get("user"))
    if not user and any(key in container for key in ("sec_uid", "secUid", "nickname")):
        user = container
    if not user:
        raise DouyinParseError("抖音用户资料响应中没有 user 字段")

    sec_user_id = _string(_value(user, "sec_uid", "secUid")) or fallback_sec_user_id
    nickname = _string(_value(user, "nickname", "real_name", "realName"))
    avatar = _url(
        _value(
            user,
            "avatar_300x300",
            "avatar300Url",
            "avatar_thumb",
            "avatarThumb",
            default={},
        )
    ) or _string(_value(user, "avatarUrl", default=""))

    room = _room_from_user(user)
    room_id = _string(
        _value(user, "room_id", "roomId", "room_id_str", "roomIdStr", default="0")
    )
    if room_id in {"", "0", "None"}:
        room_id = _string(
            _value(room, "id_str", "idStr", "room_id", "roomId", default="0")
        )
    web_rid = _string(_value(user, "web_rid", "webRid")) or _string(
        _value(room, "web_rid", "webRid")
    )
    if not web_rid:
        web_rid = _string(_value(_mapping(room.get("owner")), "web_rid", "webRid"))
    web_rid = web_rid or fallback_web_rid

    status = _value(room, "status", "live_status", "liveStatus")
    has_room = room_id not in {"", "0", "None"}
    is_live = has_room and (status is None or _integer(status) in {1, 2})
    cover = _url(
        _value(room, "cover", "room_cover", "roomCover", "background", default={})
    )
    live = DouyinLive(
        is_live=is_live,
        room_id=room_id if is_live else "0",
        web_rid=web_rid,
        title=_string(_value(room, "title", default="")),
        cover_url=cover,
    )
    return sec_user_id, nickname, avatar, live, dict(user)


def parse_snapshot(
    profile_payload: Mapping[str, Any],
    post_payload: Mapping[str, Any],
    *,
    fallback_sec_user_id: str,
    fallback_web_rid: str = "",
) -> DouyinSnapshot:
    sec_user_id, nickname, avatar, live, raw_user = parse_profile(
        profile_payload,
        fallback_sec_user_id=fallback_sec_user_id,
        fallback_web_rid=fallback_web_rid,
    )
    if not sec_user_id:
        raise DouyinParseError("无法从抖音响应中识别 sec_user_id")
    return DouyinSnapshot(
        sec_user_id=sec_user_id,
        nickname=nickname or sec_user_id,
        avatar_url=avatar,
        web_rid=live.web_rid or fallback_web_rid,
        videos=parse_videos(post_payload, expected_sec_user_id=sec_user_id),
        live=live,
        raw_profile=raw_user,
    )
