"""轮询任务：B 站动态 / B 站直播 / 抖音作品与直播。"""

from __future__ import annotations

import random
import asyncio
from collections.abc import Callable, Awaitable

from nonebot import logger

from .config import plugin_config
from .models import Platform, LiveAction, PushContent
from .sources import SourceError, douyin_source, bilibili_source
from .database import repository
from .pipeline import dispatch_content

IGNORED_DYNAMIC_TYPES = {"DYNAMIC_TYPE_LIVE_RCMD"}
_runtime_locks: dict[str, asyncio.Lock] = {}
_tasks: dict[str, asyncio.Task[None]] = {}
_initialized: set[tuple[str, str, str]] = set()


def _lock(name: str) -> asyncio.Lock:
    return _runtime_locks.setdefault(name, asyncio.Lock())


def _is_initialized(namespace: str, platform: Platform, author_id: str) -> bool:
    return (namespace, platform.value, author_id) in _initialized


def _mark_initialized(namespace: str, platform: Platform, author_id: str) -> None:
    _initialized.add((namespace, platform.value, author_id))


def _ordered(contents: list[PushContent]) -> list[PushContent]:
    def sort_key(item: PushContent) -> tuple[int, int]:
        numeric = int(item.id) if item.id.isdigit() else 0
        return item.published_at, numeric

    # 空间动态列表可能把置顶旧动态排在最前，也可能重复返回同一条。
    ordered = sorted(contents, key=sort_key, reverse=True)
    seen: set[str] = set()
    unique: list[PushContent] = []
    for item in ordered:
        if item.id in seen:
            continue
        seen.add(item.id)
        unique.append(item)
    return unique


def _is_ignored_dynamic(content: PushContent) -> bool:
    return str(content.raw.get("type") or "") in IGNORED_DYNAMIC_TYPES


# --------------------------------------------------------------------- B 站动态
async def poll_bilibili_dynamics() -> None:
    if _lock("bilibili_dynamic").locked():
        return
    if not await bilibili_source.has_cookie():
        return
    async with _lock("bilibili_dynamic"):
        authors = await repository.list_tracked(Platform.BILIBILI)
        semaphore = asyncio.Semaphore(plugin_config.sns_request_concurrency)

        async def check(author_id: str) -> None:
            async with semaphore:
                platform = Platform.BILIBILI
                previous: str | None = None
                try:
                    previous = await repository.get_state(
                        platform, "dynamic", author_id
                    )
                    items = await bilibili_source.fetch_posts(author_id)
                    if not items:
                        if previous is None:
                            await repository.set_state(
                                platform, "dynamic", author_id, ""
                            )
                        _mark_initialized("dynamic", platform, author_id)
                        return
                    items = _ordered(items)
                    newest = items[0].id
                    first_check = not _is_initialized("dynamic", platform, author_id)
                    _mark_initialized("dynamic", platform, author_id)
                    if first_check or previous is None:
                        await repository.set_state(
                            platform, "dynamic", author_id, newest
                        )
                        return
                    if previous == "":
                        pending = []
                        for item in items:
                            pending.append(item)
                            if not _is_ignored_dynamic(item):
                                break
                    else:
                        pending = []
                        found = False
                        for item in items:
                            if item.id == previous:
                                found = True
                                break
                            pending.append(item)
                        if not found:
                            logger.warning(
                                "SNS subscription: B 站动态基线 {} 已不在列表中，"
                                "重置为 {} 且不补推历史动态",
                                previous,
                                newest,
                            )
                            await repository.set_state(
                                platform, "dynamic", author_id, newest
                            )
                            return
                    pending = pending[: plugin_config.sns_initial_post_limit]
                    for item in reversed(pending):
                        if _is_ignored_dynamic(item):
                            await repository.set_state(
                                platform, "dynamic", author_id, item.id
                            )
                            continue
                        if not await dispatch_content(item):
                            logger.warning(
                                "SNS subscription: 动态 {} 未完全投递，下轮重试",
                                item.id,
                            )
                            return
                        await repository.set_state(
                            platform, "dynamic", author_id, item.id
                        )
                except SourceError as exc:
                    if previous is None:
                        await repository.set_state(platform, "dynamic", author_id, "")
                    logger.warning(
                        "SNS subscription: B 站动态轮询失败（{}）：{}", author_id, exc
                    )
                except Exception:  # 单个作者失败不影响其它作者
                    logger.exception(
                        "SNS subscription: B 站动态轮询异常（{}）", author_id
                    )

        await asyncio.gather(*(check(item.author_id) for item in authors))


