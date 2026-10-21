"""浏览器启动策略：优先系统 Chrome / Edge，回退 Playwright Chromium。

B 站卡片渲染与抖音抓取共用同一套浏览器选择：

1. ``SNS_BROWSER_EXECUTABLE`` / ``SNS_DOUYIN_BROWSER_EXECUTABLE``
   （显式路径，优先级最高）
2. ``SNS_BROWSER_CHANNEL`` / ``SNS_DOUYIN_BROWSER_CHANNEL``（``chrome`` / ``msedge``）
3. Windows 下自动探测已安装的 Chrome / Edge（``SNS_BROWSER_AUTO_DETECT``）
4. Playwright 自带 Chromium（缺失时按需安装）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from dataclasses import dataclass

from .config import DEFAULT_USER_AGENT, plugin_config

WINDOWS_CHROME_PATHS = (
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
)


class BrowserUnavailableError(RuntimeError):
    """所有候选浏览器都无法启动。"""


WINDOWS_EDGE_PATHS = (
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe",
)


@dataclass(frozen=True, slots=True)
class BrowserCandidate:
    """一次浏览器启动尝试。"""

    label: str
    channel: str = ""
    executable_path: str = ""

    @property
    def uses_system_browser(self) -> bool:
        return bool(self.channel or self.executable_path)

    def launch_kwargs(self) -> dict[str, str]:
        kwargs: dict[str, str] = {}
        if self.executable_path:
            kwargs["executable_path"] = self.executable_path
        elif self.channel:
            kwargs["channel"] = self.channel
        return kwargs


def _first_existing(paths: tuple[str, ...]) -> str:
    for raw in paths:
        expanded = Path(os.path.expandvars(raw)).expanduser()
        try:
            if expanded.is_file():
                return str(expanded)
        except OSError:
            continue
    return ""


def windows_browser_candidates() -> list[BrowserCandidate]:
    """探测 Windows 上已安装的 Chrome / Edge。"""

    if not sys.platform.startswith("win"):
        return []
    candidates: list[BrowserCandidate] = []
    chrome = _first_existing(WINDOWS_CHROME_PATHS)
    if chrome:
        candidates.append(BrowserCandidate("系统 Chrome", executable_path=chrome))
    edge = _first_existing(WINDOWS_EDGE_PATHS)
    if edge:
        candidates.append(BrowserCandidate("系统 Edge", executable_path=edge))
    return candidates


def configured_channel() -> str:
    """``SNS_BROWSER_CHANNEL`` 优先，兼容旧的 ``SNS_DOUYIN_BROWSER_CHANNEL``。"""

    return (
        plugin_config.sns_browser_channel.strip()
        or plugin_config.sns_douyin_browser_channel.strip()
    )


def configured_executable() -> Path | None:
    return plugin_config.sns_browser_executable or (
        plugin_config.sns_douyin_browser_executable
    )


def browser_candidates() -> list[BrowserCandidate]:
    """按优先级返回所有可尝试的浏览器。"""

    candidates: list[BrowserCandidate] = []
    executable = configured_executable()
    if executable is not None:
        path = executable.expanduser()
        candidates.append(
            BrowserCandidate(f"自定义浏览器 {path.name}", executable_path=str(path))
        )
    channel = configured_channel()
    if channel:
        candidates.append(BrowserCandidate(f"浏览器通道 {channel}", channel=channel))
    if (
        not candidates
        and plugin_config.sns_browser_auto_detect
        and sys.platform.startswith("win")
    ):
        candidates.extend(windows_browser_candidates())
    candidates.append(BrowserCandidate("Playwright Chromium"))

    unique: list[BrowserCandidate] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate.channel, candidate.executable_path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def describe_candidates() -> list[str]:
    return [candidate.label for candidate in browser_candidates()]


def configured_label() -> str:
    """描述当前配置会优先使用哪个浏览器。"""

    executable = configured_executable()
    if executable is not None:
        return f"自定义浏览器 {Path(executable).name}"
    channel = configured_channel()
    if channel:
        return f"浏览器通道 {channel}"
    if plugin_config.sns_browser_auto_detect:
        detected = [candidate.label for candidate in windows_browser_candidates()]
        if detected:
            return "自动探测：" + "、".join(detected)
    return "Playwright Chromium（未配置系统浏览器时）"


def browser_user_agent() -> str:
    """仅在用户显式改过 UA 时才覆盖，避免与真实 Chrome/Edge 版本冲突。"""

    value = plugin_config.sns_user_agent.strip()
    return "" if value == DEFAULT_USER_AGENT else value
