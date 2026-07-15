from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"

SOURCES_FILE = CONFIG_DIR / "sources.json"
TAXONOMY_FILE = CONFIG_DIR / "taxonomy.json"
HISTORY_FILE = DATA_DIR / "ota_updates.json"
DEMO_FILE = DATA_DIR / "demo_updates.json"
OUTPUT_HTML = DOCS_DIR / "index.html"

REQUEST_TIMEOUT = 20
DEFAULT_MAX_PAGES = 3
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 OTA-Monitor/1.0"
NEWS_KEYWORDS = ("ota", "升级", "车机", "智驾", "智能驾驶")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fp:
        try:
            return json.load(fp)
        except json.JSONDecodeError:
            return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(value, fp, ensure_ascii=False, indent=2)


def strip_html(raw: str) -> str:
    text = re.sub(r"<script[^>]*>.*?</script\\s*>", "", raw, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style\\s*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def request_text(url: str, extra_headers: dict[str, str] | None = None) -> str:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"}
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url=url, headers=headers)
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        data = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="ignore")


def normalize_source_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def build_page_url(seed_url: str, page: int) -> str:
    base_url = normalize_source_url(seed_url)
    return re.sub(r"/\d+/conjunction\.html", f"/{page}/conjunction.html", base_url, count=1)


def extract_publish_date(text: str) -> str:
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        return now_iso()
    year, month, day = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}T00:00:00+00:00"


def guess_brand(source_brand: str, title: str) -> str:
    if source_brand and source_brand not in {"全品牌", "全部品牌", "未知"}:
        return source_brand
    patterns = [
        r"([\u4e00-\u9fa5A-Za-z0-9·\-]{2,16})\s*OTA",
        r"([\u4e00-\u9fa5A-Za-z0-9·\-]{2,16})\s*车机",
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1).strip("·- ")
    return "未识别品牌"


def classify_item(title: str, summary: str, taxonomy: dict[str, Any]) -> tuple[list[str], str]:
    text = f"{title} {summary}".lower()

    domains: list[str] = []
    for domain, keywords in taxonomy.get("domains", {}).items():
        if any(str(keyword).lower() in text for keyword in keywords):
            domains.append(domain)
    if not domains:
        domains = ["未分类"]

    matched_types: list[str] = []
    for kind, keywords in taxonomy.get("change_types", {}).items():
        if any(str(keyword).lower() in text for keyword in keywords):
            matched_types.append(kind)

    priority = ["问题修复", "新功能", "体验优化"]
    change_type = "其他"
    for kind in priority:
        if kind in matched_types:
            change_type = kind
            break
    if change_type == "其他" and matched_types:
        change_type = matched_types[0]

    return domains, change_type


def make_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title}".encode("utf-8")).hexdigest()[:16]


def parse_autohome_page(html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    blocks = re.findall(r"<li[^>]*>(.*?)</li>", html, re.IGNORECASE | re.DOTALL)

    for block in blocks:
        link_match = re.search(
            r"<h3[^>]*>\s*<a[^>]+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>",
            block,
            re.IGNORECASE | re.DOTALL,
        )
        if not link_match:
            continue

        href, title_html = link_match.groups()
        title = strip_html(title_html)
        if not title:
            continue

        lowered_title = title.lower()
        if not any(keyword in lowered_title for keyword in NEWS_KEYWORDS):
            continue

        summary_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.IGNORECASE | re.DOTALL)
        summary = strip_html(summary_match.group(1)) if summary_match else title

        url = urljoin(source["url"], href.strip())
        published_at = extract_publish_date(block)
        brand = guess_brand(source.get("brand", ""), title)

        items.append(
            {
                "id": make_id(url, title),
                "brand": brand,
                "title": title,
                "summary": summary,
                "url": url,
                "source": source.get("name", "未知来源"),
                "source_url": source.get("url", ""),
                "published_at": published_at,
                "collected_at": now_iso(),
            }
        )

    return items


def fetch_autohome_updates(source: dict[str, Any], max_pages: int) -> list[dict[str, Any]]:
    all_items: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        page_url = build_page_url(source["url"], page)
        try:
            html = request_text(page_url, extra_headers=source.get("headers"))
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] 抓取失败: page={page} url={page_url} error={exc}")
            continue
        all_items.extend(parse_autohome_page(html, source))
    return all_items


