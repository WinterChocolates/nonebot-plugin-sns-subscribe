"""Web 管理后台：订阅管理、渲染方案、模板在线编辑、登录、手动推送。"""

from __future__ import annotations

import base64
import asyncio
import secrets
from hmac import compare_digest
from typing import Any, Annotated
from pathlib import Path

from fastapi import (
    File,
    Form,
    Depends,
    Request,
    APIRouter,
    UploadFile,
    HTTPException,
)
from nonebot import logger, get_app, get_bots
from pydantic import Field, BaseModel, field_validator
from fastapi.responses import Response, HTMLResponse, JSONResponse, RedirectResponse
from nonebot.adapters.onebot.v11 import Bot

from .paths import (
    DATA_DIR,
    FONT_DIR,
    RESOURCE_DIR,
    safe_asset_path,
    ensure_data_dirs,
    custom_template_dir,
    custom_template_path,
    builtin_template_path,
)
from .config import plugin_config
from .models import Platform, AtAllKind, ContentKind, RenderSelection
from .sources import SourceError, douyin_source, bilibili_source
from .database import repository
from .pipeline import push_to_group
from .renderer import (
    CUSTOMIZABLE_KINDS,
    renderer,
    render_kind,
    validate_template_source,
)
from .services import (
    subscribe,
    make_qr_png,
    mask_cookie,
    save_cookie,
    unsubscribe,
    manual_content,
    preview_content,
)
from .browser_launch import configured_label
from .sources.douyin import DouyinLoginState, DouyinLoginSession

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# 中文字体（思源黑体、微软雅黑等）体积较大，单独放宽上限
MAX_FONT_BYTES = 32 * 1024 * 1024
MAX_TEMPLATE_BYTES = 512 * 1024
AUTH_COOKIE = "sns_subscription_token"
AUTH_MAX_AGE = 7 * 24 * 60 * 60
FONT_EXTENSIONS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
TEMPLATE_EXTENSIONS = {".html", ".htm"}
LOGIN_STATE_MESSAGES = {
    DouyinLoginState.WAITING: "等待扫码 / 验证完成",
    DouyinLoginState.SUCCESS: "抖音登录成功",
    DouyinLoginState.EXPIRED: "登录会话已过期，请重新发起",
    DouyinLoginState.VERIFICATION: "需要在页面中完成安全验证",
}


# --------------------------------------------------------------------- 请求体
class SessionBody(BaseModel):
    token: str


class SubscriptionBody(BaseModel):
    platform: Platform
    group_id: int = Field(gt=0)
    target: str = Field(min_length=1, max_length=512)
    group_name: str = ""


class FilterBody(BaseModel):
    group_id: int = Field(gt=0)
    pattern: str


class AtAllBody(BaseModel):
    platform: Platform
    group_id: int = Field(gt=0)
    author_id: str = Field(min_length=1, max_length=256)
    kind: str
    enabled: bool


class OverrideBody(BaseModel):
    platform: Platform
    group_id: int = Field(gt=0)
    kind: str
    template_name: str | None = None
    font_name: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    gradient_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class RuntimeBody(BaseModel):
    enable_bilibili_dynamic: bool
    enable_bilibili_live: bool
    enable_douyin: bool
    notify_live_end: bool
    bilibili_dynamic_interval: int = Field(ge=30, le=86400)
    bilibili_live_interval: int = Field(ge=10, le=86400)
    douyin_interval: int = Field(ge=60, le=86400)


class ManualPushBody(BaseModel):
    platform: Platform
    group_id: int = Field(gt=0)
    kind: str
    identifier: str = Field(default="", max_length=512)


class PreviewBody(BaseModel):
    platform: Platform
    kind: str
    identifier: str = Field(default="", max_length=512)
    template_name: str | None = None
    font_name: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    gradient_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    source: str | None = Field(
        default=None,
        max_length=600_000,
        description="编辑器中的模板源码；传入时优先于 template_name",
    )


class TemplateSourceBody(BaseModel):
    platform: Platform
    kind: str
    name: str = Field(min_length=1, max_length=128)
    content: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        name = Path(value).name
        if name != value or not name:
            raise ValueError("模板文件名不合法")
        return name


class CookieBody(BaseModel):
    platform: Platform
    cookie: str = ""


