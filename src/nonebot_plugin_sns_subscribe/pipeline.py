"""消息投递管线：过滤 → @全体 → 渲染 → 发送（带幂等去重）。"""

from __future__ import annotations

from enum import Enum

from nonebot import logger, get_bots
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed

from .rules import should_at_all, matches_any_filter
from .assets import select_render_assets
from .models import LiveAction, ContentKind, PushContent
from .database import repository
from .renderer import renderer


class DeliveryResult(str, Enum):
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


def content_as_text(content: PushContent) -> str:
    if content.kind is ContentKind.LIVE and content.live_action is LiveAction.STOP:
        return f"🔴 {content.author_name} 直播结束啦，下次见~"
    header = f"【{content.platform.label}】{content.author_name}：{content.title}"
    lines = [header]
    if content.text and content.text != content.title:
        lines.append(content.text)
    if content.forward is not None:
        lines.append(f"转发自 @{content.forward.author_name}")
        if content.forward.title and content.forward.title != "发布了新动态":
            lines.append(content.forward.title)
        if content.forward.text and content.forward.text != content.forward.title:
            lines.append(content.forward.text)
    lines.append(content.url)
    return "\n".join(lines)


def _onebot() -> Bot | None:
    for bot in get_bots().values():
        if isinstance(bot, Bot):
            return bot
    return None


def _is_self_echo_timeout(error: ActionFailed) -> bool:
    info = error.info
    detail = " ".join(str(info.get(key) or "") for key in ("message", "wording"))
    return (
        int(info.get("retcode") or 0) == 1200
        and "waitforselfecho timeout" in detail.lower()
    )


async def _deliver_to_group(
    group_id: int,
    content: PushContent,
    *,
    bypass_filters: bool = False,
) -> DeliveryResult:
    bot = _onebot()
    if bot is None:
        logger.warning("SNS subscription: 没有已连接的 OneBot V11 bot")
        return DeliveryResult.FAILED

    if not bypass_filters:
        patterns = [pattern for _, pattern in await repository.list_filters(group_id)]
        if matches_any_filter(content.filter_text, patterns):
            logger.info(
                "SNS subscription: 内容 {} 命中群 {} 的过滤规则，已跳过",
                content.id,
                group_id,
            )
            return DeliveryResult.SKIPPED

    switches = await repository.get_atall(content.platform, group_id, content.author_id)
    message = Message()
    if should_at_all(switches, content.kind, content.live_action):
        message += MessageSegment.at("all")
        message += MessageSegment.text("\n")

    if content.kind is ContentKind.LIVE and content.live_action is LiveAction.STOP:
        message += MessageSegment.text(content_as_text(content))
    else:
        try:
            selection = await select_render_assets(group_id, content)
            image = await renderer.render(content, selection)
            message += MessageSegment.image(image)
        except Exception:  # 渲染失败不应阻断推送
            logger.exception("SNS subscription: 渲染失败，回退为文本消息")
            message += MessageSegment.text(content_as_text(content))

    try:
        await bot.send_group_msg(group_id=group_id, message=message)
    except ActionFailed as exc:
        if _is_self_echo_timeout(exc):
            logger.warning(
                "SNS subscription: 群 {} 发送后等待自身上报超时，"
                "按已送达处理以避免重复推送",
                group_id,
            )
            return DeliveryResult.SENT
        logger.exception("SNS subscription: 发送到群 {} 失败", group_id)
        return DeliveryResult.FAILED
    except Exception:
        logger.exception("SNS subscription: 发送到群 {} 失败", group_id)
        return DeliveryResult.FAILED
    return DeliveryResult.SENT


async def push_to_group(
    group_id: int, content: PushContent, *, bypass_filters: bool = False
) -> bool:
    result = await _deliver_to_group(group_id, content, bypass_filters=bypass_filters)
    return result is DeliveryResult.SENT


async def dispatch_content(content: PushContent) -> bool:
    """把一条内容投递给所有订阅该作者的群，保证同一内容只投递一次。"""

    await repository.prune_delivery_receipts(
        content.platform, content.namespace, content.author_id, content.key
    )
    completed = True
    for subscription in await repository.groups_for(
        content.platform, content.author_id
    ):
        if await repository.has_delivery_receipt(
            content.platform,
            content.namespace,
            content.author_id,
            content.key,
            subscription.group_id,
        ):
            continue
        if not await repository.claim_delivery(
            content.platform,
            content.namespace,
            content.author_id,
            content.key,
            subscription.group_id,
        ):
            completed = False
            continue
        try:
            result = await _deliver_to_group(subscription.group_id, content)
            if result is DeliveryResult.FAILED:
                completed = False
            else:
                await repository.set_delivery_receipt(
                    content.platform,
                    content.namespace,
                    content.author_id,
                    content.key,
                    subscription.group_id,
                )
        finally:
            await repository.release_delivery_claim(
                content.platform,
                content.namespace,
                content.author_id,
                content.key,
                subscription.group_id,
            )
    return completed
