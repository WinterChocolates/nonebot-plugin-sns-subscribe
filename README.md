# nonebot_plugin_sns_subscribe — B站 / 抖音订阅推送

一个插件同时订阅 **B 站** 与 **抖音** 作者，并把新内容渲染成卡片推到 QQ 群：

| 平台 | 推送内容 | 数据来源 |
| --- | --- | --- |
| B 站 | 动态、视频投稿、专栏/音频（动态里出现时）、开播/下播 | 官方 Web 接口（httpx） |
| 抖音 | 新作品、开播/下播 | Playwright 持久化浏览器旁路读取抖音 Web 接口响应 |

功能参考并整合了 [`nonebot-plugin-bilibili`](https://github.com/mengbingnaixi/nonebot-plugin-bilibili)
与 [`nonebot-plugin-douyin-notify`](https://github.com/mengbingnaixi/nonebot-plugin-douyin-notify)，
并在两者基础上增加了**在线模板编辑器**：在 Web 后台直接改 HTML 模板源码、即时预览、另存为，
再按群应用到 B 站/抖音的不同内容类型。

## 目录

- [安装](#安装)
- [配置](#配置)
- [Web 管理后台](#web-管理后台)
- [在线修改模板](#在线修改模板)
- [群内命令](#群内命令)
- [数据目录](#数据目录)
- [工作原理与注意事项](#工作原理与注意事项)

## 安装

本仓库使用 [nonebot-plugin-template](https://github.com/fllesser/nonebot-plugin-template)
的 `src/` 布局，可直接作为独立插件仓库使用。

在本仓库内开发 / 调试：

```bash
uv sync
uv run playwright install chromium   # 卡片渲染与抖音抓取需要
uv run poe test                      # 运行测试（pytest + nonebug）
uv run ruff check src tests          # 代码检查
```

在 NoneBot 机器人项目中使用：

```bash
# 方式一：pip 安装（推荐固定分支/标签）
pip install git+https://github.com/WinterChocolates/nonebot_plugin_sns_subscribe.git

# 方式二：克隆到机器人项目，并把 src 加入 plugin_dirs
git clone https://github.com/WinterChocolates/nonebot_plugin_sns_subscribe.git
```

```toml
# 机器人项目 pyproject.toml
[tool.nonebot]
plugin_dirs = ["nonebot_plugin_sns_subscribe/src"]
```

首次启动若缺少 Chromium 且 `SNS_AUTO_INSTALL_BROWSER=true`（默认），插件会自动执行
`playwright install chromium`；Linux 服务器通常还需要
`playwright install --with-deps chromium`。

启动日志会打印 Web 后台地址与管理令牌：

```
SNS subscription: Web 管理后台 -> /sns-subscribe/  管理令牌：xxxx（数据目录：...）
```

## 配置

所有配置项都可以写在 `.env`（NoneBot 会读取），前缀统一为 `SNS_`。

```dotenv
# 数据目录（默认 ./data/nonebot_plugin_sns_subscribe）
SNS_DATA=./data/nonebot_plugin_sns_subscribe

# Web 后台
SNS_WEB_ENABLED=true
SNS_WEB_PATH=/sns-subscribe
SNS_WEB_TOKEN=            # 留空则自动生成并保存到数据库
SNS_WEB_PUBLIC_URL=       # 例如 https://bot.example.com，供命令提示使用

# 轮询开关与间隔（秒）
SNS_ENABLE_BILIBILI_DYNAMIC=true
SNS_ENABLE_BILIBILI_LIVE=true
SNS_ENABLE_DOUYIN=true
SNS_BILIBILI_DYNAMIC_INTERVAL=120
SNS_BILIBILI_LIVE_INTERVAL=30
SNS_DOUYIN_INTERVAL=180
SNS_DOUYIN_ACCOUNT_DELAY=12

# 初始凭据（也可留空，之后在 Web 后台扫码登录 / 粘贴 Cookie）
SNS_BILIBILI_COOKIE=
SNS_DOUYIN_COOKIE=

# 行为
SNS_INITIAL_POST_LIMIT=3      # 单轮最多补推多少条历史内容
SNS_NOTIFY_LIVE_END=false     # 是否推送下播通知
SNS_AUTO_INSTALL_BROWSER=true

# 渲染
SNS_RENDER_WIDTH=720
SNS_RENDER_TIMEOUT=20000

# 浏览器（B站卡片渲染 + 抖音抓取共用）
SNS_BROWSER_CHANNEL=             # chrome / msedge；留空则 Windows 自动探测已安装浏览器
SNS_BROWSER_EXECUTABLE=          # 自定义浏览器路径，优先级最高
SNS_BROWSER_AUTO_DETECT=true     # Windows 下自动发现 Chrome / Edge
# 兼容旧名字（两个平台同样会读取）：
# SNS_DOUYIN_BROWSER_CHANNEL=chrome
# SNS_DOUYIN_BROWSER_EXECUTABLE=

# 抖音页面参数
SNS_DOUYIN_PAGE_TIMEOUT=45000
SNS_DOUYIN_PAGE_SETTLE_MS=2500
SNS_DOUYIN_OPERATION_TIMEOUT=90
SNS_DOUYIN_LOGIN_TIMEOUT=900
SNS_DOUYIN_HEADLESS=true          # 桌面 Windows 上设为 false 可降低风控概率
SNS_DOUYIN_BROWSER_RESTART_INTERVAL=21600
```

轮询间隔、开关、下播通知也可以在 Web 后台「运行设置」里改，改动会持久化到数据库并立即生效。

## Web 管理后台

访问 `http://<bot>:<port>/sns-subscribe/`：

- 直接用 `?token=<管理令牌>` 访问会自动登录并写入 Cookie（7 天有效）。
- 也可以打开 `/sns-subscribe/login` 手动输入令牌。
- 令牌可用 `SNS_WEB_TOKEN` 固定，否则自动生成（保存在 `settings.web_token`）。

后台包含六个标签页：

| 标签页 | 能力 |
| --- | --- |
| 概览 | 群数量、两平台订阅数、模板数、运行状态 |
| 订阅管理 | 扫描 QQ 群、按平台添加/取消订阅、关键词过滤、@全体 开关、按群设置渲染方案 |
| 模板编辑 | 列出内置/自定义模板，在线编辑源码，实时预览，上传/删除，应用到群 |
| 资源 | 上传/删除字体文件（`font_css` 自动生效，支持字体集 `.ttc`） |
| 账号登录 | B 站扫码登录；抖音浏览器扫码登录（可在截图上直接点击、输入、回车）或粘贴 Cookie |
| 运行设置 | 轮询开关与间隔、手动推送 |

## 浏览器选择（B站渲染 + 抖音共用）

卡片截图（B站/抖音）与抖音数据抓取使用同一套浏览器设置，按以下顺序尝试，全部失败才回退：

1. `SNS_BROWSER_EXECUTABLE` / `SNS_DOUYIN_BROWSER_EXECUTABLE`（显式可执行文件，优先级最高）
2. `SNS_BROWSER_CHANNEL` / `SNS_DOUYIN_BROWSER_CHANNEL`：`chrome` 或 `msedge`
3. Windows 下自动探测已安装的 Chrome 或 Edge（`SNS_BROWSER_AUTO_DETECT=true`，默认开启）
4. Playwright 自带 Chromium（缺失时按需自动安装）

因此：

- Windows 上**不需要**额外下载 Chromium，会直接调用系统 Chrome / Edge；
  只在 `SNS_DOUYIN_BROWSER_CHANNEL=chrome` 或 `msedge` 时按通道启动，
  未配置时按上面第 3 条自动发现。
- 写 `SNS_DOUYIN_BROWSER_CHANNEL=chrome` 时 **B 站卡片渲染也会用 Chrome**（旧配置名保留兼容）。
- 想强制使用 Playwright 自带的 Chromium：设置 `SNS_BROWSER_AUTO_DETECT=false` 并清空两个浏览器配置。
- 实际使用的浏览器可以在 Web 后台「运行设置 → 浏览器」查看（配置 / 卡片渲染 / 抖音抓取）。

## 在线修改模板

模板是 **Jinja2** 语法，卡片根元素必须是以下三者之一（截图只截该元素）：

```html
<article id="sns-card">…</article>
<!-- 也兼容 nonebot-plugin-bilibili / douyin-notify 的写法 -->
<article id="bili-card">…</article>
<article id="douyin-card">…</article>
```

可用变量：

| 变量 | 说明 |
| --- | --- |
| `content` | `PushContent`：`platform`、`id`、`author_id`、`kind`（`.value` / `.label`）、`title`、`text`、`url`、`author_name`、`author_avatar`、`cover`、`images`、`image_dimensions`、`rich_text`、`published_at`、`published_text`、`live_action`、`forward` |
| `font_css` | 已内联的字体与主题色 CSS，放在 `<style>` 里即可 |
| `theme_css` | 只有主题变量的 CSS（`--sns-accent` / `--sns-theme` 等） |
| `accent_color` | 当前主色 |
| `gradient_color` | 渐变第二色（空字符串表示纯色） |
| `theme_background` | 可直接用于 `background:` 的颜色/渐变 |
| `platform` / `platform_label` | `bilibili` / `douyin` 与 `B站` / `抖音` |
| `render_kind` | `dynamic` / `video` / `live` |

主题变量（三套别名都指向同一组颜色，便于直接复用参考插件的模板）：

```
--sns-accent, --sns-accent-2, --sns-theme
--bili-accent, --bili-accent-2, --bili-theme
--douyin-accent, --douyin-accent-2, --douyin-theme
```

编辑流程：

1. 「模板编辑」选平台 + 类型（B 站：动态/视频/直播；抖音：视频/直播）。
2. 默认载入内置模板（只读）；可直接编辑。
3. 点「预览渲染」——**预览使用的是编辑器里尚未保存的源码**，可以边改边看。
4. 点「保存」需要先「另存为」（例如 `my-card.html`），文件保存到
   `data/nonebot_plugin_sns_subscribe/templates/<platform>/<kind>/`。
5. 在「应用到群」选择群 → 保存渲染方案；或在「订阅管理」里按群设置。
6. 删除模板文件时，引用它的群会自动回落到内置模板。

模板示例（极简卡片）：

```html
<!doctype html>
<html><head><meta charset="utf-8"><style>
{{ font_css | safe }}
#sns-card{width:420px;padding:24px;border-radius:18px;background:var(--sns-theme);color:#fff}
#sns-card h1{font-size:22px;margin:0 0 8px}
</style></head>
<body><article id="sns-card">
  <h1>{{ content.title }}</h1>
  <p>{{ platform_label }} · {{ content.author_name }}</p>
  <p>{{ content.published_text }}</p>
</article></body></html>
```

## 群内命令

```text
/sub add <bili|douyin> <UID|主页链接>       订阅作者
/sub del <bili|douyin> <作者ID>             取消订阅
/sub list                                   查看本群订阅
/sub listall                                查看全部订阅（超管）
/sub delall | delallall                     清空本群 / 全部订阅
/sub filter add <正则>                      添加关键词过滤
/sub filter del <规则或序号>
/sub filter list
/sub atall <all|video|dynamic|live> on|off <作者ID|平台:作者ID>
/sub push <bili|douyin> <类型> <编号|主页链接>
/sub login bilibili                         扫码登录 B 站（超管）
/sub login douyin                           提示前往 Web 后台登录（超管）
/sub status                                 查看运行状态
/sub help [image]                           文本或图片帮助
```

- 别名：`/订阅`、`/sns`。
- 平台写法：`bili` / `bilibili` / `b站`，`dy` / `douyin` / `抖音`。
- 超级用户可在命令末尾追加 `-群号` 跨群操作，例如 `/sub add bili 2 -123456`。
- 群管理操作（增删订阅、过滤、@全体、手动推送）仅群主/群管理员/超级用户可用。

## 常见问题

**卡片里的中文是一片空白？**
Linux 容器/服务器常常没有中文字体，Chromium 就会把中文渲染成空白（Windows 自带微软雅黑所以看不到这个问题）。
两种解决方式：

1. 安装系统字体：`apt install fonts-noto-cjk`；
2. 在 Web 后台「资源」上传中文字体（支持 `.ttf` / `.otf` / `.ttc` / `.woff` / `.woff2`，上限 32 MiB），
   再到「订阅管理」或「模板编辑 → 应用到群」里选用该字体。

启动时如果检测不到中文字体会打印警告。

**B 站动态不推送？**
动态接口需要 Cookie：在「账号登录」扫码登录，或把浏览器 Cookie 粘贴进去。
未登录时插件会跳过动态轮询并在日志提示。

**粘贴了抖音 Cookie 还是提示未登录？**
抖音的登录态只认 `sessionid` / `sessionid_ss`，并且必须真的注入到浏览器里：

1. 粘贴内容支持三种格式：`a=b; c=d`（DevTools 请求头）、多行文本、Cookie 扩展导出的 JSON；
   保存后后台会显示「含登录态 sessionid」或「缺少 sessionid」，缺了说明复制不完整；
2. Cookie 保存后会**立即注入**正在运行的抖音浏览器，无需重启插件；
3. 点「验证登录态」：会真实打开一次抖音并（若已有抖音订阅）读取一个作者主页，
   显示 `已登录` 或具体失败原因；
4. 若仍失败，基本是 Cookie 已过期或被风控 —— 重新扫码登录最可靠。

**抖音提示「作品列表接口未就绪（HTTP 200）」？**
说明抖音对 `/aweme/v1/web/aweme/post/` 返回了空响应或非 JSON（风控页），常见原因与处理：

1. **未登录 / 登录态失效**：后台「账号登录」重新扫码，务必在登录页完成滑块验证；
   粘贴 Cookie 时要包含 `sessionid`，保存后点「验证登录态」确认；
2. **触发风控**：把 `SNS_BROWSER_CHANNEL` 设为 `chrome`（用系统 Chrome），
   桌面 Windows 上还可以设 `SNS_DOUYIN_HEADLESS=false` 让浏览器有界面运行；
3. 在后台「打开抖音登录页」里手动浏览一次该作者主页，通过验证后再执行推送；
4. 查看日志里 `抖音作品列表响应无法解析` 那一行，会打印响应片段便于判断是空响应还是验证页。

**提示浏览器不可用？**
Windows 下插件会自动使用已安装的 Chrome / Edge；也可以显式设置
`SNS_BROWSER_CHANNEL=chrome`（或 `msedge`，旧名 `SNS_DOUYIN_BROWSER_CHANNEL` 同样生效）。
都没有时才会回退 Playwright Chromium，此时需执行 `playwright install chromium`
（Linux 再加 `--with-deps`），并保证服务器有足够内存与 `/dev/shm` 空间。

**如何重置后台令牌？**
删除数据库中 `settings` 表的 `web_token` 行后重启，或直接设置 `SNS_WEB_TOKEN`。

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
渲染方案与去重回执。

## 工作原理与注意事项

- **B 站动态**需要 Cookie（否则接口易触发风控 412）。直播与视频接口通常不需要登录。
- **B 站动态基线**：首次轮询只记录最新一条作为基线；之后按时间顺序补推新动态，
  单轮最多 `SNS_INITIAL_POST_LIMIT` 条。基线在列表中消失时（置顶/删稿）会静默重置，避免刷历史。
- **抖音** 由持久化 Chromium 打开用户主页，旁路读取
  `/aweme/v1/web/user/profile/other/`、`/aweme/v1/web/aweme/post/` 等响应，无需自行签名。
  浏览器用户目录保存在 `browser-profile/`，登录一次即可长期复用。
- **抖音风控**：轮询间隔不宜过小（默认 180s），多作者之间会随机等待
  `SNS_DOUYIN_ACCOUNT_DELAY` 秒；建议同时限制订阅数量。
- **投递幂等**：每条内容对每个群只投递一次（`delivery_receipts` + `delivery_claims`），
  服务重启、并发轮询都不会重复推送；命中过滤规则的内容记为已处理，不会在规则删除后补推。
- **渲染失败**回退为文本消息；Chromium 崩溃会自动重启并重试一次渲染。
- B 站扫码登录的 Cookie 与抖音登录态都保存在数据库/浏览器目录中，请勿泄露 `data/` 目录。