class DouyinVerifyBody(BaseModel):
    target: str = Field(default="", max_length=512)


class DouyinLoginActionBody(BaseModel):
    key: str
    action: str
    x: float | None = None
    y: float | None = None
    text: str = ""
    key_name: str = ""


# --------------------------------------------------------------------- 鉴权
async def ensure_web_token() -> str:
    configured = plugin_config.sns_web_token.strip()
    if configured:
        return configured
    token = await repository.get_setting("web_token")
    if not token:
        token = secrets.token_urlsafe(24)
        await repository.set_setting("web_token", token)
    return token


async def _is_authorized(request: Request) -> bool:
    provided = (
        request.headers.get("x-sns-token")
        or request.query_params.get("token")
        or request.cookies.get(AUTH_COOKIE)
        or ""
    )
    expected = await ensure_web_token()
    return bool(provided) and compare_digest(provided, expected)


async def require_token(request: Request) -> None:
    if not await _is_authorized(request):
        raise HTTPException(status_code=401, detail="管理令牌无效或已过期")


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=AUTH_MAX_AGE,
        httponly=True,
        samesite="strict",
        path=plugin_config.sns_web_path,
    )


def _page(name: str) -> str:
    return (RESOURCE_DIR / name).read_text(encoding="utf-8")


def _image_data(image: bytes) -> str:
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:image/png;base64,{encoded}"


# --------------------------------------------------------------------- 资源
def _validate_kind(platform: Platform, kind: str) -> str:
    if kind not in CUSTOMIZABLE_KINDS[platform]:
        allowed = "、".join(CUSTOMIZABLE_KINDS[platform])
        raise HTTPException(status_code=400, detail=f"该平台可用模板类型：{allowed}")
    return kind


def _safe_upload_name(filename: str | None, allowed: set[str]) -> str:
    name = Path(filename or "").name
    if not name or Path(name).suffix.lower() not in allowed:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    return name


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


async def _save_upload(
    upload: UploadFile,
    directory: Path,
    allowed: set[str],
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> str:
    name = _safe_upload_name(upload.filename, allowed)
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413, detail=f"文件不能超过 {max_bytes // 1024 // 1024} MiB"
        )
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    await asyncio.to_thread(_write_bytes, directory / name, content)
    return name


def _list_templates() -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    for platform, kinds in CUSTOMIZABLE_KINDS.items():
        result[platform.value] = {}
        for kind in kinds:
            directory = custom_template_dir(platform.value, kind)
            names = (
                sorted(
                    path.name
                    for path in directory.glob("*")
                    if path.is_file() and path.suffix.lower() in TEMPLATE_EXTENSIONS
                )
                if directory.is_dir()
                else []
            )
            result[platform.value][kind] = names
    return result


def _read_template(platform: Platform, kind: str, name: str) -> tuple[str, bool]:
    if name:
        path = custom_template_path(platform.value, kind, name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="模板文件不存在")
        return path.read_text(encoding="utf-8"), True
    builtin = builtin_template_path(platform.value, kind)
    if not builtin.is_file():
        raise HTTPException(status_code=404, detail="内置模板不存在")
    return builtin.read_text(encoding="utf-8"), False


def _template_asset(platform: Platform, kind: str, name: str | None) -> str | None:
    if not name:
        return None
    path = custom_template_path(platform.value, kind, name)
    if not path.is_file():
        raise HTTPException(status_code=400, detail="所选模板不存在")
    return str(path)


def _font_asset(name: str | None) -> str | None:
    path = safe_asset_path(FONT_DIR, name)
    if name and path is None:
        raise HTTPException(status_code=400, detail="所选字体不存在")
    return str(path) if path else None


# --------------------------------------------------------------------- 路由
router = APIRouter(prefix=plugin_config.sns_web_path)
protected = [Depends(require_token)]


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    if await _is_authorized(request):
        return RedirectResponse(f"{plugin_config.sns_web_path}/", status_code=303)
    return HTMLResponse(_page("login.html"))


@router.post("/api/session")
async def create_session(body: SessionBody) -> JSONResponse:
    expected = await ensure_web_token()
    if not body.token or not compare_digest(body.token.strip(), expected):
        raise HTTPException(status_code=401, detail="管理令牌不正确")
    response = JSONResponse({"ok": True})
    _set_auth_cookie(response, expected)
    return response


