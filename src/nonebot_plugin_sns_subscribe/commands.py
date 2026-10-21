"""``/sub`` 统一命令。"""

from __future__ import annotations

import asyncio
from typing import Annotated

from nonebot import logger, on_command
from nonebot.params import CommandArg
from nonebot.matcher import Matcher
from nonebot.exception import MatcherException
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment

from .paths import DATA_DIR
from .config import plugin_config
from .models import Platform, AtAllKind, ContentKind, RenderSelection
from .sources import SourceError, douyin_source, bilibili_source
from .database import repository
from .pipeline import push_to_group
from .renderer import renderer
from .services import (
    subscribe,
    parse_kind,
    make_qr_png,
    unsubscribe,
    manual_content,
    parse_platform,
    sample_content,
    shorten_author_id,
    wait_bilibili_login,
)
from .permissions import CommandContext, parse_context

HELP_TEXT = """B站 / 抖音订阅推送
/sub add <bili|douyin> <UID|主页链接>   订阅作者
/sub del <bili|douyin> <作者ID>         取消订阅
/sub list                               查看本群订阅
/sub listall                            查看全部订阅（超管）
/sub delall | delallall                 清空本群 / 全部订阅
/sub filter add <正则>                  添加关键词过滤
/sub filter del <规则或序号>
/sub filter list
/sub atall <all|video|dynamic|live> on|off <作者ID>
/sub push <bili|douyin> <类型> <编号|主页链接>
/sub login bilibili                     扫码登录 B 站（超管）
/sub login douyin                       获取抖音登录方式（超管）
/sub status                             查看运行状态
/sub help                               显示本帮助

超级用户可在命令末尾追加 -群号 指定群。"""

sub = on_command("sub", aliases={"订阅", "sns"}, priority=10, block=True)


def _require_manage(ctx: CommandContext) -> None:
    if not ctx.can_manage:
        raise PermissionError("仅群主、群管理员或超级用户可执行此操作")


def _require_group(ctx: CommandContext) -> int:
    if ctx.group_id is None:
        raise ValueError("此命令需要在群内使用，或在私聊末尾追加 -群号")
    return ctx.group_id


def _require_super(ctx: CommandContext) -> None:
    if not ctx.is_superuser:
        raise PermissionError("此命令仅限超级用户")


async def _group_name(bot: Bot, group_id: int) -> str:
    try:
        info = await bot.get_group_info(group_id=group_id, no_cache=True)
    except Exception:  # 取群名失败不影响订阅
        return ""
    return str(info.get("group_name") or "")


async def _handle_add(matcher: Matcher, bot: Bot, ctx: CommandContext) -> None:
    _require_manage(ctx)
    if len(ctx.tokens) != 3:
        raise ValueError("用法：/sub add <bili|douyin> <UID|主页链接>")
    platform = parse_platform(ctx.tokens[1])
    group_id = _require_group(ctx)
    profile, added = await subscribe(
        platform, ctx.tokens[2], group_id, await _group_name(bot, group_id)
    )
    action = "已订阅" if added else "本群已订阅"
    await matcher.finish(
        f"{action} {profile.name}（{profile.platform.label} · "
        f"{shorten_author_id(profile.author_id)}）"
    )


async def _handle_del(matcher: Matcher, ctx: CommandContext) -> None:
    _require_manage(ctx)
    if len(ctx.tokens) < 3:
        raise ValueError("用法：/sub del <bili|douyin> <作者ID>")
    platform = parse_platform(ctx.tokens[1])
    author_id = " ".join(ctx.tokens[2:]).strip()
    removed = await unsubscribe(platform, _require_group(ctx), author_id)
    await matcher.finish("已取消订阅" if removed else "本群没有该订阅")


async def _handle_list(matcher: Matcher, ctx: CommandContext) -> None:
    items = await repository.list_subscriptions(_require_group(ctx))
    if not items:
        await matcher.finish("本群暂无订阅")
    lines = [
        f"{index}. [{item.platform.label}] {item.display_name}"
        f"（{shorten_author_id(item.author_id)}）"
        for index, item in enumerate(items, 1)
    ]
    await matcher.finish("本群订阅：\n" + "\n".join(lines))


async def _handle_listall(matcher: Matcher, ctx: CommandContext) -> None:
    _require_super(ctx)
    items = await repository.list_subscriptions()
    if not items:
        await matcher.finish("当前没有任何订阅")
    lines = [
        f"群 {item.group_id} [{item.platform.label}] {item.display_name}"
        f"（{shorten_author_id(item.author_id)}）"
        for item in items
    ]
    await matcher.finish("全部订阅：\n" + "\n".join(lines))


