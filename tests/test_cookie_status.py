def make_text(has_bilibili: bool, has_douyin: bool) -> str:
    from nonebot_plugin_sns_subscribe import cookie_status_text

    return cookie_status_text(has_bilibili, has_douyin, "/sns-subscribe")


def test_both_cookies_configured():
    text = make_text(True, True)

    assert "B 站与抖音 Cookie 均已配置" in text
    assert "尚未配置" not in text


def test_both_cookies_missing():
    text = make_text(False, False)

    assert "尚未配置 B 站与抖音 Cookie" in text
    assert "都会跳过" in text
    assert "/sns-subscribe/" in text


def test_only_douyin_configured():
    text = make_text(False, True)

    assert "尚未配置 B 站 Cookie" in text
    assert "抖音 Cookie 已配置" in text
    assert "/sns-subscribe/" in text


def test_only_bilibili_configured():
    text = make_text(True, False)

    assert "尚未配置抖音 Cookie" in text
    assert "B 站 Cookie 已配置" in text
    assert "/sns-subscribe/" in text


def test_single_missing_message_is_specific():
    for text in (make_text(False, True), make_text(True, False)):
        assert "B 站与抖音 Cookie" not in text
