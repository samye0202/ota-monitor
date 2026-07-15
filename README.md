# OTA Monitor

一个可自动运行的竞品 OTA 情报机器人：定时抓取公开更新信息，自动去重和分类，并生成可在线浏览的静态网页。

## 这版能做什么

- 统一使用 `src/` 入口运行采集与页面生成
- 抓取汽车之家的 [车辆 OTA 资讯专题](https://www.autohome.com.cn/31107/0/1/conjunction.html#pvareaid=6867404)
- 自动识别品牌、功能域和更新类型（基于 `config/taxonomy.json`）
- 对历史数据去重并保存到 `data/ota_updates.json`
- 生成 `docs/index.html` 可视化页面，支持品牌/分类/更新类型筛选
- 通过 GitHub Actions 定时更新并提交结果

## 本地运行

无需安装第三方依赖（Python 3.10+）：

```bash
python -m src.ota_monitor --demo
python -m src.ota_monitor
```

> 兼容旧入口：`python ota_monitor.py`（内部会转发到 `src` 入口）。

生成文件：

```text
docs/index.html       # GitHub Pages 页面
data/ota_updates.json # 结构化历史条目
```

## 配置说明

### 数据源

`config/sources.json` 默认内置汽车之家 OTA 专题源：

```json
{
  "brand": "全品牌",
  "name": "汽车之家 · 车辆OTA资讯",
  "url": "https://www.autohome.com.cn/31107/0/1/conjunction.html#pvareaid=6867404",
  "type": "autohome_ota"
}
```

### 分类词表

`config/taxonomy.json` 用于维护：

- `domains`：功能域关键词（如智能驾驶、导航与地图、语音与 AI）
- `change_types`：更新类型关键词（新功能、体验优化、问题修复）

## 自动发布网页

仓库包含两个工作流（`update-monitor.yml` 与 `ota-monitor.yml`），都会执行统一入口 `python -m src.ota_monitor` 并提交：

- `data/ota_updates.json`
- `docs/index.html`

启用 GitHub Pages：

1. 在仓库 **Settings → Pages** 中选择 **Deploy from a branch**。
2. 分支选择 `main`，目录选择 `/docs`。
3. 在 **Settings → Actions → General** 中确认 Workflow permissions 为 **Read and write permissions**。

## 说明

- `--demo` 会读取 `data/demo_updates.json` 生成演示页面，便于本地验证。
- 默认模式会优先抓取真实来源；若抓取失败且历史为空，会自动回退到演示数据，保证页面可用。
- 页面保留原始链接和抓取时间，重要判断请回到原始来源复核。
