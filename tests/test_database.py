import pytest


async def _new_repository(tmp_path):
    from nonebot_plugin_sns_subscribe.database import Repository

    repository = Repository(tmp_path / "subscription.sqlite3")
    await repository.initialize()
    return repository


def _profile(platform, author_id="2", name="测试作者"):
    from nonebot_plugin_sns_subscribe.models import AuthorProfile

    return AuthorProfile(platform=platform, author_id=author_id, name=name)


@pytest.mark.asyncio
async def test_subscription_crud(tmp_path):
    from nonebot_plugin_sns_subscribe.models import Platform

    repository = await _new_repository(tmp_path)
    profile = _profile(Platform.BILIBILI)

    assert await repository.add_subscription(profile, 111, "测试群") is True
    assert await repository.add_subscription(profile, 111, "测试群") is False

    subscriptions = await repository.list_subscriptions(111)
    assert len(subscriptions) == 1
    assert subscriptions[0].author_id == "2"
    assert subscriptions[0].group_name == "测试群"
    assert subscriptions[0].display_name == "测试作者"

    tracked = await repository.list_tracked(Platform.BILIBILI)
    assert [item.author_id for item in tracked] == ["2"]
    assert await repository.list_tracked(Platform.DOUYIN) == []

    assert await repository.remove_subscription(Platform.BILIBILI, 111, "2") is True
    assert await repository.remove_subscription(Platform.BILIBILI, 111, "2") is False
    assert await repository.list_subscriptions(111) == []


@pytest.mark.asyncio
async def test_filters_and_atall(tmp_path):
    from nonebot_plugin_sns_subscribe.models import Platform, AtAllKind

    repository = await _new_repository(tmp_path)

    assert await repository.add_filter(111, "抽奖") is True
    assert await repository.add_filter(111, "抽奖") is False
    assert [pattern for _, pattern in await repository.list_filters(111)] == ["抽奖"]

    with pytest.raises(ValueError, match="无效的正则表达式"):
        await repository.add_filter(111, "(")

    assert await repository.remove_filter(111, "1") is True
    assert await repository.remove_filter(111, "抽奖") is False

    switches = await repository.set_atall(
        Platform.BILIBILI, 111, "2", AtAllKind.LIVE, enabled=True
    )
    assert switches == {"live"}
    assert await repository.get_atall(Platform.BILIBILI, 111, "2") == {"live"}

    rows = await repository.list_atall(111)
    assert rows == [
        {"platform": "bilibili", "author_id": "2", "kind": "live"},
    ]


@pytest.mark.asyncio
async def test_overrides_and_snapshot(tmp_path):
    from nonebot_plugin_sns_subscribe.models import Platform

    repository = await _new_repository(tmp_path)
    await repository.add_subscription(_profile(Platform.BILIBILI), 111, "测试群")

    await repository.set_override(
        Platform.BILIBILI, 111, "dynamic", "my.html", "font.ttf", "#123456", "#654321"
    )
    resolved = await repository.resolve_override(Platform.BILIBILI, 111, "dynamic")
    assert resolved == ("my.html", "font.ttf", "#123456", "#654321")

    overrides = await repository.list_overrides(111)
    assert overrides[0]["template_name"] == "my.html"

    await repository.clear_template_references(Platform.BILIBILI, "dynamic", "my.html")
    assert (await repository.resolve_override(Platform.BILIBILI, 111, "dynamic"))[
        0
    ] is None

    await repository.set_override(Platform.BILIBILI, 111, "dynamic")
    assert await repository.list_overrides(111) == []

    snapshot = await repository.export_snapshot()
    assert len(snapshot["groups"]) == 1
    assert snapshot["groups"][0]["group_id"] == 111
    assert snapshot["groups"][0]["subscriptions"][0]["author_id"] == "2"


@pytest.mark.asyncio
async def test_state_and_delivery(tmp_path):
    from nonebot_plugin_sns_subscribe.models import Platform

    repository = await _new_repository(tmp_path)
    platform = Platform.DOUYIN

    await repository.set_state(platform, "video", "SEC", "v1")
    assert await repository.get_state(platform, "video", "SEC") == "v1"
    assert await repository.get_state(platform, "video", "MISSING") is None

    await repository.set_json_state(
        platform, "live", "SEC", {"live": True, "room": "1"}
    )
    assert await repository.get_json_state(platform, "live", "SEC") == {
        "live": True,
        "room": "1",
    }

    assert await repository.claim_delivery(platform, "video", "SEC", "v1", 111) is True
    assert await repository.claim_delivery(platform, "video", "SEC", "v1", 111) is False
    await repository.release_delivery_claim(platform, "video", "SEC", "v1", 111)

    assert (
        await repository.has_delivery_receipt(platform, "video", "SEC", "v1", 111)
        is False
    )
    await repository.set_delivery_receipt(platform, "video", "SEC", "v1", 111)
    assert (
        await repository.has_delivery_receipt(platform, "video", "SEC", "v1", 111)
        is True
    )

    await repository.set_delivery_receipt(platform, "video", "SEC", "v0", 111)
    await repository.prune_delivery_receipts(platform, "video", "SEC", "v1")
    assert (
        await repository.has_delivery_receipt(platform, "video", "SEC", "v0", 111)
        is False
    )


@pytest.mark.asyncio
async def test_settings_and_clear(tmp_path):
    from nonebot_plugin_sns_subscribe.models import Platform

    repository = await _new_repository(tmp_path)
    await repository.add_subscription(_profile(Platform.DOUYIN, "SEC"), 111, "群")

    await repository.set_setting("douyin_cookie", "sessionid=abc")
    assert await repository.get_setting("douyin_cookie") == "sessionid=abc"
    assert await repository.get_setting("missing", "default") == "default"

    assert await repository.clear_group(111) == 1
    assert await repository.list_subscriptions(111) == []

    await repository.add_subscription(_profile(Platform.DOUYIN, "SEC"), 111, "群")
    assert await repository.clear_all() == 1
    assert await repository.list_subscriptions() == []
