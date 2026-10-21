"""数据目录与资源目录布局。"""

from __future__ import annotations

from pathlib import Path

from .config import plugin_config

PLUGIN_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = PLUGIN_DIR / "resources"
BUILTIN_TEMPLATE_DIR = RESOURCE_DIR / "templates"


def _resolve_data_dir(configured: Path | None) -> Path:
    if configured is None:
        return (Path.cwd() / "data" / "nonebot_plugin_sns_subscribe").resolve()
    path = configured.expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


DATA_DIR: Path = _resolve_data_dir(plugin_config.sns_data)
DATABASE_PATH = DATA_DIR / "subscription.sqlite3"
TEMPLATE_DIR = DATA_DIR / "templates"
FONT_DIR = DATA_DIR / "fonts"
RENDER_DIR = DATA_DIR / "renders"
BROWSER_PROFILE_DIR = DATA_DIR / "browser-profile"


def ensure_data_dirs() -> None:
    for directory in (
        DATA_DIR,
        TEMPLATE_DIR,
        FONT_DIR,
        RENDER_DIR,
        BROWSER_PROFILE_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def builtin_template_path(platform: str, kind: str) -> Path:
    return BUILTIN_TEMPLATE_DIR / platform / f"{kind}.html"


def custom_template_dir(platform: str, kind: str) -> Path:
    return TEMPLATE_DIR / platform / kind


def custom_template_path(platform: str, kind: str, name: str) -> Path:
    return custom_template_dir(platform, kind) / Path(name).name


def safe_asset_path(directory: Path, name: str | None) -> Path | None:
    """Resolve ``name`` inside ``directory`` and reject path traversal."""

    if not name:
        return None
    path = (directory / Path(name).name).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        return None
    return path


ensure_data_dirs()
