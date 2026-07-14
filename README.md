# OTA Monitor

一个可自动运行的竞品 OTA 情报机器人：定时抓取公开更新信息，自动去重和分类，并生成可在线浏览的静态网页。

## 这版能做什么

- 读取 `config/sources.json` 中配置的 RSS、JSON 和基础 HTML 信息源
- 将新条目按竞品、功能域、更新类型自动分类
- 保留原始链接、抓取时间和分类依据，方便人工复核
- 将结果生成到 `docs/index.html`；开启 GitHub Pages 后可直接在线查看
- 通过 GitHub Actions 每天自动运行，也可在 Actions 页面手动触发

> 首次运行使用 `data/demo_updates.json` 生成演示页面，确保项目克隆后即可预览。它们是演示数据，不是竞品事实记录。接入真实来源后，演示数据会在第一次真实抓取时自动被替换。

## 本地运行

无需安装第三方依赖（Python 3.10+）：

```bash
python -m src.ota_monitor --demo
python -m src.ota_monitor
```

生成文件：

```text
docs/index.html       # GitHub Pages 页面
data/ota_updates.json # 结构化历史条目
```

## 接入真实来源

编辑 `config/sources.json`，每个来源支持：

```json
{
  "brand": "品牌名",
  "name": "来源名称",
  "url": "https://example.com/feed.xml",
  "type": "rss"
}
```

- `rss`：RSS / Atom 订阅源，推荐优先使用，稳定性最高。
- `json`：返回数组或 `items` / `data` 数组的公开 JSON 接口。
- `html`：为没有订阅源的公开发布页提供基础抓取；建议后续针对具体站点补充解析器。

为了保证信息可信，页面会显示来源链接和抓取时间；机器只负责收集、归类和摘要，不会把无法验证的信息写成结论。

## 自动发布网页

1. 在仓库 **Settings → Pages** 中，选择 **Deploy from a branch**。
2. 选择分支 `main` 和目录 `/docs`，保存。
3. 在仓库 **Settings → Actions → General** 中，确保 Workflow permissions 允许 **Read and write permissions**。
4. 在 Actions 页面手动运行一次 **Update OTA monitor** 工作流。

工作流每天 09:15（北京时间）更新一次，也可以在 Actions 页面随时手动运行。

## 分类规则

`config/taxonomy.json` 是可维护的分类词表。默认分类包含：智能驾驶、导航与地图、座舱交互、语音与 AI、娱乐生态、能源补给、连接与账号、性能稳定性。每条记录也会标出更新类型：新功能、体验优化、问题修复或其他。

## 后续演进

这个版本先替代「收集 → 整理 → 归类 → 更新周报页面」的重复劳动。后续可以增加：

- 接入公司可用的大模型，对条目生成更精细的摘要和影响判断；
- 为各竞品编写专属解析器，提高网页抓取准确率；
- 推送到飞书文档或群消息；
- 增加功能对比、趋势和竞品覆盖率看板。
