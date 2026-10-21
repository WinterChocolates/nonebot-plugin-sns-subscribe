import pytest


def test_find_card_id():
    from nonebot_plugin_sns_subscribe.renderer import find_card_id

    assert find_card_id('<article id="sns-card"></article>') == "sns-card"
    assert find_card_id("<div id='bili-card'></div>") == "bili-card"
    assert find_card_id('<article id="douyin-card"></article>') == "douyin-card"
    assert find_card_id("<div class='card'></div>") is None


def test_validate_template_source():
    from nonebot_plugin_sns_subscribe.renderer import validate_template_source

    assert validate_template_source('<article id="sns-card"></article>') == "sns-card"

    with pytest.raises(ValueError, match="不能为空"):
        validate_template_source("")

    with pytest.raises(ValueError, match="sns-card"):
        validate_template_source("<html><body>no card</body></html>")


def test_render_kind_mapping():
    from nonebot_plugin_sns_subscribe.models import Platform, ContentKind
    from nonebot_plugin_sns_subscribe.renderer import CUSTOMIZABLE_KINDS, render_kind

    assert render_kind(Platform.BILIBILI, ContentKind.DYNAMIC) == "dynamic"
    assert render_kind(Platform.BILIBILI, ContentKind.VIDEO) == "video"
    assert render_kind(Platform.BILIBILI, ContentKind.ARTICLE) == "dynamic"
    assert render_kind(Platform.BILIBILI, ContentKind.LIVE) == "live"
    assert render_kind(Platform.DOUYIN, ContentKind.DYNAMIC) == "video"
    assert render_kind(Platform.DOUYIN, ContentKind.LIVE) == "live"

    assert CUSTOMIZABLE_KINDS[Platform.BILIBILI] == ("dynamic", "video", "live")
    assert CUSTOMIZABLE_KINDS[Platform.DOUYIN] == ("video", "live")


def test_builtin_templates_exist_and_validate():
    from nonebot_plugin_sns_subscribe.paths import builtin_template_path
    from nonebot_plugin_sns_subscribe.renderer import (
        CUSTOMIZABLE_KINDS,
        validate_template_source,
    )

    for platform, kinds in CUSTOMIZABLE_KINDS.items():
        for kind in kinds:
            path = builtin_template_path(platform.value, kind)
            assert path.is_file(), path
            source = path.read_text(encoding="utf-8")
            assert validate_template_source(source) == "sns-card"
            assert "font_css" in source


def test_template_source_prefers_custom_file(tmp_path):
    from nonebot_plugin_sns_subscribe.models import (
        Platform,
        ContentKind,
        PushContent,
        RenderSelection,
    )
    from nonebot_plugin_sns_subscribe.renderer import CardRenderer

    content = PushContent(
        platform=Platform.BILIBILI,
        id="1",
        author_id="2",
        kind=ContentKind.DYNAMIC,
        title="标题",
        url="https://t.bilibili.com/1",
        author_name="UP",
    )
    renderer = CardRenderer()

    builtin = renderer.template_source(content, RenderSelection())
    assert "sns-card" in builtin

    custom = tmp_path / "custom.html"
    custom.write_text('<article id="sns-card">custom</article>', encoding="utf-8")
    source = renderer.template_source(
        content, RenderSelection(template_path=str(custom))
    )
    assert "custom" in source


async def test_has_cjk_font_returns_bool():
    from nonebot_plugin_sns_subscribe.renderer import has_cjk_font

    assert isinstance(await has_cjk_font(), bool)