# --------------------------------------------------------------------- B 站直播
async def poll_bilibili_lives() -> None:
    if _lock("bilibili_live").locked():
        return
    async with _lock("bilibili_live"):
        authors = await repository.list_tracked(Platform.BILIBILI)
        semaphore = asyncio.Semaphore(plugin_config.sns_request_concurrency)

        async def check(author_id: str) -> None:
            async with semaphore:
                platform = Platform.BILIBILI
                try:
                    content = await bilibili_source.fetch_live(author_id)
                    if content is None:
                        return
                    current = content.live_action or LiveAction.STOP
                    previous = await repository.get_state(platform, "live", author_id)
                    if previous is None:
                        # 首次订阅：正在直播则补推开播，否则只记录基线
                        if current is LiveAction.START:
                            if await dispatch_content(content):
                                await repository.set_state(
                                    platform, "live", author_id, current.value
                                )
                        else:
                            await repository.set_state(
                                platform, "live", author_id, current.value
                            )
                        return
                    if previous == current.value:
                        return
                    if (
                        current is LiveAction.STOP
                        and not plugin_config.sns_notify_live_end
                    ):
                        await repository.set_state(
                            platform, "live", author_id, current.value
                        )
                        return
                    if await dispatch_content(content):
                        await repository.set_state(
                            platform, "live", author_id, current.value
                        )
                    else:
                        logger.warning(
                            "SNS subscription: 直播状态 {} 未完全投递，下轮重试",
                            author_id,
                        )
                except SourceError as exc:
                    logger.warning(
                        "SNS subscription: B 站直播轮询失败（{}）：{}", author_id, exc
                    )
                except Exception:
                    logger.exception(
                        "SNS subscription: B 站直播轮询异常（{}）", author_id
                    )

        await asyncio.gather(*(check(item.author_id) for item in authors))


# --------------------------------------------------------------------- 抖音
def _pending_videos(
    contents: list[PushContent], previous_id: str
) -> list[PushContent] | None:
    """返回需要推送的旧→新列表；``None`` 表示基线已失效。"""

    if not contents:
        return []
    if not previous_id:
        return []
    pending: list[PushContent] = []
    for item in contents:
        if item.id == previous_id:
            return list(reversed(pending))
        pending.append(item)
    return None


async def poll_douyin() -> None:
    if _lock("douyin").locked():
        return
    async with _lock("douyin"):
        platform = Platform.DOUYIN
        authors = await repository.list_tracked(platform)
        delay = plugin_config.sns_douyin_account_delay
        for index, author in enumerate(authors):
            try:
                await _check_douyin_author(author.author_id)
            except SourceError as exc:
                logger.warning(
                    "SNS subscription: 抖音轮询失败（{}）：{}", author.author_id, exc
                )
            except Exception:  # 单作者异常不应中断整轮
                logger.exception(
                    "SNS subscription: 抖音轮询异常（{}）", author.author_id
                )
            if index + 1 < len(authors) and delay > 0:
                await asyncio.sleep(random.uniform(delay * 0.8, delay * 1.2))


async def _check_douyin_author(author_id: str) -> None:
    platform = Platform.DOUYIN
    snapshot = await douyin_source.fetch_snapshot(author_id)
    await repository.refresh_subscription_profile(snapshot.profile)

    # ---- 作品 ----
    previous_video = await repository.get_state(platform, "video", author_id)
    contents = snapshot.posts
    if previous_video is None:
        # 首次订阅：只记录基线，不补推历史作品
        await repository.set_state(
            platform,
            "video",
            author_id,
            contents[0].id if contents else "",
        )
    else:
        pending = _pending_videos(contents, previous_video)
        if pending is None:
            newest = contents[0].id if contents else ""
            logger.warning(
                "SNS subscription: 抖音作品基线 {} 已不在列表中，"
                "重置为 {} 且不补推历史作品",
                previous_video,
                newest,
            )
            await repository.set_state(platform, "video", author_id, newest)
        else:
            for content in pending[: plugin_config.sns_initial_post_limit]:
                if not await dispatch_content(content):
                    logger.warning(
                        "SNS subscription: 抖音作品 {} 未完全投递，下轮重试",
                        content.id,
                    )
                    break
                await repository.set_state(platform, "video", author_id, content.id)

    # ---- 直播 ----
    if snapshot.live is None:
        return
    previous_live = await repository.get_json_state(platform, "live", author_id)
    live = snapshot.live
    is_live = live.live_action is LiveAction.START
    current = {"live": is_live, "room": live.id}
    if previous_live is None:
        # 首次订阅：正在直播则补推开播，否则只记录基线
        if (is_live and await dispatch_content(live)) or not is_live:
            await repository.set_json_state(platform, "live", author_id, current)
        return
    was_live = bool(previous_live.get("live"))
    same_room = str(previous_live.get("room") or "") == live.id
    if is_live and (not was_live or not same_room):
        if await dispatch_content(live):
            await repository.set_json_state(platform, "live", author_id, current)
        return
    if not is_live and was_live and plugin_config.sns_notify_live_end:
        if await dispatch_content(live):
            await repository.set_json_state(platform, "live", author_id, current)
        return
    await repository.set_json_state(platform, "live", author_id, current)


