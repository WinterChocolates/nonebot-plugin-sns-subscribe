import pytest


def test_normalize_atall_switches_all_is_exclusive():
    from nonebot_plugin_sns_subscribe.rules import normalize_atall_switches
    from nonebot_plugin_sns_subscribe.models import AtAllKind

    assert normalize_atall_switches([], AtAllKind.ALL, enabled=True) == {"all"}

    switches = normalize_atall_switches(["all"], AtAllKind.VIDEO, enabled=True)
    assert switches == {"video"}

    switches = normalize_atall_switches(["video"], AtAllKind.LIVE, enabled=True)
    assert switches == {"live", "video"}

    switches = normalize_atall_switches(
        ["live", "video"], AtAllKind.VIDEO, enabled=False
    )
    assert switches == {"live"}


def test_should_at_all():
    from nonebot_plugin_sns_subscribe.rules import should_at_all
    from nonebot_plugin_sns_subscribe.models import LiveAction, ContentKind

    assert should_at_all({"all"}, ContentKind.VIDEO) is True
    assert should_at_all({"video"}, ContentKind.VIDEO) is True
    assert should_at_all({"video"}, ContentKind.DYNAMIC) is False
    assert should_at_all({"live"}, ContentKind.LIVE, LiveAction.START) is True
    assert should_at_all({"live"}, ContentKind.LIVE, LiveAction.STOP) is False


def test_validate_regex():
    from nonebot_plugin_sns_subscribe.rules import validate_regex

    validate_regex("抽奖|广告")

    with pytest.raises(ValueError, match="长度"):
        validate_regex("")

    with pytest.raises(ValueError, match="无效的正则表达式"):
        validate_regex("(")

    with pytest.raises(ValueError, match="长度"):
        validate_regex("a" * 257)


def test_matches_any_filter():
    from nonebot_plugin_sns_subscribe.rules import matches_any_filter

    patterns = ["抽奖", "广告\\d+"]

    assert matches_any_filter("本条包含抽奖信息", patterns) is True
    assert matches_any_filter("广告123", patterns) is True
    assert matches_any_filter("正常内容", patterns) is False
    assert matches_any_filter("正常内容", ["("]) is False
