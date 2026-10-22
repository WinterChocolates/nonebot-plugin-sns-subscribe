def _profile(avatar: str = "https://p3.douyinpic.com/avatar.jpg"):
    from nonebot_plugin_sns_subscribe.models import Platform, AuthorProfile

    return AuthorProfile(
        platform=Platform.DOUYIN,
        author_id="MS4wLjABAAAA",
        name="某博主",
        avatar=avatar,
    )


def _snapshot(cover: str, is_live: bool = True, web_rid: str = "123456"):
    from nonebot_plugin_sns_subscribe.sources.douyin_parser import (
        DouyinLive,
        DouyinSnapshot,
    )

    return DouyinSnapshot(
        sec_user_id="MS4wLjABAAAA",
        nickname="某博主",
        avatar_url="https://p3.douyinpic.com/avatar.jpg",
        web_rid=web_rid,
        live=DouyinLive(
            is_live=is_live,
            room_id="7300000000000000000",
            web_rid=web_rid,
            title="测试直播",
            cover_url=cover,
        ),
    )


def test_live_cover_falls_back_to_avatar():
    from nonebot_plugin_sns_subscribe.sources.douyin import DouyinSource

    content = DouyinSource._live_content(_profile(), _snapshot(cover=""))
    assert content.cover == "https://p3.douyinpic.com/avatar.jpg"
    assert content.author_avatar == content.cover


def test_live_cover_keeps_detected_cover():
    from nonebot_plugin_sns_subscribe.sources.douyin import DouyinSource

    content = DouyinSource._live_content(
        _profile(), _snapshot(cover="https://p3.douyinpic.com/live.jpg")
    )
    assert content.cover == "https://p3.douyinpic.com/live.jpg"


def test_live_cover_fallback_without_avatar_stays_empty():
    from nonebot_plugin_sns_subscribe.sources.douyin import DouyinSource

    content = DouyinSource._live_content(_profile(avatar=""), _snapshot(cover=""))
    assert content.cover == ""