@router.post("/api/logout", dependencies=protected)
async def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE, path=plugin_config.sns_web_path)
    return response


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    query_token = request.query_params.get("token") or ""
    if query_token:
        expected = await ensure_web_token()
        if compare_digest(query_token, expected):
            response = RedirectResponse(
                f"{plugin_config.sns_web_path}/", status_code=303
            )
            _set_auth_cookie(response, expected)
            return response
    if not await _is_authorized(request):
        return RedirectResponse(f"{plugin_config.sns_web_path}/login", status_code=303)
    return HTMLResponse(_page("web.html"))


# --------------------------------------------------------------------- 状态
@router.get("/api/state", dependencies=protected)
async def api_state() -> dict[str, Any]:
    snapshot = await repository.export_snapshot()
    groups: list[dict[str, Any]] = snapshot["groups"]
    bilibili_cookie = await bilibili_source.has_cookie()
    douyin_cookie = await douyin_source.has_cookie()
    total_subscriptions = sum(len(group["subscriptions"]) for group in groups)
    platforms: dict[str, int] = {Platform.BILIBILI.value: 0, Platform.DOUYIN.value: 0}
    for group in groups:
        for item in group["subscriptions"]:
            platforms[item["platform"]] = platforms.get(item["platform"], 0) + 1
    return {
        "groups": groups,
        "fonts": sorted(path.name for path in FONT_DIR.glob("*") if path.is_file()),
        "templates": _list_templates(),
        "kinds": {key.value: list(value) for key, value in CUSTOMIZABLE_KINDS.items()},
        "cookies": {
            Platform.BILIBILI.value: {
                "configured": bilibili_cookie,
                "hint": mask_cookie(await bilibili_source.cookie_string()),
            },
            Platform.DOUYIN.value: {
                "configured": douyin_cookie,
                "hint": mask_cookie(await douyin_source.cookie_string()),
                "stored_session": await douyin_source.stored_has_session(),
            },
        },
        "config": {
            "enable_bilibili_dynamic": plugin_config.sns_enable_bilibili_dynamic,
            "enable_bilibili_live": plugin_config.sns_enable_bilibili_live,
            "enable_douyin": plugin_config.sns_enable_douyin,
            "notify_live_end": plugin_config.sns_notify_live_end,
            "bilibili_dynamic_interval": plugin_config.sns_bilibili_dynamic_interval,
            "bilibili_live_interval": plugin_config.sns_bilibili_live_interval,
            "douyin_interval": plugin_config.sns_douyin_interval,
            "render_width": plugin_config.sns_render_width,
        },
        "stats": {
            "groups": len(groups),
            "subscriptions": total_subscriptions,
            "bilibili": platforms.get(Platform.BILIBILI.value, 0),
            "douyin": platforms.get(Platform.DOUYIN.value, 0),
            "templates": sum(
                len(names)
                for platform in _list_templates().values()
                for names in platform.values()
            ),
        },
        "browser": {
            "configured": configured_label(),
            "renderer": renderer.browser_label,
            "douyin": douyin_source.browser_label,
        },
        "data_dir": str(DATA_DIR),
        "web_path": plugin_config.sns_web_path,
    }


@router.get("/api/groups/scan", dependencies=protected)
async def scan_groups() -> dict[str, Any]:
    bot = next((item for item in get_bots().values() if isinstance(item, Bot)), None)
    if bot is None:
        raise HTTPException(status_code=503, detail="OneBot V11 尚未连接")
    try:
        result = await bot.get_group_list()
    except Exception as exc:
        logger.exception("SNS subscription: 读取群列表失败")
        raise HTTPException(status_code=502, detail="读取 QQ 群列表失败") from exc
    groups = []
    for item in result:
        group_id = int(item.get("group_id") or 0)
        if group_id <= 0:
            continue
        name = str(item.get("group_name") or f"QQ群 {group_id}")
        avatar = f"https://p.qlogo.cn/gh/{group_id}/{group_id}/100"
        groups.append(
            {
                "group_id": group_id,
                "group_name": name,
                "avatar": avatar,
                "member_count": int(item.get("member_count") or 0),
            }
        )
        await repository.upsert_group(group_id, name, avatar)
    groups.sort(key=lambda entry: str(entry["group_name"]).casefold())
    return {"groups": groups}


