"""插件配置项（统一前缀 ``SNS_``，避免污染 NoneBot 全局配置）。"""

from __future__ import annotations

from pathlib import Path

from nonebot import get_plugin_config
from pydantic import Field, BaseModel, SecretStr

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class Config(BaseModel):
    """B 站 / 抖音订阅推送插件配置。"""

    sns_data: Path | None = Field(
        default=None,
        description="插件数据目录（数据库、模板、字体、浏览器用户目录）",
    )
    # ---- Web 管理后台 ----
    sns_web_enabled: bool = Field(default=True, description="是否启用 Web 管理后台")
    sns_web_path: str = Field(default="/sns-subscribe", description="Web 后台挂载路径")
    sns_web_token: str = Field(default="", description="管理令牌；留空则自动生成")
    sns_web_public_url: str = Field(
        default="", description="对外访问地址，用于命令中提示"
    )
    # ---- 轮询开关与间隔 ----
    sns_enable_bilibili_dynamic: bool = True
    sns_enable_bilibili_live: bool = True
    sns_enable_douyin: bool = True
    sns_bilibili_dynamic_interval: int = Field(default=120, ge=30, le=86400)
    sns_bilibili_live_interval: int = Field(default=30, ge=10, le=86400)
    sns_douyin_interval: int = Field(default=180, ge=60, le=86400)
    sns_douyin_account_delay: int = Field(
        default=12,
        ge=0,
        le=300,
        description="抖音每个作者之间的随机等待秒数基数",
    )
    # ---- 网络 ----
    sns_request_timeout: float = Field(default=15.0, gt=0)
    sns_request_concurrency: int = Field(default=4, ge=1, le=20)
    sns_user_agent: str = DEFAULT_USER_AGENT
    # ---- 渲染 ----
    sns_render_width: int = Field(default=720, ge=480, le=1600)
    sns_render_timeout: int = Field(default=20000, ge=3000, le=60000)
    sns_auto_install_browser: bool = Field(
        default=True,
        description="内置 Chromium 缺失时自动执行 playwright install chromium",
    )
    # ---- 行为 ----
    sns_initial_post_limit: int = Field(default=3, ge=1, le=20)
    sns_notify_live_end: bool = Field(default=False, description="是否推送下播通知")
    # ---- 初始凭据（也可在 Web 后台 / 命令中写入数据库）----
    sns_bilibili_cookie: SecretStr | None = None
    sns_douyin_cookie: SecretStr | None = None
    # ---- 浏览器（B站卡片渲染与抖音抓取共用）----
    sns_browser_channel: str = Field(
        default="",
        description="已安装的浏览器通道：chrome / msedge；留空则 Windows 自动探测",
    )
    sns_browser_executable: Path | None = Field(
        default=None, description="浏览器可执行文件路径，优先级高于 channel"
    )
    sns_browser_auto_detect: bool = Field(
        default=True, description="Windows 下自动发现系统 Chrome / Edge"
    )
    # ---- 抖音浏览器 ----
    sns_douyin_page_timeout: int = Field(default=45000, ge=5000, le=120000)
    sns_douyin_page_settle_ms: int = Field(default=2500, ge=500, le=15000)
    sns_douyin_operation_timeout: int = Field(default=90, ge=30, le=300)
    sns_douyin_login_timeout: int = Field(default=900, ge=60, le=3600)
    sns_douyin_headless: bool = Field(
        default=True,
        description="抖音浏览器是否无界面运行；桌面 Windows 上关闭可降低风控概率",
    )
    sns_douyin_browser_channel: str = Field(
        default="",
        description="旧配置名，等同 SNS_BROWSER_CHANNEL（两个平台共用）",
    )
    sns_douyin_browser_executable: Path | None = Field(
        default=None, description="旧配置名，等同 SNS_BROWSER_EXECUTABLE"
    )
    sns_douyin_browser_restart_interval: int = Field(
        default=6 * 60 * 60,
        ge=30 * 60,
        le=7 * 24 * 60 * 60,
        description="定期清理缓存并重启 Chromium 的间隔秒数",
    )


plugin_config = get_plugin_config(Config)