async def _handle_clear(
    matcher: Matcher, ctx: CommandContext, *, all_groups: bool
) -> None:
    if all_groups:
        _require_super(ctx)
        count = await repository.clear_all()
    else:
        _require_manage(ctx)
        count = await repository.clear_group(_require_group(ctx))
    await matcher.finish(f"已删除 {count} 条订阅")


async def _resolve_atall_targets(
    group_id: int, selector: str
) -> list[tuple[Platform, str]]:
    items = await repository.list_subscriptions(group_id)
    if ":" in selector:
        raw_platform, _, author_id = selector.partition(":")
        platform = parse_platform(raw_platform)
        return [(platform, author_id.strip())]
    matches = [
        (item.platform, item.author_id)
        for item in items
        if selector in {item.author_id, item.author_name}
    ]
    if not matches:
        raise ValueError("本群没有匹配该作者的订阅，可用 平台:作者ID 精确指定")
    return matches


async def _handle_atall(matcher: Matcher, ctx: CommandContext) -> None:
    group_id = _require_group(ctx)
    if len(ctx.tokens) == 2 and ctx.tokens[1].lower() == "list":
        rows = await repository.list_atall(group_id)
        if not rows:
            await matcher.finish("本群未开启任何 @全体")
        lines = [
            f"[{Platform(row['platform']).label}] {row['author_id']}：{row['kind']}"
            for row in rows
        ]
        await matcher.finish("@全体配置：\n" + "\n".join(lines))

    _require_manage(ctx)
    if len(ctx.tokens) != 4:
        raise ValueError(
            "用法：/sub atall <all|video|dynamic|live> <on|off> <作者ID|平台:作者ID>"
        )
    kind_text = ctx.tokens[1].lower()
    if kind_text not in AtAllKind._value2member_map_:
        raise ValueError("类型必须是 all、video、dynamic、live、article 或 music")
    switch = ctx.tokens[2].lower()
    if switch not in {"on", "off"}:
        raise ValueError("开关必须是 on 或 off")
    targets = await _resolve_atall_targets(group_id, ctx.tokens[3])
    lines = []
    for platform, author_id in targets:
        updated = await repository.set_atall(
            platform,
            group_id,
            author_id,
            AtAllKind(kind_text),
            enabled=switch == "on",
        )
        summary = ", ".join(sorted(updated)) if updated else "全部关闭"
        lines.append(f"[{platform.label}] {shorten_author_id(author_id)}：{summary}")
    await matcher.finish("@全体已更新：\n" + "\n".join(lines))


async def _handle_filter(matcher: Matcher, ctx: CommandContext) -> None:
    group_id = _require_group(ctx)
    if len(ctx.tokens) == 2 and ctx.tokens[1].lower() == "list":
        rules = await repository.list_filters(group_id)
        if not rules:
            await matcher.finish("本群暂无过滤规则")
        lines = [f"{index}. {pattern}" for index, (_, pattern) in enumerate(rules, 1)]
        await matcher.finish("过滤规则：\n" + "\n".join(lines))

    _require_manage(ctx)
    if len(ctx.tokens) < 3:
        raise ValueError("用法：/sub filter add|del <关键词或正则>")
    action = ctx.tokens[1].lower()
    selector = " ".join(ctx.tokens[2:]).strip()
    if action == "add":
        added = await repository.add_filter(group_id, selector)
        await matcher.finish("过滤规则已添加" if added else "该过滤规则已存在")
    if action == "del":
        removed = await repository.remove_filter(group_id, selector)
        await matcher.finish("过滤规则已删除" if removed else "未找到该过滤规则")
    raise ValueError("过滤操作必须是 add、del 或 list")


async def _handle_push(matcher: Matcher, ctx: CommandContext) -> None:
    _require_manage(ctx)
    if len(ctx.tokens) < 4:
        raise ValueError("用法：/sub push <bili|douyin> <类型> <编号|主页链接>")
    platform = parse_platform(ctx.tokens[1])
    kind = parse_kind(ctx.tokens[2])
    identifier = " ".join(ctx.tokens[3:]).strip()
    content = await manual_content(platform, kind, identifier)
    sent = await push_to_group(_require_group(ctx), content, bypass_filters=True)
    await matcher.finish("推送成功" if sent else "推送失败，请查看日志")