# --------------------------------------------------------------------- 订阅
@router.post("/api/subscriptions", dependencies=protected)
async def add_subscription(body: SubscriptionBody) -> dict[str, Any]:
    try:
        profile, added = await subscribe(
            body.platform, body.target.strip(), body.group_id, body.group_name
        )
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "added": added,
        "platform": profile.platform.value,
        "author_id": profile.author_id,
        "author_name": profile.name,
        "avatar": profile.avatar,
    }


@router.delete(
    "/api/subscriptions/{platform}/{group_id}/{author_id:path}", dependencies=protected
)
async def delete_subscription(
    platform: Platform, group_id: int, author_id: str
) -> dict[str, Any]:
    removed = await unsubscribe(platform, group_id, author_id)
    return {"ok": True, "removed": removed}


@router.post("/api/filters", dependencies=protected)
async def add_filter(body: FilterBody) -> dict[str, Any]:
    try:
        added = await repository.add_filter(body.group_id, body.pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "added": added}


@router.delete("/api/filters/{group_id}", dependencies=protected)
async def delete_filter(group_id: int, pattern: str) -> dict[str, Any]:
    return {
        "ok": True,
        "removed": await repository.remove_filter_exact(group_id, pattern),
    }


@router.post("/api/atall", dependencies=protected)
async def set_atall(body: AtAllBody) -> dict[str, Any]:
    if body.kind not in AtAllKind._value2member_map_:
        raise HTTPException(status_code=400, detail="无效的 @全体 类型")
    switches = await repository.set_atall(
        body.platform,
        body.group_id,
        body.author_id,
        AtAllKind(body.kind),
        enabled=body.enabled,
    )
    return {"ok": True, "switches": sorted(switches)}


@router.post("/api/overrides", dependencies=protected)
async def set_override(body: OverrideBody) -> dict[str, Any]:
    kind = _validate_kind(body.platform, body.kind)
    template_name = Path(body.template_name).name if body.template_name else None
    font_name = Path(body.font_name).name if body.font_name else None
    if (
        template_name
        and not custom_template_path(body.platform.value, kind, template_name).is_file()
    ):
        raise HTTPException(status_code=400, detail="所选模板不存在")
    if font_name and safe_asset_path(FONT_DIR, font_name) is None:
        raise HTTPException(status_code=400, detail="所选字体不存在")
    await repository.set_override(
        body.platform,
        body.group_id,
        kind,
        template_name,
        font_name,
        body.color.lower() if body.color else None,
        body.gradient_color.lower() if body.gradient_color else None,
    )
    return {"ok": True}


@router.post("/api/runtime", dependencies=protected)
async def update_runtime(body: RuntimeBody) -> dict[str, Any]:
    from .jobs import save_runtime_config

    await save_runtime_config(
        enable_bilibili_dynamic=body.enable_bilibili_dynamic,
        enable_bilibili_live=body.enable_bilibili_live,
        enable_douyin=body.enable_douyin,
        notify_live_end=body.notify_live_end,
        bilibili_dynamic_interval=body.bilibili_dynamic_interval,
        bilibili_live_interval=body.bilibili_live_interval,
        douyin_interval=body.douyin_interval,
    )
    return {"ok": True}


# --------------------------------------------------------------------- 推送
@router.post("/api/push", dependencies=protected)
async def manual_push(body: ManualPushBody) -> dict[str, Any]:
    try:
        kind = ContentKind(body.kind)
        content = await manual_content(body.platform, kind, body.identifier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="推送类型无效") from exc
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sent = await push_to_group(body.group_id, content, bypass_filters=True)
    if not sent:
        raise HTTPException(
            status_code=502, detail="推送失败，请检查 OneBot 连接与日志"
        )
    return {"ok": True, "title": content.title, "author": content.author_name}