# --------------------------------------------------------------------- 调度器
async def _loop(
    name: str,
    func: Callable[[], Awaitable[None]],
    interval_attr: str,
    enabled_attr: str,
    *,
    initial_delay: float,
) -> None:
    await asyncio.sleep(initial_delay)
    while True:
        interval = max(1, int(getattr(plugin_config, interval_attr)))
        try:
            if getattr(plugin_config, enabled_attr):
                await func()
        except asyncio.CancelledError:
            raise
        except Exception:  # 定时任务必须存活
            logger.exception("SNS subscription: 轮询任务 {} 异常", name)
        await asyncio.sleep(interval)


def install_jobs() -> None:
    """按当前配置（重新）创建轮询任务。"""

    for task in _tasks.values():
        task.cancel()
    _tasks.clear()
    _tasks["bilibili_dynamic"] = asyncio.create_task(
        _loop(
            "bilibili_dynamic",
            poll_bilibili_dynamics,
            "sns_bilibili_dynamic_interval",
            "sns_enable_bilibili_dynamic",
            initial_delay=5,
        )
    )
    _tasks["bilibili_live"] = asyncio.create_task(
        _loop(
            "bilibili_live",
            poll_bilibili_lives,
            "sns_bilibili_live_interval",
            "sns_enable_bilibili_live",
            initial_delay=12,
        )
    )
    _tasks["douyin"] = asyncio.create_task(
        _loop(
            "douyin",
            poll_douyin,
            "sns_douyin_interval",
            "sns_enable_douyin",
            initial_delay=20,
        )
    )


async def shutdown_jobs() -> None:
    for task in _tasks.values():
        task.cancel()
    tasks = list(_tasks.values())
    _tasks.clear()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            continue


# --------------------------------------------------------------- 运行期配置
_RUNTIME_BOOL_SETTINGS = {
    "runtime_enable_bilibili_dynamic": "sns_enable_bilibili_dynamic",
    "runtime_enable_bilibili_live": "sns_enable_bilibili_live",
    "runtime_enable_douyin": "sns_enable_douyin",
    "runtime_notify_live_end": "sns_notify_live_end",
}
_RUNTIME_INT_SETTINGS = {
    "runtime_bilibili_dynamic_interval": "sns_bilibili_dynamic_interval",
    "runtime_bilibili_live_interval": "sns_bilibili_live_interval",
    "runtime_douyin_interval": "sns_douyin_interval",
}


async def load_runtime_config() -> None:
    for key, attr in _RUNTIME_BOOL_SETTINGS.items():
        value = await repository.get_setting(key)
        if value in {"true", "false"}:
            setattr(plugin_config, attr, value == "true")
    for key, attr in _RUNTIME_INT_SETTINGS.items():
        value = await repository.get_setting(key)
        if value.isdigit():
            setattr(plugin_config, attr, int(value))


async def save_runtime_config(
    *,
    enable_bilibili_dynamic: bool,
    enable_bilibili_live: bool,
    enable_douyin: bool,
    notify_live_end: bool,
    bilibili_dynamic_interval: int,
    bilibili_live_interval: int,
    douyin_interval: int,
) -> None:
    values = {
        "runtime_enable_bilibili_dynamic": enable_bilibili_dynamic,
        "runtime_enable_bilibili_live": enable_bilibili_live,
        "runtime_enable_douyin": enable_douyin,
        "runtime_notify_live_end": notify_live_end,
    }
    for key, flag in values.items():
        await repository.set_setting(key, "true" if flag else "false")
    intervals = {
        "runtime_bilibili_dynamic_interval": max(30, bilibili_dynamic_interval),
        "runtime_bilibili_live_interval": max(10, bilibili_live_interval),
        "runtime_douyin_interval": max(60, douyin_interval),
    }
    for key, interval in intervals.items():
        await repository.set_setting(key, str(interval))
    await load_runtime_config()
    install_jobs()