async def _handle_login(matcher: Matcher, ctx: CommandContext) -> None:
    _require_super(ctx)
    target = ctx.tokens[1].lower() if len(ctx.tokens) > 1 else "bilibili"
    if target in {"bili", "bilibili", "b站"}:
        session_key, url = await bilibili_source.create_qr_login()
        if not session_key or not url:
            raise RuntimeError("B 站未返回登录二维码")
        await matcher.send("请在 3 分钟内使用哔哩哔哩客户端扫码登录")
        try:
            await asyncio.wait_for(
                matcher.send(MessageSegment.image(make_qr_png(url))), timeout=20
            )
        except Exception:  # 图片发送失败时回退到链接
            logger.warning("SNS subscription: 二维码图片发送失败，回退为链接")
            await matcher.send(f"二维码图片发送失败，请打开链接扫码：{url}")
        await wait_bilibili_login(session_key)
        await matcher.finish("B 站登录成功，Cookie 已保存")
    if target in {"dy", "douyin", "抖音"}:
        base = plugin_config.sns_web_public_url.strip()
        path = plugin_config.sns_web_path.rstrip("/")
        address = f"{base}{path}/" if base else f"{path}/"
        await matcher.finish(
            "抖音登录需要在 Web 后台完成（扫码 / 手机验证）：\n"
            f"{address}\n"
            "进入「账号登录」标签页即可发起扫码登录，"
            "也可以在后台直接粘贴浏览器 Cookie。"
        )
    raise ValueError("用法：/sub login <bilibili|douyin>")


async def _handle_status(matcher: Matcher, ctx: CommandContext) -> None:
    group_id = ctx.group_id
    all_items = await repository.list_subscriptions(group_id)
    bilibili_count = sum(1 for item in all_items if item.platform is Platform.BILIBILI)
    douyin_count = len(all_items) - bilibili_count
    bili_cookie = await bilibili_source.has_cookie()
    douyin_cookie = await douyin_source.has_cookie()
    lines = [
        f"本群订阅：B站 {bilibili_count} / 抖音 {douyin_count}",
        "B 站动态："
        + ("运行中" if plugin_config.sns_enable_bilibili_dynamic else "已关闭")
        + f"（每 {plugin_config.sns_bilibili_dynamic_interval}s，"
        + ("Cookie 已配置" if bili_cookie else "Cookie 未配置")
        + "）",
        "B 站直播："
        + ("运行中" if plugin_config.sns_enable_bilibili_live else "已关闭")
        + f"（每 {plugin_config.sns_bilibili_live_interval}s）",
        "抖音轮询："
        + ("运行中" if plugin_config.sns_enable_douyin else "已关闭")
        + f"（每 {plugin_config.sns_douyin_interval}s，"
        + ("Cookie 已配置" if douyin_cookie else "Cookie 未配置")
        + "）",
        f"数据目录：{DATA_DIR}",
    ]
    await matcher.finish("\n".join(lines))


async def _handle_help(matcher: Matcher, *, as_image: bool) -> None:
    if not as_image:
        await matcher.finish(HELP_TEXT)
    content = sample_content(Platform.BILIBILI, ContentKind.DYNAMIC)
    content.title = "B站 / 抖音订阅推送命令"
    content.text = HELP_TEXT
    content.url = "/sub help"
    content.author_name = "NoneBot2"
    try:
        image = await renderer.render(content, RenderSelection())
    except Exception:  # 渲染不可用时回退文本
        logger.exception("SNS subscription: 帮助图片渲染失败，回退文本")
        await matcher.finish(HELP_TEXT)
    await matcher.finish(MessageSegment.image(image))


@sub.handle()
async def handle_sub(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    argument: Annotated[Message, CommandArg()],
) -> None:
    raw = argument.extract_plain_text().strip()
    tokens = raw.split() if raw else ["help"]
    try:
        if tokens[0].lower() in {"help", "帮助"}:
            as_image = len(tokens) > 1 and tokens[1].lower() in {"image", "img", "图"}
            await _handle_help(matcher, as_image=as_image)
        ctx = parse_context(event, tokens)
        command = ctx.tokens[0].lower() if ctx.tokens else "help"
        if command == "add":
            await _handle_add(matcher, bot, ctx)
        if command == "del":
            await _handle_del(matcher, ctx)
        if command == "list":
            await _handle_list(matcher, ctx)
        if command == "listall":
            await _handle_listall(matcher, ctx)
        if command == "delall":
            await _handle_clear(matcher, ctx, all_groups=False)
        if command == "delallall":
            await _handle_clear(matcher, ctx, all_groups=True)
        if command == "atall":
            await _handle_atall(matcher, ctx)
        if command == "filter":
            await _handle_filter(matcher, ctx)
        if command == "push":
            await _handle_push(matcher, ctx)
        if command == "login":
            await _handle_login(matcher, ctx)
        if command == "status":
            await _handle_status(matcher, ctx)
        await matcher.finish("未知子命令，请使用 /sub help")
    except (
        ValueError,
        PermissionError,
        SourceError,
        RuntimeError,
        TimeoutError,
    ) as exc:
        await matcher.finish(str(exc))
    except MatcherException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 兜底，避免命令静默失败
        logger.exception("SNS subscription: 命令执行失败：{}", exc)
        await matcher.finish("操作失败，请查看日志")


__all__ = ["HELP_TEXT", "handle_sub", "sub"]
