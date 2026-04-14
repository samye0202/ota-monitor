#!/usr/bin/env python3
"""
OTA竞品监控 - GitHub Actions 版本
功能：抓取汽车之家OTA资讯，识别新增内容，推送到飞书群聊
"""

import requests
import json
import re
import os
import time
from datetime import datetime
from urllib.parse import urljoin

# ============ 配置 ============
BASE_URL = "https://www.autohome.com.cn/31107/0/{page}/conjunction.html"
HISTORY_FILE = "ota_history.json"
WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK", "")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
}

# 品牌关键词映射
BRAND_KEYWORDS = {
    '比亚迪': ['比亚迪', 'BYD', '腾势', '方程豹', '仰望'],
    '小鹏': ['小鹏', 'XPENG', 'G6', 'G9', 'X9', 'P7', 'MONA', '天玑'],
    '理想': ['理想', 'Li Auto', 'L7', 'L8', 'L9', 'MEGA'],
    '蔚来': ['蔚来', 'NIO', 'ET5', 'ET7', 'ES6', 'ES8', '乐道'],
    '特斯拉': ['特斯拉', 'Tesla', 'Model 3', 'Model Y', 'FSD'],
    '华为问界': ['问界', 'AITO', 'M5', 'M7', 'M9', '华为'],
    '极氪': ['极氪', 'ZEEKR', '001', '007', '009'],
    '小米': ['小米', 'Xiaomi', 'SU7'],
    '长安': ['长安', '深蓝', '启源', '阿维塔'],
    '吉利': ['吉利', '极星', '银河', '领克', 'smart'],
    '长城': ['长城', '坦克', '欧拉', '魏牌', '哈弗'],
    '广汽': ['广汽', '埃安', 'AION', '昊铂', '传祺'],
    '上汽': ['上汽', '智己', '飞凡', '荣威', '名爵'],
    '奇瑞': ['奇瑞', '星途', '捷途', 'iCAR'],
    '零跑': ['零跑', 'Leapmotor', 'C10', 'C11'],
    '哪吒': ['哪吒', 'NETA'],
    '东风': ['东风', '岚图', '猛士', '奕派'],
    '北汽': ['北汽', '极狐', 'ARCFOX', '享界'],
    '莲花': ['莲花', '路特斯', 'Lotus', 'ELETRE', 'EMEYA'],
    '保时捷': ['保时捷', 'Taycan'],
    '奔驰': ['奔驰', 'Mercedes', 'EQ', 'EQS', 'EQE'],
    '宝马': ['宝马', 'BMW', 'iX', 'i4', 'i7'],
    '奥迪': ['奥迪', 'Audi', 'e-tron'],
    '大众': ['大众', 'Volkswagen', 'ID.3', 'ID.4', 'ID.7'],
}

