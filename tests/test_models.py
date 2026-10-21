def test_push_content_key_and_namespace():
    from nonebot_plugin_sns_subscribe.models import (
        Platform,
        LiveAction,
        ContentKind,
        PushContent,
    )

    content = PushContent(
        platform=Platform.BILIBILI,
        id="123",
        author_id="2",
        kind=ContentKind.LIVE,
        title="开播了：测试",
        url="https://live.bilibili.com/1",
        author_name="测试 UP",
        live_action=LiveAction.START,
    )

    assert content.key == "live:start:123"
    assert content.namespace == "live"
    assert "开播了：测试" in content.filter_text

    video = PushContent(
        platform=Platform.DOUYIN,
        id="456",
        author_id="SEC",
        kind=ContentKind.VIDEO,
        title="新视频",
        url="https://www.douyin.com/video/456",
        author_name="抖音作者",
    )

    assert video.key == "video:publish:456"
    assert video.namespace == "post"


def test_filter_text_includes_forward():
    from nonebot_plugin_sns_subscribe.models import Platform, ContentKind, PushContent

    forward = PushContent(
        platform=Platform.BILIBILI,
        id="orig",
        author_id="3",
        kind=ContentKind.DYNAMIC,
        title="原动态",
        url="https://t.bilibili.com/orig",
        author_name="原作者",
    )
    content = PushContent(
        platform=Platform.BILIBILI,
        id="1",
        author_id="2",
        kind=ContentKind.DYNAMIC,
        title="转发动态",
        url="https://t.bilibili.com/1",
        author_name="转发者",
        forward=forward,
    )

    assert "原动态" in content.filter_text
    assert "原作者" in content.filter_text


def test_published_text_uses_china_timezone():
    from nonebot_plugin_sns_subscribe.models import Platform, ContentKind, PushContent

    content = PushContent(
        platform=Platform.BILIBILI,
        id="1",
        author_id="2",
        kind=ContentKind.DYNAMIC,
        title="标题",
        url="https://t.bilibili.com/1",
        author_name="UP",
        published_at=1700000000,
    )

    assert content.published_text == "2023-11-15 06:13"

    content.published_at = 0
    assert content.published_text == ""


def test_clean_title_strips_hashtags():
    from nonebot_plugin_sns_subscribe.models import clean_title

    assert clean_title("新视频 #话题 #测试") == "新视频"
    assert clean_title("#只有话题") == "发布了新内容"
    assert clean_title("   ") == "发布了新内容"
    assert clean_title("", "默认标题") == "默认标题"


def test_platform_and_kind_labels():
    from nonebot_plugin_sns_subscribe.models import Platform, ContentKind

    assert Platform.BILIBILI.label == "B站"
    assert Platform.DOUYIN.label == "抖音"
    assert Platform.BILIBILI.accent == "#fb7299"
    assert Platform.DOUYIN.accent == "#fe2c55"
    assert ContentKind.DYNAMIC.label == "动态"
    assert ContentKind.LIVE.label == "直播"
