import pytest
from pydantic import ValidationError


def test_config_defaults():
    from nonebot_plugin_sns_subscribe.config import Config

    config = Config()

    assert config.sns_web_path == "/sns-subscribe"
    assert config.sns_enable_bilibili_dynamic is True
    assert config.sns_enable_douyin is True
    assert config.sns_browser_auto_detect is True
    assert config.sns_douyin_headless is True
    assert config.sns_initial_post_limit == 3


def test_config_rejects_invalid_intervals():
    from nonebot_plugin_sns_subscribe.config import Config

    with pytest.raises(ValidationError):
        Config(sns_bilibili_dynamic_interval=5)

    with pytest.raises(ValidationError):
        Config(sns_request_concurrency=0)


def test_config_accepts_runtime_overrides():
    from nonebot_plugin_sns_subscribe.config import Config

    config = Config(
        sns_browser_channel="chrome",
        sns_render_width=900,
        sns_initial_post_limit=5,
    )

    assert config.sns_browser_channel == "chrome"
    assert config.sns_render_width == 900
    assert config.sns_initial_post_limit == 5
