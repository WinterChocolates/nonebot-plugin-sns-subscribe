import pytest
from fake import fake_group_message_event_v11
from nonebug import App


@pytest.mark.asyncio
async def test_sub_help(app: App):
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_sns_subscribe.commands import HELP_TEXT, sub

    event = fake_group_message_event_v11(message="/sub help")

    async with app.test_matcher(sub) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, HELP_TEXT, result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_sub_list_without_subscriptions(app: App):
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_sns_subscribe.commands import sub

    event = fake_group_message_event_v11(message="/sub list")

    async with app.test_matcher(sub) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_call_send(event, "本群暂无订阅", result=None, bot=bot)
        ctx.should_finished()


@pytest.mark.asyncio
async def test_sub_unknown_subcommand(app: App):
    import nonebot
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import Adapter as OnebotV11Adapter

    from nonebot_plugin_sns_subscribe.commands import sub

    event = fake_group_message_event_v11(message="/sub unknown")

    async with app.test_matcher(sub) as ctx:
        adapter = nonebot.get_adapter(OnebotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, event)
        ctx.should_call_send(
            event, "未知子命令，请使用 /sub help", result=None, bot=bot
        )
        ctx.should_finished()