@router.post("/api/preview", dependencies=protected)
async def preview_render(body: PreviewBody) -> dict[str, Any]:
    try:
        kind = ContentKind(body.kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="预览类型无效") from exc
    try:
        content = await preview_content(body.platform, kind, body.identifier.strip())
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    template_kind = _validate_kind(body.platform, render_kind(body.platform, kind))
    selection = RenderSelection(
        template_path=_template_asset(body.platform, template_kind, body.template_name),
        font_path=_font_asset(body.font_name),
        accent_color=body.color.lower() if body.color else None,
        gradient_color=body.gradient_color.lower() if body.gradient_color else None,
    )
    inline_source = body.source
    if inline_source is not None:
        try:
            validate_template_source(inline_source)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        image = await renderer.render(content, selection, inline_source)
    except Exception as exc:
        logger.exception("SNS subscription: 预览渲染失败")
        raise HTTPException(status_code=400, detail=f"预览渲染失败：{exc}") from exc
    return {"ok": True, "image": _image_data(image)}


# --------------------------------------------------------------------- 模板
@router.get("/api/templates", dependencies=protected)
async def list_templates() -> dict[str, Any]:
    return {
        "templates": _list_templates(),
        "kinds": {key.value: list(value) for key, value in CUSTOMIZABLE_KINDS.items()},
    }


@router.get("/api/template/source", dependencies=protected)
async def template_source(
    platform: Platform, kind: str, name: str = ""
) -> dict[str, Any]:
    kind = _validate_kind(platform, kind)
    try:
        content, custom = _read_template(platform, kind, name.strip())
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="模板必须使用 UTF-8 编码") from exc
    return {
        "platform": platform.value,
        "kind": kind,
        "name": name.strip(),
        "custom": custom,
        "content": content,
    }


