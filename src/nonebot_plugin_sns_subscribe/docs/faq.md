# 常见问题与工作原理

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
未登录时插件会跳过动态轮询并在日志提示。直播与视频接口通常不需要登录。

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

**后台访问返回 404？**
`.env` 里的 `SNS_WEB_ENABLED` 被设成了 `false`，此时插件不挂载任何 Web 路由。改成 `true` 后重启。

## 工作原理与注意事项

- **B 站动态**需要 Cookie（否则接口易触发风控 412）。
- **B 站动态基线**：首次轮询只记录最新一条作为基线；之后按时间顺序补推新动态，
  单轮最多 `SNS_INITIAL_POST_LIMIT` 条。基线在列表中消失时（置顶/删稿）会静默重置，避免刷历史。
- **抖音** 由持久化 Chromium 打开用户主页，旁路读取
  `/aweme/v1/web/user/profile/other/`、`/aweme/v1/web/aweme/post/` 等响应，无需自行签名。
  浏览器用户目录保存在 `browser-profile/`，登录一次即可长期复用。
- **抖音风控**：轮询间隔不宜过小（默认 180s），多作者之间会随机等待
  `SNS_DOUYIN_ACCOUNT_DELAY` 秒；建议同时限制订阅数量。
- **投递幂等**：每条内容对每个群只投递一次（`delivery_receipts` + `delivery_claims`），
  服务重启、并发轮询都不会重复推送；命中过滤规则的内容记为已处理，不会在规则删除后补推。
- **合并转发**：图片发送成功后再发一条合并转发，内容只有本次链接；发送失败只记日志，
  不影响图片推送的送达判定。可用 `SNS_BILIBILI_FORWARD` / `SNS_DOUYIN_FORWARD` 分别关闭。
- **渲染失败**回退为文本消息；Chromium 崩溃会自动重启并重试一次渲染。
- B 站扫码登录的 Cookie 与抖音登录态都保存在数据库/浏览器目录中，请勿泄露 `data/` 目录。

[← 返回 README](../../../README.md) · [配置项](./configuration.md) · [Web 后台](./web-console.md)
