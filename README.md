# nonebot_plugin_sns_subscribe — B站 / 抖音订阅推送

一个插件同时订阅 **B 站** 与 **抖音** 作者，把新内容渲染成卡片推到 QQ 群：

| 平台 | 推送内容 | 数据来源 |
| --- | --- | --- |
| B 站 | 动态、视频投稿、专栏/音频（动态里出现时）、开播/下播 | 官方 Web 接口（httpx） |
| 抖音 | 新作品、开播/下播 | Playwright 持久化浏览器旁路读取抖音 Web 接口响应 |

每条推送先发渲染图，紧接着再发一条**合并转发消息**：标题为「【B站】博主名称」/「【抖音】博主名称」，
内容就是本次链接（比裸链接更不容易被风控吞掉）。可用 `SNS_BILIBILI_FORWARD` / `SNS_DOUYIN_FORWARD` 关闭。

功能参考并整合了 [`nonebot-plugin-bilibili`](https://github.com/mengbingnaixi/nonebot-plugin-bilibili)
与 [`nonebot-plugin-douyin-notify`](https://github.com/mengbingnaixi/nonebot-plugin-douyin-notify)，
并增加了**在线模板编辑器**：在 Web 后台直接改 HTML 模板、即时预览、另存为，再按群应用到不同内容类型。

## 文档

| 文档 | 内容 |
| --- | --- |
| [配置项详解](src/nonebot_plugin_sns_subscribe/docs/configuration.md) | 全部 `SNS_*` 配置、浏览器选择、数据目录、Cookie 启动提示 |
| [Web 后台与在线模板](src/nonebot_plugin_sns_subscribe/docs/web-console.md) | 后台六个标签页、模板变量、编辑流程与示例 |
| [常见问题与工作原理](src/nonebot_plugin_sns_subscribe/docs/faq.md) | 中文空白、抖音风控、幂等投递、渲染回退等 |

## 安装

本仓库使用 [nonebot-plugin-template](https://github.com/fllesser/nonebot-plugin-template)
的 `src/` 布局。在机器人项目中使用：

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

本仓库内开发 / 调试：

```bash
uv sync
uv run playwright install chromium   # 卡片渲染与抖音抓取需要
uv run poe test                      # 运行测试（pytest + nonebug）
uv run ruff check src tests          # 代码检查
```

## 快速开始

最小配置（`.env` 或 `.env.prod`），Cookie 也可以留空、之后在 Web 后台扫码登录：

```dotenv
SNS_WEB_ENABLED=true
SNS_BILIBILI_COOKIE=
SNS_DOUYIN_COOKIE=
```

启动日志会打印后台地址与令牌：

```
SNS subscription: Web 管理后台 -> /sns-subscribe/  管理令牌：xxxx（数据目录：...）
```

打开 `http://<bot>:<port>/sns-subscribe/?token=<令牌>`，或用 `/sub add` 在群里直接订阅。
首次启动缺少 Chromium 且 `SNS_AUTO_INSTALL_BROWSER=true`（默认）时会自动安装；
Linux 服务器通常还需要 `playwright install --with-deps chromium`。
完整配置见 [配置项详解](src/nonebot_plugin_sns_subscribe/docs/configuration.md)。

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
/sub push <bili|douyin> [类型] <编号|主页链接>
/sub login bilibili                         扫码登录 B 站（超管）
/sub login douyin                           提示前往 Web 后台登录（超管）
/sub status                                 查看运行状态
/sub help [image]                           文本或图片帮助
```

- 别名：`/订阅`、`/sns`；平台写法：`bili` / `bilibili` / `b站`，`dy` / `douyin` / `抖音`。
- `/sub push` 的类型可省略：`/sub push douyin https://www.douyin.com/user/xxxx`
  会按链接自动推断（抖音主页/作品链接→最新作品，`live.douyin.com`→直播；
  B 站 BV/av→视频，`t.bilibili.com/opus`→动态，`live.bilibili.com`→直播）。
- 超级用户可在命令末尾追加 `-群号` 跨群操作，例如 `/sub add bili 2 -123456`。
- 群管理操作（增删订阅、过滤、@全体、手动推送）仅群主/群管理员/超级用户可用。
