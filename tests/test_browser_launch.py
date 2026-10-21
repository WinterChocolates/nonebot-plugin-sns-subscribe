def _clear_browser_settings(monkeypatch, browser_launch):
    monkeypatch.setattr(browser_launch.plugin_config, "sns_browser_channel", "")
    monkeypatch.setattr(browser_launch.plugin_config, "sns_douyin_browser_channel", "")
    monkeypatch.setattr(browser_launch.plugin_config, "sns_browser_executable", None)
    monkeypatch.setattr(
        browser_launch.plugin_config, "sns_douyin_browser_executable", None
    )


def test_channel_candidate(monkeypatch):
    from nonebot_plugin_sns_subscribe import browser_launch

    _clear_browser_settings(monkeypatch, browser_launch)
    monkeypatch.setattr(browser_launch.plugin_config, "sns_browser_channel", "chrome")

    candidates = browser_launch.browser_candidates()

    assert candidates[0].channel == "chrome"
    assert candidates[0].launch_kwargs() == {"channel": "chrome"}
    assert candidates[-1].label == "Playwright Chromium"
    assert candidates[-1].uses_system_browser is False
    assert browser_launch.configured_label() == "浏览器通道 chrome"


def test_douyin_alias_is_shared(monkeypatch):
    from nonebot_plugin_sns_subscribe import browser_launch

    _clear_browser_settings(monkeypatch, browser_launch)
    monkeypatch.setattr(
        browser_launch.plugin_config, "sns_douyin_browser_channel", "msedge"
    )

    candidates = browser_launch.browser_candidates()

    assert candidates[0].channel == "msedge"
    assert candidates[0].launch_kwargs() == {"channel": "msedge"}


def test_executable_outranks_channel(monkeypatch, tmp_path):
    from nonebot_plugin_sns_subscribe import browser_launch

    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")

    _clear_browser_settings(monkeypatch, browser_launch)
    monkeypatch.setattr(browser_launch.plugin_config, "sns_browser_channel", "chrome")
    monkeypatch.setattr(
        browser_launch.plugin_config, "sns_browser_executable", executable
    )

    candidates = browser_launch.browser_candidates()

    assert candidates[0].launch_kwargs() == {"executable_path": str(executable)}
    assert browser_launch.configured_label().startswith("自定义浏览器")


def test_auto_detect_disabled(monkeypatch):
    from nonebot_plugin_sns_subscribe import browser_launch

    _clear_browser_settings(monkeypatch, browser_launch)
    monkeypatch.setattr(browser_launch.plugin_config, "sns_browser_auto_detect", False)

    assert [item.label for item in browser_launch.browser_candidates()] == [
        "Playwright Chromium"
    ]
    assert (
        browser_launch.configured_label() == "Playwright Chromium（未配置系统浏览器时）"
    )


def test_windows_detection(monkeypatch, tmp_path):
    from nonebot_plugin_sns_subscribe import browser_launch

    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"")
    edge = tmp_path / "msedge.exe"
    edge.write_bytes(b"")

    _clear_browser_settings(monkeypatch, browser_launch)
    monkeypatch.setattr(browser_launch.plugin_config, "sns_browser_auto_detect", True)
    monkeypatch.setattr(browser_launch.sys, "platform", "win32")
    monkeypatch.setattr(browser_launch, "WINDOWS_CHROME_PATHS", (str(chrome),))
    monkeypatch.setattr(browser_launch, "WINDOWS_EDGE_PATHS", (str(edge),))

    labels = [item.label for item in browser_launch.browser_candidates()]

    assert labels == ["系统 Chrome", "系统 Edge", "Playwright Chromium"]
    assert browser_launch.configured_label().startswith("自动探测")


def test_browser_user_agent_only_when_customized(monkeypatch):
    from nonebot_plugin_sns_subscribe import browser_launch
    from nonebot_plugin_sns_subscribe.config import DEFAULT_USER_AGENT

    monkeypatch.setattr(
        browser_launch.plugin_config, "sns_user_agent", DEFAULT_USER_AGENT
    )
    assert browser_launch.browser_user_agent() == ""

    monkeypatch.setattr(browser_launch.plugin_config, "sns_user_agent", "CustomUA/1.0")
    assert browser_launch.browser_user_agent() == "CustomUA/1.0"
