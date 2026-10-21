"""渲染资源选择：把群配置映射成模板 / 字体 / 配色。"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable, Awaitable

from .paths import FONT_DIR, safe_asset_path, custom_template_path
from .models import PushContent, RenderSelection
from .database import repository
from .renderer import render_kind

Selector = Callable[[int, PushContent], Awaitable[Path | None]]


def _custom_template(platform: str, kind: str, name: str | None) -> Path | None:
    if not name:
        return None
    path = custom_template_path(platform, kind, name)
    return path if path.is_file() else None


async def select_render_assets(group_id: int, content: PushContent) -> RenderSelection:
    kind = render_kind(content.platform, content.kind)
    template_name, font_name, color, gradient = await repository.resolve_override(
        content.platform, group_id, kind
    )
    template_path = _custom_template(content.platform.value, kind, template_name)
    font_path = safe_asset_path(FONT_DIR, font_name)
    return RenderSelection(
        template_path=str(template_path) if template_path else None,
        font_path=str(font_path) if font_path else None,
        accent_color=color,
        gradient_color=gradient,
    )
