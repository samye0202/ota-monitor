"""Collect public OTA update notices and build a static intelligence page.

This module intentionally uses only the Python standard library so it can run
unchanged in GitHub Actions and on a colleague's machine.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import urllib.request
from urllib.parse import urljoin
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "ota_updates.json"
DEMO_PATH = ROOT / "data" / "demo_updates.json"
SOURCES_PATH = ROOT / "config" / "sources.json"
TAXONOMY_PATH = ROOT / "config" / "taxonomy.json"
SITE_PATH = ROOT / "docs" / "index.html"
USER_AGENT = "ota-monitor/1.0 (+https://github.com/samye0202/ota-monitor)"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback


def read_required_json(path: Path) -> Any:
    """Read a required configuration file and fail loudly when it is invalid."""
    if not path.exists():
        raise RuntimeError(f"Required configuration file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Required configuration file is invalid: {path}: {exc}") from exc
    if not value:
        raise RuntimeError(f"Required configuration file is empty: {path}")
    return value
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return fallback


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def normalize_date(value: str | None) -> str:
    if not value:
        return now_iso()
    value = value.strip()
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return now_iso()


def request_text(url: str, extra_headers: dict[str, str] | None = None) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json, application/xml, text/xml, text/html;q=0.9"}
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=25) as response:  # nosec B310: configured public sources
        raw = response.read()
        declared = response.headers.get_content_charset()
        meta = re.search(br"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", raw[:4096], re.I)
        candidates = [declared, meta.group(1).decode("ascii", errors="ignore") if meta else None, "utf-8", "gb18030"]
        for charset in filter(None, candidates):
            try:
                return raw.decode(charset)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", errors="replace")


def element_text(element: ET.Element, names: list[str]) -> str:
    for child in element.iter():
        local_name = child.tag.rsplit("}", 1)[-1].lower()
        if local_name in names and child.text:
            return clean_text(child.text)
    return ""


def parse_rss(source: dict[str, Any], content: str) -> list[dict[str, Any]]:
    root = ET.fromstring(content)
    entries = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    results = []
    for entry in entries[:40]:
        link = ""
        for child in entry.iter():
            if child.tag.rsplit("}", 1)[-1].lower() == "link":
                link = child.attrib.get("href", "") or (child.text or "")
                if link:
                    break
        results.append({
            "brand": source["brand"],
            "title": element_text(entry, ["title"]) or "未命名 OTA 更新",
            "summary": element_text(entry, ["description", "summary", "content"]),
            "url": link.strip() or source["url"],
            "published_at": normalize_date(element_text(entry, ["pubdate", "published", "updated", "date"])),
            "source_name": source.get("name", source["brand"]),
        })
    return results


def parse_json_source(source: dict[str, Any], content: str) -> list[dict[str, Any]]:
    payload = json.loads(content)
    if isinstance(payload, dict):
        payload = payload.get("items") or payload.get("data") or payload.get("results") or []
    if not isinstance(payload, list):
        return []
    results = []
    for item in payload[:40]:
        if not isinstance(item, dict):
            continue
        results.append({
            "brand": source["brand"],
            "title": clean_text(str(item.get("title") or item.get("name") or "未命名 OTA 更新")),
            "summary": clean_text(str(item.get("summary") or item.get("description") or item.get("content") or "")),
            "url": str(item.get("url") or item.get("link") or source["url"]),
            "published_at": normalize_date(str(item.get("published_at") or item.get("published") or item.get("date") or "")),
            "source_name": source.get("name", source["brand"]),
        })
    return results


def parse_html(source: dict[str, Any], content: str) -> list[dict[str, Any]]:
    # Generic, conservative parser: turns headings into reviewable leads rather
    # than pretending it understands a brand-specific release-note page.
    headings = re.findall(r"<(?:h1|h2|h3)[^>]*>(.*?)</(?:h1|h2|h3)>", content, re.I | re.S)
    results = []
    for heading in headings[:20]:
        title = clean_text(heading)
        if len(title) < 4:
            continue
        results.append({
            "brand": source["brand"],
            "title": title,
            "summary": "来自公开发布页的待复核更新线索。",
            "url": source["url"],
            "published_at": now_iso(),
            "source_name": source.get("name", source["brand"]),
            "needs_review": True,
        })
    return results


def parse_autohome_ota(source: dict[str, Any], content: str) -> list[dict[str, Any]]:
    """Extract OTA article leads from Autohome's public OTA topic page."""
    results = []
    seen: set[str] = set()
    links = re.findall(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", content, re.I | re.S)
    for href, anchor in links:
        title = clean_text(anchor)
        if len(title) < 8 or not re.search(r"OTA|升级|车机|智驾|辅助驾驶", title, re.I):
            continue
        url = urljoin(source["url"], html.unescape(href))
        if url in seen:
            continue
        seen.add(url)
        results.append({
            "brand": "待识别",
            "title": title,
            "summary": "来自汽车之家「车辆OTA资讯」专题页，待进一步查看原文确认版本与具体更新项。",
            "url": url,
            "published_at": now_iso(),
            "source_name": source.get("name", "汽车之家 · 车辆OTA资讯"),
            "needs_review": True,
        })
    return results[:50]


def classify(item: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    domains = [name for name, keywords in taxonomy["domains"].items() if any(keyword.lower() in text for keyword in keywords)]
    change_type = next((name for name, keywords in taxonomy["change_types"].items() if any(keyword.lower() in text for keyword in keywords)), "其他")
    item["domains"] = domains or ["待归类"]
    item["change_type"] = change_type
    item["classification_basis"] = "规则词表：" + "、".join(item["domains"])
    item["collected_at"] = now_iso()
    fingerprint = f"{item.get('brand','')}|{item.get('title','')}|{item.get('url','')}"
    item["id"] = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return item


def collect_sources() -> tuple[list[dict[str, Any]], list[str]]:
    source_config = read_required_json(SOURCES_PATH)
    sources = source_config.get("sources", []) if isinstance(source_config, dict) else []
    if not isinstance(sources, list) or not sources:
        raise RuntimeError("config/sources.json must contain at least one source")
    collected: list[dict[str, Any]] = []
    errors: list[str] = []
    parsers = {"rss": parse_rss, "json": parse_json_source, "html": parse_html, "autohome_ota": parse_autohome_ota}
    for source in sources:
        try:
            parser = parsers.get(source.get("type", "rss").lower())
            if not parser:
                raise ValueError(f"unsupported source type: {source.get('type')}")
            collected.extend(parser(source, request_text(source["url"], source.get("headers"))))
        except Exception as exc:  # Continue so one unavailable source never stops the daily report.
            errors.append(f"{source.get('name', source.get('url', 'unknown source'))}: {exc}")
    if not collected:
        raise RuntimeError("No OTA entries were collected. " + " | ".join(errors))
    return collected, errors


def merge_items(existing: list[dict[str, Any]], incoming: list[dict[str, Any]], taxonomy: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {item.get("id"): item for item in existing if item.get("id")}
    for raw_item in incoming:
        item = classify(raw_item, taxonomy)
        prior = by_id.get(item["id"])
        if prior:
            item["first_seen_at"] = prior.get("first_seen_at", item["collected_at"])
        else:
            item["first_seen_at"] = item["collected_at"]
        by_id[item["id"]] = item
    return sorted(by_id.values(), key=lambda item: item.get("published_at", ""), reverse=True)


def render_site(items: list[dict[str, Any]], errors: list[str], demo_mode: bool) -> str:
    data = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    escaped_errors = "".join(f"<li>{html.escape(error)}</li>" for error in errors)
    warning = "<div class='notice'>当前展示的是演示数据。运行真实采集后会自动替换。</div>" if demo_mode else ""
    error_panel = f"<details class='errors'><summary>本次有 {len(errors)} 个来源未成功抓取</summary><ul>{escaped_errors}</ul></details>" if errors else ""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang='zh-CN'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>OTA 竞品情报台</title>
  <style>
    :root {{ --bg:#07111f; --panel:#101f33; --line:#233958; --text:#ecf4ff; --muted:#9fb2cc; --brand:#50e3c2; --accent:#6ea8fe; --warn:#ffd166; }}
    * {{ box-sizing:border-box }} body {{ margin:0; font-family:Inter,"Microsoft YaHei",sans-serif; background:radial-gradient(circle at 10% 0,#173760 0,var(--bg) 38%); color:var(--text) }}
    main {{ max-width:1280px; margin:auto; padding:42px 24px 64px }} h1 {{ margin:0; font-size:clamp(28px,5vw,48px); letter-spacing:-1px }} .subtitle {{ color:var(--muted); margin:12px 0 30px }}
    .notice,.errors {{ background:#182b42; border:1px solid var(--line); border-radius:12px; padding:12px 16px; color:var(--warn); margin:0 0 18px }}
    .stats {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin:20px 0 }} .stat {{ background:linear-gradient(140deg,#142943,#0d1b2e); border:1px solid var(--line); border-radius:16px; padding:18px }} .stat b {{ display:block; font-size:28px; color:var(--brand) }}
    .filters {{ display:flex; flex-wrap:wrap; gap:10px; margin:26px 0 }} button {{ cursor:pointer; color:var(--text); border:1px solid var(--line); background:#10233a; border-radius:999px; padding:9px 14px }} button.active,button:hover {{ background:var(--accent); border-color:var(--accent) }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:16px }} article {{ background:rgba(16,31,51,.94); border:1px solid var(--line); border-radius:16px; padding:18px; min-height:240px; display:flex; flex-direction:column }} article h2 {{ font-size:18px; line-height:1.45; margin:10px 0 }} article p {{ color:var(--muted); line-height:1.65; flex:1 }} .meta {{ color:var(--muted); font-size:13px }} .tag {{ display:inline-block; margin:3px 4px 0 0; color:#cbe4ff; background:#1b3d62; padding:4px 8px; border-radius:999px; font-size:12px }} .type {{ color:#06141d; background:var(--brand) }} a {{ color:#8fc0ff; text-decoration:none }} a:hover {{ text-decoration:underline }} .empty {{ color:var(--muted); padding:48px 0 }} footer {{ color:var(--muted); margin-top:34px; font-size:13px }}
  </style>
</head>
<body><main>
  <h1>OTA 竞品情报台</h1><p class='subtitle'>自动收集 · 自动归类 · 可追溯查看　/　更新于 {generated_at}</p>
  {warning}
  <section class='stats' id='stats'></section><section class='filters' id='filters'></section><section class='grid' id='cards'></section>
  {error_panel}
  <footer>每一条内容均保留公开来源链接和机器分类依据。自动分类仅供情报整理，重要判断请回到原始来源复核。</footer>
</main>
<script>
const items={data}; const cards=document.querySelector('#cards'), filters=document.querySelector('#filters'), stats=document.querySelector('#stats');
const domains=[...new Set(items.flatMap(x=>x.domains||[]))]; let selected='全部';
function esc(v){{const d=document.createElement('div');d.textContent=v||'';return d.innerHTML}}
function render(){{const shown=selected==='全部'?items:items.filter(x=>(x.domains||[]).includes(selected));cards.innerHTML=shown.length?shown.map(x=>`<article><div><span class="tag type">${{esc(x.change_type)}}</span>${{(x.domains||[]).map(d=>`<span class="tag">${{esc(d)}}</span>`).join('')}}</div><h2>${{esc(x.title)}}</h2><p>${{esc(x.summary||'暂无摘要')}}</p><div class="meta">${{esc(x.brand)}} · ${{esc((x.published_at||'').slice(0,10))}}</div><a href="${{esc(x.url)}}" target="_blank" rel="noreferrer">查看原始来源 ↗</a></article>`).join(''):'<p class="empty">当前筛选条件下没有条目。</p>';}}
function setup(){{const brands=new Set(items.map(x=>x.brand));stats.innerHTML=`<div class="stat"><b>${{items.length}}</b>收录条目</div><div class="stat"><b>${{brands.size}}</b>覆盖竞品</div><div class="stat"><b>${{domains.length}}</b>功能域</div>`;['全部',...domains].forEach(d=>{{const b=document.createElement('button');b.textContent=d;b.className=d===selected?'active':'';b.onclick=()=>{{selected=d;[...filters.children].forEach(e=>e.classList.toggle('active',e.textContent===d));render()}};filters.append(b)}});render();}} setup();
</script></body></html>"""


def run(demo: bool = False) -> None:
    taxonomy = read_required_json(TAXONOMY_PATH)
    if not isinstance(taxonomy, dict) or not isinstance(taxonomy.get("domains"), dict) or not isinstance(taxonomy.get("change_types"), dict):
        raise RuntimeError("config/taxonomy.json must contain domains and change_types objects")
    existing = read_json(DATA_PATH, [])
    errors: list[str] = []
    if demo:
        incoming = read_json(DEMO_PATH, [])
        if not incoming:
            raise RuntimeError("data/demo_updates.json is empty")
    else:
        incoming, errors = collect_sources()
    items = merge_items(existing, incoming, taxonomy)
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SITE_PATH.write_text(render_site(items, errors, demo), encoding="utf-8")
    print(f"Generated {SITE_PATH.relative_to(ROOT)} with {len(items)} items.")
    if errors:
        print("Some sources failed: " + "; ".join(errors), file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect OTA notices and generate static report")
    parser.add_argument("--demo", action="store_true", help="build using bundled demo data")
    run(parser.parse_args().demo)
