import pytest


def make_content(**overrides):
    from nonebot_plugin_sns_subscribe.models import (
        Platform,
        ContentKind,
    )

    params = {
        "platform": Platform.BILIBILI,
        "id": "BV1xx411c7mD",
        "author_id": "2",
        "kind": ContentKind.VIDEO,
        "title": "【示例】B站视频标题",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD",
        "author_name": "示例UP主",
        "text": "视频简介",
        "author_avatar": "https://i0.hdslb.com/bfs/face/a.jpg",
        "cover": "https://i0.hdslb.com/bfs/archive/xxx.jpg",
    }
    params.update(overrides)
    return _coerce(params)


def _coerce(params: dict):
    from nonebot_plugin_sns_subscribe.models import (
        Platform,
        LiveAction,
        ContentKind,
        PushContent,
    )

    for key, enum in (
        ("platform", Platform),
        ("kind", ContentKind),
        ("live_action", LiveAction),
    ):
        value = params.get(key)
        if isinstance(value, str):
            params[key] = enum(value)
    return PushContent(**params)


def make_douyin_content(**overrides):
    from nonebot_plugin_sns_subscribe.models import (
        Platform,
        ContentKind,
    )

    params = {
        "platform": Platform.DOUYIN,
        "id": "7300000000000000000",
        "author_id": "MS4wLjABAAAA",
        "kind": ContentKind.VIDEO,
        "title": "抖音示例标题",
        "url": "https://www.douyin.com/video/7300000000000000000",
        "author_name": "某博主",
        "cover": "https://p3.douyinpic.com/cover.jpg",
    }
    params.update(overrides)
    return make_content(**params)


def test_forward_nodes_only_carry_link():
    from nonebot_plugin_sns_subscribe.forward import link_forward_nodes

    nodes = link_forward_nodes(make_content(), 10001)

    assert nodes == [
        {
            "type": "node",
            "data": {
                "user_id": "10001",
                "nickname": "【B站】示例UP主",
                "content": "https://www.bilibili.com/video/BV1xx411c7mD",
            },
        }
    ]


def test_forward_nodes_fall_back_to_kind_url():
    from nonebot_plugin_sns_subscribe.forward import link_forward_nodes

    nodes = link_forward_nodes(make_content(url="", title=""), 10001)
    assert nodes is not None
    assert nodes[0]["data"]["content"] == (
        "https://www.bilibili.com/video/BV1xx411c7mD"
    )


def test_forward_nodes_normalize_scheme_less_url():
    from nonebot_plugin_sns_subscribe.forward import link_forward_nodes

    nodes = link_forward_nodes(
        make_content(url="//www.bilibili.com/video/BV1xx411c7mD"), 10001
    )
    assert nodes is not None
    assert nodes[0]["data"]["content"] == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_forward_nodes_default_nickname():
    from nonebot_plugin_sns_subscribe.forward import link_forward_nodes

    nodes = link_forward_nodes(make_content(author_name=""), 10001)
    assert nodes is not None
    assert nodes[0]["data"]["nickname"] == "【B站】"


def test_forward_nickname_combines_author_and_platform():
    from nonebot_plugin_sns_subscribe.forward import forward_nickname

    assert forward_nickname(make_content()) == "【B站】示例UP主"
    assert forward_nickname(make_douyin_content()) == "【抖音】某博主"


def test_douyin_forward_sends_video_link():
    from nonebot_plugin_sns_subscribe.forward import (
        link_forward_nodes,
        link_forward_message,
    )

    nodes = link_forward_nodes(make_douyin_content(), 10001)
    assert nodes == [
        {
            "type": "node",
            "data": {
                "user_id": "10001",
                "nickname": "【抖音】某博主",
                "content": "https://www.douyin.com/video/7300000000000000000",
            },
        }
    ]

    message = link_forward_message(make_douyin_content(), 10001)
    assert message is not None
    node = message[0]
    assert node.data["nickname"] == "【抖音】某博主"
    assert "https://www.douyin.com/video/7300000000000000000" in str(
        node.data["content"]
    )


def test_douyin_forward_falls_back_to_video_url():
    from nonebot_plugin_sns_subscribe.forward import link_forward_nodes

    nodes = link_forward_nodes(make_douyin_content(url=""), 10001)
    assert nodes is not None
    assert nodes[0]["data"]["content"] == (
        "https://www.douyin.com/video/7300000000000000000"
    )


def test_forward_message_uses_node_segment():
    from nonebot_plugin_sns_subscribe.forward import link_forward_message

    message = link_forward_message(make_content(), 10001)
    assert message is not None
    assert len(message) == 1

    node = message[0]
    assert node.type == "node"
    assert node.data["nickname"] == "【B站】示例UP主"
    assert node.data["user_id"] == "10001"
    assert "https://www.bilibili.com/video/BV1xx411c7mD" in str(node.data["content"])


def test_live_end_is_not_forwarded():
    from nonebot_plugin_sns_subscribe.models import LiveAction
    from nonebot_plugin_sns_subscribe.forward import (
        link_forward_nodes,
        link_forward_message,
    )

    for content in (
        make_content(kind="live", live_action=LiveAction.STOP, url=""),
        make_douyin_content(kind="live", live_action=LiveAction.STOP, url=""),
    ):
        assert link_forward_nodes(content, 10001) is None
        assert link_forward_message(content, 10001) is None


def test_forward_skips_unsupported_kind():
    from nonebot_plugin_sns_subscribe.models import ContentKind
    from nonebot_plugin_sns_subscribe.forward import link_forward_nodes

    # 抖音没有专栏，且链接为空时无法兜底 → 不转发
    content = make_douyin_content(kind=ContentKind.ARTICLE, url="")
    assert link_forward_nodes(content, 10001) is None


@pytest.mark.parametrize("url", ["", "https://live.douyin.com/room"])
def test_douyin_live_link_fallback(url):
    from nonebot_plugin_sns_subscribe.forward import link_forward_nodes

    content = make_douyin_content(kind="live", id="1234", url=url)
    nodes = link_forward_nodes(content, 10001)

    assert nodes is not None
    expected = url or "https://live.douyin.com/1234"
    assert nodes[0]["data"]["content"] == expected