def merge_history(existing: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: dict[str, dict[str, Any]] = {item.get("id", ""): item for item in existing if item.get("id")}
    new_count = 0

    for item in fresh:
        item_id = item.get("id")
        if not item_id:
            continue
        if item_id not in merged:
            new_count += 1
        merged[item_id] = item

    def sort_key(item: dict[str, Any]) -> str:
        return item.get("published_at") or item.get("collected_at") or ""

    merged_list = sorted(merged.values(), key=sort_key, reverse=True)
    return merged_list, new_count


def normalize_item(item: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "id": item.get("id") or make_id(str(item.get("url", "")), str(item.get("title", ""))),
        "brand": item.get("brand") or "未识别品牌",
        "title": item.get("title") or "（无标题）",
        "summary": item.get("summary") or item.get("title") or "",
        "url": item.get("url") or "",
        "source": item.get("source") or "未知来源",
        "source_url": item.get("source_url") or "",
        "published_at": item.get("published_at") or now_iso(),
        "collected_at": item.get("collected_at") or now_iso(),
    }

    domains, change_type = classify_item(normalized["title"], normalized["summary"], taxonomy)
    normalized["domains"] = domains
    normalized["change_type"] = change_type
    return normalized


def render_html(items: list[dict[str, Any]], generated_at: str, used_demo: bool) -> str:
    safe_items_json = json.dumps(items, ensure_ascii=False)
    notice = (
        "当前为演示数据（网络抓取失败或手动 demo 模式）。"
        if used_demo
        else "数据来自汽车之家 OTA 专题页自动采集。"
    )

    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>OTA 竞品情报台</title>
  <style>
    :root{{--bg:#07111f;--panel:#101f33;--line:#233958;--text:#ecf4ff;--muted:#9fb2cc;--brand:#50e3c2;--accent:#6ea8fe;--warn:#ffd166;}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:radial-gradient(circle at 10% 0,#173760 0,var(--bg) 38%);color:var(--text);font-family:Inter,"Microsoft YaHei",sans-serif}}
    main{{max-width:1280px;margin:auto;padding:42px 24px 64px}}
    h1{{margin:0;font-size:clamp(28px,5vw,48px);letter-spacing:-1px}}
    .subtitle{{color:var(--muted);margin:12px 0 20px}}
    .notice{{background:#182b42;border:1px solid var(--line);border-radius:12px;padding:12px 16px;color:var(--warn);margin-bottom:18px}}
    .stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0}}
    .stat,article{{background:rgba(16,31,51,.94);border:1px solid var(--line);border-radius:16px;padding:18px}}
    .stat b{{display:block;font-size:28px;color:var(--brand)}}
    .filters{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:22px 0}}
    select{{width:100%;padding:10px;border:1px solid var(--line);border-radius:10px;background:#10233a;color:var(--text)}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:16px}}
    article{{min-height:260px;display:flex;flex-direction:column}}
    article h2{{font-size:18px;line-height:1.45;margin:10px 0}}
    article p{{color:var(--muted);line-height:1.65;flex:1}}
    .meta,.footer-note{{color:var(--muted);font-size:13px}}
    .tag{{display:inline-block;margin:3px 4px 0 0;color:#cbe4ff;background:#1b3d62;padding:4px 8px;border-radius:999px;font-size:12px}}
    .type{{color:#06141d;background:var(--brand)}}
    a{{color:#8fc0ff;text-decoration:none}}a:hover{{text-decoration:underline}}
    .footer-note{{margin-top:34px}}
  </style>
</head>
<body><main>
  <h1>OTA 竞品情报台</h1>
  <p class=\"subtitle\">自动收集 · 自动归类 · 可追溯查看</p>
  <div class=\"notice\">{escape(notice)} 更新时间：{escape(generated_at)}</div>
  <section class=\"stats\" id=\"stats\"></section>
  <section class=\"filters\">
    <select id=\"brandFilter\"></select>
    <select id=\"domainFilter\"></select>
    <select id=\"typeFilter\"></select>
  </section>
  <section class=\"grid\" id=\"cards\"></section>
  <footer class=\"footer-note\">每条内容均保留公开来源链接和机器分类依据。重要判断请回到原始来源复核。</footer>
</main>
<script>
const items = {safe_items_json};
const cards = document.querySelector('#cards');
const stats = document.querySelector('#stats');
const brandFilter = document.querySelector('#brandFilter');
const domainFilter = document.querySelector('#domainFilter');
const typeFilter = document.querySelector('#typeFilter');

function esc(v){{
  const d=document.createElement('div');
  d.textContent=v||'';
  return d.innerHTML;
}}

function buildOptions(selectNode, label, values){{
  const options = ['全部', ...Array.from(new Set(values)).filter(Boolean).sort((a,b)=>a.localeCompare(b,'zh'))];
  selectNode.innerHTML = options.map(v => `<option value="${{esc(v)}}">${{esc(label)}}：${{esc(v)}}</option>`).join('');
}}

function getFilteredItems(){{
  return items.filter(item => {{
    const brandOk = brandFilter.value === '全部' || item.brand === brandFilter.value;
    const domainOk = domainFilter.value === '全部' || (item.domains || []).includes(domainFilter.value);
    const typeOk = typeFilter.value === '全部' || item.change_type === typeFilter.value;
    return brandOk && domainOk && typeOk;
  }});
}}

function render(){{
  const shown = getFilteredItems();
  cards.innerHTML = shown.map(item => `
    <article>
      <div>
        <span class="tag type">${{esc(item.change_type)}}</span>
        ${{(item.domains || []).map(d => `<span class="tag">${{esc(d)}}</span>`).join('')}}
      </div>
      <h2>${{esc(item.title)}}</h2>
      <p>${{esc(item.summary)}}</p>
      <div class="meta">${{esc(item.brand)}} · ${{esc((item.published_at || '').slice(0,10))}} · ${{esc(item.source)}}</div>
      <a href="${{esc(item.url)}}" target="_blank" rel="noreferrer">查看原始来源 ↗</a>
    </article>
  `).join('');

  const brands = new Set(shown.map(i => i.brand));
  const domains = new Set(shown.flatMap(i => i.domains || []));
  stats.innerHTML = `
    <div class="stat"><b>${{shown.length}}</b>当前筛选条目</div>
    <div class="stat"><b>${{brands.size}}</b>覆盖品牌</div>
    <div class="stat"><b>${{domains.size}}</b>覆盖功能域</div>
  `;
}}

buildOptions(brandFilter, '品牌', items.map(i => i.brand));
buildOptions(domainFilter, '分类', items.flatMap(i => i.domains || []));
buildOptions(typeFilter, '更新类型', items.map(i => i.change_type));

[brandFilter, domainFilter, typeFilter].forEach(node => node.addEventListener('change', render));
render();
</script>
</body></html>
"""


def collect_updates(sources: list[dict[str, Any]], max_pages: int) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for source in sources:
        source_type = source.get("type")
        if source_type == "autohome_ota":
            updates.extend(fetch_autohome_updates(source, max_pages=max_pages))
    return updates


def run(demo_mode: bool = False, max_pages: int = DEFAULT_MAX_PAGES) -> dict[str, int]:
    taxonomy = load_json(TAXONOMY_FILE, default={})
    existing_items = load_json(HISTORY_FILE, default=[])

    used_demo = demo_mode
    if demo_mode:
        fresh_items = load_json(DEMO_FILE, default=[])
    else:
        sources_payload = load_json(SOURCES_FILE, default={"sources": []})
        fresh_items = collect_updates(sources_payload.get("sources", []), max_pages=max_pages)
        if not fresh_items and not existing_items:
            used_demo = True
            fresh_items = load_json(DEMO_FILE, default=[])
        elif not fresh_items and existing_items:
            used_demo = all(not str(item.get("source_url", "")).strip() for item in existing_items)

    normalized_existing = [normalize_item(item, taxonomy) for item in existing_items]
    normalized_fresh = [normalize_item(item, taxonomy) for item in fresh_items]

    merged_items, new_count = merge_history(normalized_existing, normalized_fresh)
    save_json(HISTORY_FILE, merged_items)

    generated_at = now_iso()
    html = render_html(merged_items, generated_at=generated_at, used_demo=used_demo)
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    return {
        "total": len(merged_items),
        "new": new_count,
        "used_demo": int(used_demo),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="采集并生成 OTA 监控页面")
    parser.add_argument("--demo", action="store_true", help="使用 data/demo_updates.json 生成页面")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES, help="汽车之家最多抓取页数")
    args = parser.parse_args()

    result = run(demo_mode=args.demo, max_pages=max(args.max_pages, 1))
    print(
        f"[DONE] total={result['total']} new={result['new']} demo={'yes' if result['used_demo'] else 'no'}"
    )


if __name__ == "__main__":
    main()
