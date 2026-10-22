# 配置项详解

所有配置项都写在 NoneBot 的 `.env` / `.env.prod` 里，前缀统一为 `SNS_`。
最少只需要配置凭据（也可以留空，之后在 Web 后台扫码登录）。

## 完整示例

```dotenv
# 数据目录（默认 ./data/nonebot_plugin_sns_subscribe）
SNS_DATA=./data/nonebot_plugin_sns_subscribe

# ---- Web 后台 ----
SNS_WEB_ENABLED=true
SNS_WEB_PATH=/sns-subscribe
SNS_WEB_TOKEN=            # 留空则自动生成并保存到数据库
SNS_WEB_PUBLIC_URL=       # 例如 https://bot.example.com，供命令提示使用

# ---- 轮询开关与间隔（秒）----
SNS_ENABLE_BILIBILI_DYNAMIC=true
SNS_ENABLE_BILIBILI_LIVE=true
SNS_ENABLE_DOUYIN=true
SNS_BILIBILI_DYNAMIC_INTERVAL=120
SNS_BILIBILI_LIVE_INTERVAL=30
SNS_DOUYIN_INTERVAL=180
SNS_DOUYIN_ACCOUNT_DELAY=12

# ---- 初始凭据（也可留空，之后在 Web 后台扫码登录 / 粘贴 Cookie）----
SNS_BILIBILI_COOKIE=
SNS_DOUYIN_COOKIE=

# ---- 行为 ----
SNS_INITIAL_POST_LIMIT=3      # 单轮最多补推多少条历史内容
SNS_NOTIFY_LIVE_END=false     # 是否推送下播通知
SNS_BILIBILI_FORWARD=true     # B 站推送图片后再发合并转发（内容为链接）
SNS_DOUYIN_FORWARD=true       # 抖音推送图片后再发合并转发（内容为链接）
SNS_AUTO_INSTALL_BROWSER=true

# ---- 渲染 ----
SNS_RENDER_WIDTH=720
SNS_RENDER_TIMEOUT=20000

# ---- 浏览器（卡片渲染 + 抖音抓取共用）----
SNS_BROWSER_CHANNEL=             # chrome / msedge；留空则 Windows 自动探测
SNS_BROWSER_EXECUTABLE=          # 自定义浏览器路径，优先级最高
SNS_BROWSER_AUTO_DETECT=true     # Windows 下自动发现 Chrome / Edge
# 兼容旧名字（两个平台同样会读取）：
# SNS_DOUYIN_BROWSER_CHANNEL=chrome
# SNS_DOUYIN_BROWSER_EXECUTABLE=

# ---- 抖音页面参数 ----
SNS_DOUYIN_PAGE_TIMEOUT=45000
SNS_DOUYIN_PAGE_SETTLE_MS=2500
SNS_DOUYIN_OPERATION_TIMEOUT=90
SNS_DOUYIN_LOGIN_TIMEOUT=900
SNS_DOUYIN_HEADLESS=true          # 桌面 Windows 上设为 false 可降低风控概率
SNS_DOUYIN_BROWSER_RESTART_INTERVAL=21600
```

轮询间隔、开关、下播通知也可以在 Web 后台「运行设置」里改，改动会持久化到数据库并立即生效。

## 浏览器选择（卡片渲染 + 抖音共用）

卡片截图（B 站/抖音）与抖音数据抓取使用同一套浏览器设置，按以下顺序尝试：

1. `SNS_BROWSER_EXECUTABLE` / `SNS_DOUYIN_BROWSER_EXECUTABLE`（显式可执行文件，优先级最高）
2. `SNS_BROWSER_CHANNEL` / `SNS_DOUYIN_BROWSER_CHANNEL`：`chrome` 或 `msedge`
3. Windows 下自动探测已安装的 Chrome 或 Edge（`SNS_BROWSER_AUTO_DETECT=true`，默认开启）
4. Playwright 自带 Chromium（缺失时按需自动安装）

因此：

- Windows 上**不需要**额外下载 Chromium，会直接调用系统 Chrome / Edge；
- 写 `SNS_DOUYIN_BROWSER_CHANNEL=chrome` 时 **B 站卡片渲染也会用 Chrome**（旧配置名保留兼容）；
- 想强制使用 Playwright 自带的 Chromium：设置 `SNS_BROWSER_AUTO_DETECT=false` 并清空两个浏览器配置；
- 实际使用的浏览器可以在 Web 后台「运行设置 → 浏览器」查看（配置 / 卡片渲染 / 抖音抓取）。

## 数据目录

```
data/nonebot_plugin_sns_subscribe/
├── subscription.sqlite3        # 订阅、状态、渲染方案、Cookie、投递回执
├── templates/
│   ├── bilibili/{dynamic,video,live}/*.html
│   └── douyin/{video,live}/*.html
├── fonts/                      # 上传的字体
├── renders/                    # 预留
└── browser-profile/            # 抖音登录态（持久化 Chromium 用户目录）
```

数据库中每个业务表都带 `platform` 字段，因此两平台共用一套订阅、过滤、@全体、
渲染方案与去重回执。`data/` 内含 Cookie 与登录态，请勿泄露。

## 启动时的 Cookie 提示

启动日志会检查两个平台：

| B 站 | 抖音 | 日志内容 |
| --- | --- | --- |
| 已配置 | 已配置 | `B 站与抖音 Cookie 均已配置，动态、作品与直播轮询正常`（INFO） |
| 未配置 | 未配置 | `尚未配置 B 站与抖音 Cookie，…都会跳过`（WARNING） |
| 未配置 | 已配置 | `尚未配置 B 站 Cookie，B 站动态与直播轮询会跳过；抖音 Cookie 已配置` |
| 已配置 | 未配置 | `尚未配置抖音 Cookie，抖音作品与直播轮询会跳过；B 站 Cookie 已配置` |

[← 返回 README](../../../README.md)
