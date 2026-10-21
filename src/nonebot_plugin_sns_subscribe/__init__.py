"""B 站 / 抖音订阅推送插件。

统一订阅两类平台的作者：B 站推送动态/视频/直播，抖音推送作品与开播；
Web 管理后台支持在线编辑渲染模板、上传字体、配置配色与扫码登录。
"""

from __future__ import annotations

from nonebot import logger, get_driver
from nonebot.plugin import PluginMetadata

from .web import install_web, ensure_web_token
from .jobs import install_jobs, shutdown_jobs, load_runtime_config
from .paths import DATA_DIR
from .config import Config, plugin_config
from .sources import douyin_source, bilibili_source
from .database import repository
from .renderer import renderer, has_cjk_font

__plugin_meta__ = PluginMetadata(
    name="B站/抖音订阅推送",
    description=(
        "统一订阅 B 站与抖音作者：B 站推送动态、视频与直播，抖音推送作品与开播；"
        "支持 Web 后台在线编辑卡片模板、上传字体、配置配色、扫码登录与手动推送。"
    ),
    usage=(
        "发送 /sub help 查看完整帮助。\n"
        "常用命令：\n"
        "/sub add bili <UID|主页链接>       订阅 B 站 UP 主\n"
        "/sub add douyin <主页链接>          订阅抖音作者\n"
        "/sub list                           查看本群订阅\n"
        "/sub push bili video <BV号>         手动推送\n"
        "/sub login bilibili                 扫码登录 B 站（超级用户）"
    ),
    type="application",
    config=Config,
    homepage="https://github.com/mengbingnaixi/nonebot-plugin-bilibili",
    supported_adapters={"~onebot.v11"},
    extra={"author": "nb-bot"},
)

install_web()

driver = get_driver()


@driver.on_startup
async def _startup() -> None:
    await repository.initialize()
    await _seed_credentials()
    await load_runtime_config()
    if not await renderer.prepare():
        logger.warning(
            "SNS subscription: Chromium 不可用，卡片渲染将回退为文本；"
            "请执行 `playwright install chromium`"
        )
    elif not await has_cjk_font():
        logger.warning(
            "SNS subscription: 系统中没有中文字体，卡片里的中文会显示为空白；"
            "请在 Web 后台「资源」上传中文字体并在渲染方案中选用，"
            "或安装 fonts-noto-cjk"
        )
    install_jobs()
    if not await bilibili_source.has_cookie():
        logger.warning(
            "SNS subscription: 尚未配置 B 站 Cookie，动态轮询会跳过；"
            "请在 Web 后台（{}/）扫码登录",
            plugin_config.sns_web_path,
        )
    if plugin_config.sns_web_enabled:
        token = await ensure_web_token()
        logger.warning(
            "SNS subscription: Web 管理后台 -> {}/  管理令牌：{}  （数据目录：{}）",
            plugin_config.sns_web_path,
            token,
            DATA_DIR,
        )


async def _seed_credentials() -> None:
    """首次启动时把配置里的 Cookie 写入数据库（两个平台各自独立判断）。"""

    bilibili = plugin_config.sns_bilibili_cookie
    if (
        bilibili is not None
        and bilibili.get_secret_value().strip()
        and not await repository.get_setting("bilibili_cookie")
    ):
        await repository.set_setting(
            "bilibili_cookie", bilibili.get_secret_value().strip()
        )
    douyin = plugin_config.sns_douyin_cookie
    if (
        douyin is not None
        and douyin.get_secret_value().strip()
        and not await repository.get_setting("douyin_cookie")
    ):
        await repository.set_setting("douyin_cookie", douyin.get_secret_value().strip())


@driver.on_shutdown
async def _shutdown() -> None:
    await shutdown_jobs()
    await renderer.close()
    await bilibili_source.close()
    await douyin_source.close()


# 导入即注册 /sub 命令
from . import commands as commands

__all__ = ["Config", "commands", "plugin_config", "repository"]