def load_history():
    """加载历史记录"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"articles": [], "last_update": ""}

def save_history(history):
    """保存历史记录"""
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def detect_brand(title):
    """识别品牌"""
    for brand, keywords in BRAND_KEYWORDS.items():
        for keyword in keywords:
            if keyword in title:
                return brand
    return "其他品牌"

def extract_version(title):
    """提取版本号"""
    patterns = [
        r'OTA[\s]*(\d+\.?\d*[\.\d]*)',
        r'版本[\s]*(\d+\.?\d*[\.\d]*)',
        r'V(\d+\.?\d*[\.\d]*)',
        r'(\d+\.\d+\.\d+)',
        r'天玑[\s]*(\d+\.?\d*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

def fetch_article_detail(article_url):
    """抓取文章详情页"""
    try:
        print(f"  📄 抓取详情: {article_url[:50]}...")
        response = requests.get(article_url, headers=HEADERS, timeout=15)
        response.encoding = 'gb2312'
        html = response.text
        
        detail = {"full_content": "", "update_items": [], "modules": []}
        
        # 提取正文
        content_match = re.search(r'<div[^>]*class=["\']text-content["\'][^>]*>(.*?)</div>', 
                                   html, re.DOTALL | re.IGNORECASE)
        if content_match:
            content = content_match.group(1)
            content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r'<[^>]+>', ' ', content)
            content = re.sub(r'&nbsp;', ' ', content)
            content = re.sub(r'\s+', ' ', content).strip()
            detail["full_content"] = content[:2000] if len(content) > 2000 else content
        
        # 解析OTA更新内容
        detail["update_items"] = parse_ota_updates(detail["full_content"])
        detail["modules"] = detect_modules(detail["full_content"])
        
        return detail
    except Exception as e:
        print(f"  ⚠️ 详情页失败: {str(e)[:40]}")
        return None

def parse_ota_updates(content):
    """解析OTA具体更新内容"""
    items = []
    if not content:
        return items
    
    keywords = [
        r'新增([^，。；]{3,25})',
        r'优化([^，。；]{3,25})',
        r'升级([^，。；]{3,25})',
        r'支持([^，。；]{3,25})',
        r'上线([^，。；]{3,25})',
    ]
    
    for pattern in keywords:
        matches = re.findall(pattern, content)
        for match in matches:
            item = match.strip()
            if len(item) > 5 and len(item) < 35 and item not in items:
                items.append(item)
    
    return items[:5]

def detect_modules(content):
    """检测升级模块"""
    modules = []
    if not content:
        return modules
    
    module_keywords = {
        "智能座舱": ["车机", "座舱", "语音", "导航", "音乐", "APP"],
        "智能驾驶": ["辅助驾驶", "智驾", "NOA", "NGP", "泊车"],
        "底盘操控": ["底盘", "制动", "刹车", "转向", "驾驶模式"],
        "车控系统": ["车控", "车身", "空调", "座椅", "灯光"],
        "智能网联": ["网络", "互联", "CarPlay", "手机"],
    }
    
    content_lower = content.lower()
    for module, keywords in module_keywords.items():
        for keyword in keywords:
            if keyword in content_lower:
                if module not in modules:
                    modules.append(module)
                break
    
    return modules if modules else ["智能座舱"]

def fetch_ota_news(page=1):
    """抓取OTA新闻列表"""
    url = BASE_URL.format(page=page)
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.encoding = 'gb2312'
        html = response.text
        
        articles = []
        pattern = r'<li[^>]*>.*?<h3>.*?<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>.*?</h3>.*?</li>'
        matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)
        
        for article_url, title_html in matches:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if not title or 'OTA' not in title:
                continue
            
            # 补全URL
            article_url = article_url.strip()
            if article_url.startswith('/'):
                article_url = f"https://www.autohome.com.cn{article_url}"
            elif article_url.startswith('www.'):
                article_url = f"https://{article_url}"
            
            # 提取文章ID
            article_id_match = re.search(r'/news/(\d+)/(\d+)\.html', article_url)
            article_id = article_id_match.group(2) if article_id_match else article_url
            
            brand = detect_brand(title)
            version = extract_version(title)
            
            articles.append({
                "id": article_id,
                "title": title,
                "url": article_url,
                "brand": brand,
                "version": version,
            })
        
        return articles
    except Exception as e:
        print(f"⚠️ 页面 {page} 失败: {str(e)[:40]}")
        return []

def send_to_feishu(message_data):
    """发送消息到飞书群聊"""
    if not WEBHOOK_URL:
        print("⚠️ 未配置 FEISHU_WEBHOOK，跳过推送")
        return False
    
    try:
        headers = {"Content-Type": "application/json"}
        response = requests.post(WEBHOOK_URL, json=message_data, headers=headers, timeout=15)
        result = response.json()
        if result.get("code") == 0:
            print("✅ 消息发送成功")
            return True
        else:
            print(f"⚠️ 发送失败: {result}")
            return False
    except Exception as e:
        print(f"⚠️ 发送异常: {str(e)[:40]}")
        return False

def format_message(new_articles):
    """格式化飞书消息"""
    if not new_articles:
        return None
    
    lines = [
        f"📢 OTA行业动态更新 ({datetime.now().strftime('%m月%d日')})",
        "",
        f"本次监测发现 **{len(new_articles)}** 条新OTA资讯",
        ""
    ]
    
    # 按品牌分组
    brand_groups = {}
    for article in new_articles:
        brand = article.get("brand", "其他品牌")
        if brand not in brand_groups:
            brand_groups[brand] = []
        brand_groups[brand].append(article)
    
    # 只显示前5个品牌
    for brand, articles in sorted(brand_groups.items(), key=lambda x: -len(x[1]))[:5]:
        lines.append(f"📌 **{brand}** ({len(articles)}条)")
        
        for article in articles[:2]:  # 每个品牌最多2条
            version_str = f" (版本: {article.get('version')})" if article.get('version') else ""
            lines.append(f"• {article['title']}{version_str}")
            
            # 添加更新内容
            if article.get("detail") and article["detail"].get("update_items"):
                items = article["detail"]["update_items"][:2]
                for item in items:
                    lines.append(f"  📝 {item}")
        
        if len(articles) > 2:
            lines.append(f"  _...还有 {len(articles)-2} 条_")
        lines.append("")
    
    lines.append(f"⏰ 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("📊 数据来源: 汽车之家")
    
    return {
        "msg_type": "text",
        "content": {"text": "\n".join(lines)}
    }

def main():
    """主函数"""
    print("=" * 60)
    print("🚗 OTA竞品监控 - GitHub Actions")
    print("=" * 60)
    print(f"开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Webhook: {'已配置' if WEBHOOK_URL else '未配置'}")
    print()
    
    # 加载历史
    history = load_history()
    existing_ids = {a.get("id") for a in history.get("articles", [])}
    print(f"📚 历史: {len(existing_ids)} 篇")
    
    # 抓取列表
    all_articles = []
    for page in range(1, 3):
        print(f"📄 抓取第 {page} 页...")
        articles = fetch_ota_news(page)
        all_articles.extend(articles)
        print(f"   找到 {len(articles)} 篇")
        time.sleep(1)
    
    # 去重
    seen_ids = set()
    unique_articles = []
    for a in all_articles:
        if a["id"] not in seen_ids:
            seen_ids.add(a["id"])
            unique_articles.append(a)
    all_articles = unique_articles
    print(f"✅ 列表完成: {len(all_articles)} 篇")
    
    # 识别新增
    new_articles = [a for a in all_articles if a.get("id") not in existing_ids]
    print(f"🆕 新增: {len(new_articles)} 篇")
    print()
    
    # 抓取详情（限制前10篇避免超时）
    if new_articles:
        print("📄 抓取详情...")
        for i, article in enumerate(new_articles[:10]):
            print(f"  [{i+1}/{min(len(new_articles),10)}] {article['title'][:30]}...")
            detail = fetch_article_detail(article["url"])
            if detail:
                article["detail"] = detail
                print(f"    ✅ {len(detail.get('update_items', []))} 项更新")
            time.sleep(0.5)
        print()
    
    # 保存历史
    if new_articles:
        history["articles"].extend(new_articles)
        history["last_update"] = datetime.now().isoformat()
        save_history(history)
        print(f"💾 已保存: {len(history['articles'])} 篇")
    
    # 推送飞书
    if new_articles and WEBHOOK_URL:
        print()
        print("📤 推送飞书...")
        message = format_message(new_articles)
        if message:
            send_to_feishu(message)
    else:
        print("📤 无新增或未配置Webhook，跳过推送")
    
    # 输出摘要
    print()
    print("=" * 60)
    print(f"✅ 完成 | 抓取: {len(all_articles)} | 新增: {len(new_articles)}")
    print("=" * 60)

if __name__ == "__main__":
    main()
