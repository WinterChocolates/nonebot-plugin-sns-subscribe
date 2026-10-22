# Web 管理后台与在线模板

## 访问

访问 `http://<bot>:<port>/sns-subscribe/`：

- 直接用 `?token=<管理令牌>` 访问会自动登录并写入 Cookie（7 天有效）；
- 也可以打开 `/sns-subscribe/login` 手动输入令牌；
- 令牌可用 `SNS_WEB_TOKEN` 固定，否则自动生成（保存在 `settings.web_token`）。

启动日志会打印地址与令牌：

```
SNS subscription: Web 管理后台 -> /sns-subscribe/  管理令牌：xxxx（数据目录：...）
```

> 如果访问返回 404，先确认 `.env` 里没有把 `SNS_WEB_ENABLED` 设为 `false` —— 关闭时不会挂载任何路由。

## 六个标签页

| 标签页 | 能力 |
| --- | --- |
| 概览 | 群数量、两平台订阅数、模板数、运行状态 |
| 订阅管理 | 扫描 QQ 群、按平台添加/取消订阅、关键词过滤、@全体 开关、按群设置渲染方案 |
| 模板编辑 | 列出内置/自定义模板，在线编辑源码，实时预览，上传/删除，应用到群 |
| 资源 | 上传/删除字体文件（`font_css` 自动生效，支持字体集 `.ttc`） |
| 账号登录 | B 站扫码登录；抖音浏览器扫码登录（可在截图上直接点击、输入、回车）或粘贴 Cookie |
| 运行设置 | 轮询开关与间隔、手动推送 |

手动推送支持省略类型：`/api/push` 与后台表单里选择类型即可，群内命令见
[README 的群内命令](../../../README.md#群内命令)。

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

1. 「模板编辑」选平台 + 类型（B 站：动态/视频/直播；抖音：视频/直播）；
2. 默认载入内置模板（只读）；可直接编辑；
3. 点「预览渲染」——**预览使用的是编辑器里尚未保存的源码**，可以边改边看；
4. 点「保存」需要先「另存为」（例如 `my-card.html`），文件保存到
   `data/nonebot_plugin_sns_subscribe/templates/<platform>/<kind>/`；
5. 在「应用到群」选择群 → 保存渲染方案；或在「订阅管理」里按群设置；
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

[← 返回 README](../../../README.md) · [常见问题](./faq.md)