@router.put("/api/template/source", dependencies=protected)
async def save_template_source(body: TemplateSourceBody) -> dict[str, Any]:
    kind = _validate_kind(body.platform, body.kind)
    name = _safe_upload_name(body.name, TEMPLATE_EXTENSIONS)
    if len(body.content.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail="模板内容不能超过 512 KiB")
    try:
        card_id = validate_template_source(body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = custom_template_path(body.platform.value, kind, name)
    await asyncio.to_thread(_write_bytes, path, body.content.encode("utf-8"))
    return {
        "ok": True,
        "name": name,
        "card_id": card_id,
        "templates": _list_templates(),
    }


@router.delete("/api/template", dependencies=protected)
async def delete_template(platform: Platform, kind: str, name: str) -> dict[str, Any]:
    kind = _validate_kind(platform, kind)
    path = custom_template_path(platform.value, kind, Path(name).name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="模板文件不存在")
    await asyncio.to_thread(path.unlink)
    await repository.clear_template_references(platform, kind, path.name)
    return {"ok": True, "templates": _list_templates()}


@router.post("/api/upload/template", dependencies=protected)
async def upload_template(
    file: Annotated[UploadFile, File()],
    platform: Annotated[str, Form()] = "bilibili",
    kind: Annotated[str, Form()] = "dynamic",
) -> dict[str, Any]:
    try:
        platform_value = Platform(platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="无效的平台") from exc
    kind = _validate_kind(platform_value, kind)
    name = _safe_upload_name(file.filename, TEMPLATE_EXTENSIONS)
    content = await file.read(MAX_TEMPLATE_BYTES + 1)
    if len(content) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail="模板内容不能超过 512 KiB")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="模板必须使用 UTF-8 编码") from exc
    try:
        card_id = validate_template_source(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = custom_template_path(platform_value.value, kind, name)
    await asyncio.to_thread(_write_bytes, path, content)
    return {
        "ok": True,
        "name": name,
        "card_id": card_id,
        "templates": _list_templates(),
    }


@router.post("/api/upload/font", dependencies=protected)
async def upload_font(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    name = await _save_upload(file, FONT_DIR, FONT_EXTENSIONS, MAX_FONT_BYTES)
    return {"ok": True, "name": name}


@router.delete("/api/font", dependencies=protected)
async def delete_font(name: str) -> dict[str, Any]:
    path = safe_asset_path(FONT_DIR, Path(name).name)
    if path is None:
        raise HTTPException(status_code=404, detail="字体文件不存在")
    await asyncio.to_thread(path.unlink)
    await repository.clear_font_references(path.name)
    return {"ok": True}


# --------------------------------------------------------------------- 登录
@router.post("/api/cookie", dependencies=protected)
async def set_cookie(body: CookieBody) -> dict[str, Any]:
    return {"ok": True, **(await save_cookie(body.platform, body.cookie))}


@router.post("/api/login/bilibili/qr", dependencies=protected)
async def bilibili_login_qr() -> dict[str, Any]:
    try:
        key, url = await bilibili_source.create_qr_login()
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not key or not url:
        raise HTTPException(status_code=502, detail="B 站未返回登录二维码")
    return {"key": key, "image": _image_data(make_qr_png(url))}


@router.get("/api/login/bilibili/poll", dependencies=protected)
async def bilibili_login_poll(key: str) -> dict[str, Any]:
    try:
        code, message, cookie = await bilibili_source.poll_qr_login(key)
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if code == 0 and cookie:
        await repository.set_setting("bilibili_cookie", cookie)
        return {"state": "success", "message": "登录成功，Cookie 已保存"}
    if code == 86038:
        return {"state": "expired", "message": "二维码已失效，请重新生成"}
    if code == 86101:
        return {"state": "waiting", "message": "等待扫码"}
    if code == 86090:
        return {"state": "scanned", "message": "已扫码，请在手机上确认"}
    return {"state": "pending", "message": message or f"状态码 {code}"}


def _login_payload(session: DouyinLoginSession) -> dict[str, Any]:
    return {
        "key": session.key,
        "state": session.state.value,
        "message": LOGIN_STATE_MESSAGES.get(session.state, ""),
        "url": session.url,
        "image": _image_data(session.image) if session.image else "",
    }


@router.post("/api/login/douyin/start", dependencies=protected)
async def douyin_login_start() -> dict[str, Any]:
    try:
        session = await douyin_source.begin_login()
    except SourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("SNS subscription: 抖音登录页打开失败")
        raise HTTPException(
            status_code=502, detail=f"打开抖音登录页失败：{exc}"
        ) from exc
    return _login_payload(session)


@router.get("/api/login/douyin/status", dependencies=protected)
async def douyin_login_status(key: str) -> dict[str, Any]:
    try:
        session = await douyin_source.login_status(key)
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _login_payload(session)


@router.post("/api/login/douyin/action", dependencies=protected)
async def douyin_login_action(body: DouyinLoginActionBody) -> dict[str, Any]:
    try:
        session = await douyin_source.login_action(
            body.key,
            body.action,
            x=body.x,
            y=body.y,
            text=body.text,
            key_name=body.key_name,
        )
    except SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _login_payload(session)


@router.post("/api/login/douyin/save", dependencies=protected)
async def douyin_login_save() -> dict[str, Any]:
    raw = await douyin_source.save_cookies()
    return {"ok": True, "hint": mask_cookie(raw)}


@router.post("/api/login/douyin/verify", dependencies=protected)
async def douyin_login_verify(body: DouyinVerifyBody | None = None) -> dict[str, Any]:
    target = body.target.strip() if body else ""
    if not target:
        subscriptions = await repository.list_subscriptions(platform=Platform.DOUYIN)
        if subscriptions:
            target = subscriptions[0].author_id
    try:
        result = await douyin_source.verify_session(target)
    except Exception as exc:
        logger.exception("SNS subscription: 抖音登录态检测失败")
        raise HTTPException(status_code=502, detail=f"登录态检测失败：{exc}") from exc
    return {"ok": True, **result}


@router.delete("/api/login/douyin/{key}", dependencies=protected)
async def douyin_login_stop(key: str) -> dict[str, Any]:
    await douyin_source.end_login(key)
    return {"ok": True}


# --------------------------------------------------------------------- 安装
_INSTALL_STATE: dict[str, bool] = {"installed": False}


def install_web() -> None:
    if _INSTALL_STATE["installed"] or not plugin_config.sns_web_enabled:
        return
    ensure_data_dirs()
    try:
        get_app().include_router(router)
    except Exception:  # 非 FastAPI 驱动时降级
        logger.exception("SNS subscription: Web 后台需要 NoneBot 的 FastAPI 驱动")
        return
    _INSTALL_STATE["installed"] = True
    logger.info(
        "SNS subscription: Web 管理后台已启用 -> {}/",
        plugin_config.sns_web_path,
    )


__all__ = ["ensure_web_token", "install_web", "router"]
