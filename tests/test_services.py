import pytest


def test_infer_kind_for_douyin():
    from nonebot_plugin_sns_subscribe.models import Platform, ContentKind
    from nonebot_plugin_sns_subscribe.services import infer_kind

    profile = "https://www.douyin.com/user/MS4wLjABAAAAxcUzPARSihV4pISuTPyH01LuEG5eBAjcIHRWazheZww"
    assert infer_kind(Platform.DOUYIN, profile) is ContentKind.VIDEO
    assert (
        infer_kind(Platform.DOUYIN, "https://v.douyin.com/abc123/")
        is ContentKind.VIDEO
    )
    assert (
        infer_kind(Platform.DOUYIN, "https://www.douyin.com/video/7300000000000000000")
        is ContentKind.VIDEO
    )
    assert (
        infer_kind(Platform.DOUYIN, "https://live.douyin.com/123456")
        is ContentKind.LIVE
    )


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("BV1xx411c7mD", "video"),
        ("https://www.bilibili.com/video/BV1xx411c7mD", "video"),
        ("https://t.bilibili.com/123456789", "dynamic"),
        ("https://www.bilibili.com/opus/987654321", "dynamic"),
        ("https://www.bilibili.com/read/cv123", "article"),
        ("https://www.bilibili.com/audio/au123", "music"),
        ("https://live.bilibili.com/12345", "live"),
    ],
)
def test_infer_kind_for_bilibili(identifier, expected):
    from nonebot_plugin_sns_subscribe.models import Platform
    from nonebot_plugin_sns_subscribe.services import infer_kind

    assert infer_kind(Platform.BILIBILI, identifier).value == expected


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("BV1xx411c7mD", "BV1xx411c7mD"),
        ("https://www.bilibili.com/video/BV1xx411c7mD/?spm=1", "BV1xx411c7mD"),
        ("https://live.bilibili.com/12345?broadcast_type=0", "12345"),
        ("https://t.bilibili.com/987654321", "987654321"),
        ("https://www.bilibili.com/video/av123456", "123456"),
        ("12345", "12345"),
    ],
)
def test_normalize_bilibili_identifier(identifier, expected):
    from nonebot_plugin_sns_subscribe.services import (
        normalize_bilibili_identifier,
    )

    assert normalize_bilibili_identifier(identifier) == expected
