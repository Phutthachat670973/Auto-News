# news.py
# ============================================================================================================
# Purpose (UPDATED)
# - ส่งออก LINE แค่ "Project Impact" (ข่าวโครงการ) เท่านั้น
# - แต่สร้าง "Daily Focus" จากแนวคิด Energy Digest เพื่อเป็น PROMPT CONTEXT ช่วยคัดข่าวโครงการให้แม่นขึ้น
#
# ENV ที่สำคัญ:
#   LINE_CHANNEL_ACCESS_TOKEN=...
#   GROQ_API_KEY=...
#   GOOGLE_NEWS_QUERY="(energy OR oil OR gas OR LNG OR OPEC OR geopolitics OR sanctions OR pipeline) (Thailand OR PTTEP OR PTT OR Qatar OR UAE OR Oman OR Malaysia OR Myanmar)"
#   GOOGLE_NEWS_HL=th
#   GOOGLE_NEWS_GL=TH
#   GOOGLE_NEWS_CEID=TH:th
#
# ตัวเลือก:
#   SELECT_LIMIT=60
#   PROJECT_SEND_LIMIT=10
#   SHOW_SOURCE_RATING=true|false
#   MIN_SOURCE_SCORE=0.40
#   USE_KEYWORD_GATE=false|true
#   DRY_RUN=false|true
# ============================================================================================================

from __future__ import annotations

import os
import re
import json
import time
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import pytz
import requests
from dateutil import parser as dateutil_parser

# ============================================================================================================
# ENV
# ============================================================================================================

USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (NewsBot/1.0)").strip()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")
if not GROQ_API_KEY:
    raise RuntimeError("ไม่พบ GROQ_API_KEY")

SELECT_LIMIT = int(os.getenv("SELECT_LIMIT", "60"))
PROJECT_SEND_LIMIT = int(os.getenv("PROJECT_SEND_LIMIT", "10"))

TRACK_DIR = os.getenv("TRACK_DIR", "sent_links").strip()

SHOW_SOURCE_RATING = os.getenv("SHOW_SOURCE_RATING", "true").strip().lower() == "true"
MIN_SOURCE_SCORE = float(os.getenv("MIN_SOURCE_SCORE", "0.40"))

USE_KEYWORD_GATE = os.getenv("USE_KEYWORD_GATE", "false").strip().lower() == "true"

ADD_SECTION_HEADERS = os.getenv("ADD_SECTION_HEADERS", "true").strip().lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"

DEFAULT_HERO_URL = os.getenv("DEFAULT_HERO_URL", "https://i.imgur.com/4M34hi2.png").strip()
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "10"))

# สร้าง Daily Focus จากข่าวกี่ชิ้น (ยิ่งมากยิ่งแม่น แต่กิน token มากขึ้น)
FOCUS_BUILD_LIMIT = int(os.getenv("FOCUS_BUILD_LIMIT", "14"))

bangkok_tz = pytz.timezone("Asia/Bangkok")

# ============================================================================================================
# RSS FEEDS (Google News ONLY)
# ============================================================================================================

RSS_FEEDS: List[Dict[str, str]] = [
    {"name": "GoogleNews", "url": "", "country": "Multi"},
]

GOOGLE_NEWS_QUERY = os.getenv("GOOGLE_NEWS_QUERY", "").strip()
GOOGLE_NEWS_HL = os.getenv("GOOGLE_NEWS_HL", "th").strip()
GOOGLE_NEWS_GL = os.getenv("GOOGLE_NEWS_GL", "TH").strip()
GOOGLE_NEWS_CEID = os.getenv("GOOGLE_NEWS_CEID", "TH:th").strip()

def build_google_news_rss(query: str) -> str:
    from urllib.parse import quote_plus
    q = quote_plus(query or "")
    return f"https://news.google.com/rss/search?q={q}&hl={GOOGLE_NEWS_HL}&gl={GOOGLE_NEWS_GL}&ceid={GOOGLE_NEWS_CEID}"

if RSS_FEEDS and RSS_FEEDS[0].get("name") == "GoogleNews":
    RSS_FEEDS[0]["url"] = (
        build_google_news_rss(GOOGLE_NEWS_QUERY)
        if GOOGLE_NEWS_QUERY
        else f"https://news.google.com/rss?hl={GOOGLE_NEWS_HL}&gl={GOOGLE_NEWS_GL}&ceid={GOOGLE_NEWS_CEID}"
    )

# ============================================================================================================
# Helpers
# ============================================================================================================

def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def clip(s: str, n: int) -> str:
    s = clean_ws(s)
    return s if len(s) <= n else s[:n] + "…"

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def normalize_url(url: str) -> str:
    try:
        u = (url or "").strip()
        if not u:
            return u
        p = urlparse(u)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in ("utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid")]
        return urlunparse(p._replace(query=urlencode(q), fragment=""))
    except Exception:
        return (url or "").strip()

def load_sent_links() -> set:
    ensure_dir(TRACK_DIR)
    fp = os.path.join(TRACK_DIR, "sent_links.txt")
    if not os.path.exists(fp):
        return set()
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return set([line.strip() for line in f if line.strip()])
    except Exception:
        return set()

def save_sent_links(links: List[str]) -> None:
    ensure_dir(TRACK_DIR)
    fp = os.path.join(TRACK_DIR, "sent_links.txt")
    old = load_sent_links()
    new = old.union(set([normalize_url(x) for x in links if x]))
    with open(fp, "w", encoding="utf-8") as f:
        for x in sorted(new):
            f.write(x + "\n")

def dedupe_news(items: List[Dict[str, Any]], sent: set) -> List[Dict[str, Any]]:
    out = []
    seen = set(sent)
    for it in items:
        link = normalize_url(it.get("link", ""))
        if not link:
            continue
        if link in seen:
            continue
        seen.add(link)
        out.append(it)
    return out

def parse_datetime(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = dateutil_parser.parse(s)
        if dt.tzinfo is None:
            dt = bangkok_tz.localize(dt)
        return dt.astimezone(bangkok_tz)
    except Exception:
        return None

def http_get(url: str, timeout: int = 15) -> requests.Response:
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)

def resolve_final_url(url: str) -> str:
    try:
        u = normalize_url(url)
        if not u:
            return u
        try:
            r = requests.head(u, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=12)
            if getattr(r, "url", None):
                return normalize_url(r.url)
        except Exception:
            pass
        r2 = http_get(u, timeout=15)
        return normalize_url(r2.url or u)
    except Exception:
        return normalize_url(url)

def extract_og_image(url: str) -> Optional[str]:
    try:
        r = http_get(url, timeout=15)
        html = r.text or ""
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r'<meta[^>]+name=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            return m.group(1).strip()
        return None
    except Exception:
        return None

# ============================================================================================================
# Source rating (heuristic)
# ============================================================================================================

HIGH_TRUST = {
    "reuters.com", "apnews.com", "bbc.com", "ft.com", "bloomberg.com",
    "wsj.com", "nytimes.com", "theguardian.com", "economist.com",
    "iea.org", "opec.org", "worldbank.org", "imf.org",
}
MID_TRUST = {
    "cnbc.com", "forbes.com", "investing.com", "oilprice.com",
    "prachachat.net", "bangkokbiznews.com", "posttoday.com",
}

def domain_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""

def source_score(url: str) -> float:
    d = domain_of(url)
    if not d:
        return 0.0
    if d in HIGH_TRUST:
        return 0.90
    if d in MID_TRUST:
        return 0.65
    if d.endswith(".go.th") or d.endswith(".gov") or d.endswith(".gov.uk"):
        return 0.85
    if d.endswith(".edu") or d.endswith(".ac.th"):
        return 0.80
    return 0.45

# ============================================================================================================
# Keyword gate (optional)
# ============================================================================================================

KEYWORDS = [
    "oil","gas","lng","energy","opec","refinery","crude",
    "sanction","geopolitic","pipeline","shipping","tariff",
    "thailand","ptt","pttep","gulf","qatar","uae","oman","malaysia","myanmar",
]

def keyword_hit(n: Dict[str, Any]) -> bool:
    blob = (n.get("title","") + " " + n.get("summary","")).lower()
    return any(k in blob for k in KEYWORDS)

# ============================================================================================================
# RSS loading (Google News only)
# ============================================================================================================

def fetch_feed(feed: Dict[str, str]) -> List[Dict[str, Any]]:
    d = feedparser.parse(feed["url"])
    items: List[Dict[str, Any]] = []
    for e in d.entries:
        link = e.get("link", "") or ""
        title = (e.get("title", "") or "").strip()
        summary = (e.get("summary", "") or e.get("description", "") or "").strip()

        published = None
        for k in ("published", "updated", "pubDate"):
            if e.get(k):
                published = parse_datetime(e.get(k))
                if published:
                    break

        items.append({
            "feed_name": feed.get("name", "feed"),
            "feed_country": feed.get("country", "Global"),
            "title": clip(title, 220),
            "summary": clip(summary, 600),
            "link": normalize_url(link),
            "published": published,
        })
    return items

def load_news() -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    for f in RSS_FEEDS:
        try:
            items = fetch_feed(f)
            print(f"[FEED] {f.get('name')} -> {len(items)} items")
            all_items.extend(items)
        except Exception as e:
            print("[FEED ERROR]", f.get("name"), f.get("url"), e)

    all_items.sort(key=lambda x: x.get("published") or datetime.min.replace(tzinfo=bangkok_tz), reverse=True)
    return all_items

# ============================================================================================================
# LLM (Groq)
# ============================================================================================================

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def groq_chat(prompt: str, temperature: float = 0.25) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "คุณคือผู้ช่วยวิเคราะห์ข่าวภาษาไทยแบบกระชับและแม่นยำ ห้ามเดานอกข้อมูล"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip()

def parse_json_loose(s: str) -> Optional[Dict[str, Any]]:
    try:
        s2 = s.strip()
        s2 = re.sub(r"^```(json)?", "", s2).strip()
        s2 = re.sub(r"```$", "", s2).strip()
        return json.loads(s2)
    except Exception:
        return None

def enforce_thai(s: str) -> str:
    return clean_ws(s)

# ============================================================================================================
# NEW: Daily Focus (ใช้แนวคิด Energy Digest เป็น "Prompt Context" ไม่ส่งออก)
# ============================================================================================================

DAILY_FOCUS_PROMPT = """
คุณจะได้รับรายการข่าวหลายชิ้น (title + summary) ให้สรุปเป็น "Daily Focus" เพื่อใช้เป็นบริบทช่วยคัดข่าวโครงการ

โฟกัส: พลังงาน/การเมือง/การเงิน/ซัพพลายเชน/ราคาน้ำมัน-ก๊าซ/มาตรการรัฐ/ความเสี่ยงภูมิรัฐศาสตร์
สิ่งที่ต้องการ:
- ตอบเป็นข้อความล้วน (ไม่ต้องเป็น JSON)
- 4–6 bullet สั้น ๆ (ขึ้นต้นด้วย "• ")
- เขียนเป็นแนว "ประเด็นที่ควรโฟกัสวันนี้" ไม่ต้องเล่าข่าวรายชิ้น
- ห้ามเดานอกข้อมูลจาก title/summary ที่ให้

ข่าว (ตัวอย่างรายการ):
{items_text}

Daily Focus:
"""

def build_daily_focus(items: List[Dict[str, Any]]) -> str:
    sample = items[:max(1, FOCUS_BUILD_LIMIT)]
    lines = []
    for i, n in enumerate(sample, 1):
        lines.append(f"[{i}] {clip(n.get('title',''), 180)} | {clip(n.get('summary',''), 260)}")
    items_text = "\n".join(lines).strip()
    raw = groq_chat(DAILY_FOCUS_PROMPT.format(items_text=items_text), temperature=0.2)
    # กันยาวเกิน: เอาแค่ไม่กี่บรรทัด
    raw = raw.strip()
    raw_lines = [l.strip() for l in raw.splitlines() if l.strip()]
    raw_lines = raw_lines[:8]
    return "\n".join(raw_lines).strip()

# ============================================================================================================
# Project Impact prompt (UPDATED: ใส่ Daily Focus เป็น context)
# ============================================================================================================

PROJECT_PROMPT_TMPL = """
คุณจะได้รับข่าว 1 รายการ (title/summary/url/published) และ "Daily Focus" ของวัน
ให้ตอบเป็น JSON เท่านั้น

Daily Focus (ใช้เป็นบริบท/แนวโน้มของวันเพื่อช่วยตัดสินใจ):
{daily_focus}

เป้าหมาย: คัดกรองข่าวที่ "มีผลกระทบต่อโครงการ/ประเทศ" (พลังงาน การเมือง การเงิน ซัพพลายเชน นโยบาย ราคาน้ำมัน/ก๊าซ ฯลฯ)

กติกา:
- ตอบ JSON เท่านั้น ห้ามมีข้อความอื่น
- pass: true/false
- country: ประเทศที่เกี่ยวข้อง (ถ้าไม่แน่ใจให้เดาจากบริบทให้น้อยที่สุด ถ้าไม่รู้ให้ใช้ "-")
- project: ชื่อโครงการ (ถ้าไม่รู้ให้ "-")
- category: Energy/Politics/Finance/SupplyChain/Other
- impact: bullet เดียว (ขึ้นต้นด้วย "• ") 3–4 ประโยค ภาษาไทย โทนรายงานข่าว อธิบายว่ากระทบอย่างไร (ห้ามเดามั่ว)
- หลักคิด: ถ้าข่าวสอดคล้อง/เชื่อมโยงกับ Daily Focus และมีนัยต่อความเสี่ยง/ต้นทุน/นโยบาย ให้พิจารณา pass มากขึ้น

ข่าว:
TITLE: {title}
SUMMARY: {summary}
URL: {url}
PUBLISHED: {published}

ตอบ JSON:
"""

def groq_batch_tag_and_filter(items: List[Dict[str, Any]], daily_focus: str, chunk_size: int = 10) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in range(0, len(items), chunk_size):
        chunk = items[i:i+chunk_size]
        for n in chunk:
            prompt = PROJECT_PROMPT_TMPL.format(
                daily_focus=daily_focus or "• ไม่มีข้อมูลแนวโน้มของวัน",
                title=clip(n.get("title",""), 220),
                summary=clip(n.get("summary",""), 700),
                url=n.get("final_url") or n.get("link",""),
                published=str(n.get("published") or ""),
            )
            raw = groq_chat(prompt, temperature=0.25)
            js = parse_json_loose(raw) or {"pass": False}
            out.append(js)
            time.sleep(0.35)
    return out

# ============================================================================================================
# LINE Sender
# ============================================================================================================

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/broadcast"

def create_text_messages(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    chunks = []
    s = text
    while s:
        chunks.append(s[:4900])
        s = s[4900:]
    return [{"type": "text", "text": c} for c in chunks]

def create_flex(news: Dict[str, Any]) -> Dict[str, Any]:
    hero = news.get("hero") or DEFAULT_HERO_URL
    title = (news.get("title") or "")[:80]
    impact = (news.get("impact") or "").strip()
    country = (news.get("country") or "-").strip()
    project = (news.get("project") or "-").strip()
    category = (news.get("category") or "-").strip()
    link = (news.get("final_url") or news.get("link") or "").strip()

    score = float(news.get("source_score") or 0.0)
    src_txt = f"ความน่าเชื่อถือ: {score:.2f}" if SHOW_SOURCE_RATING else ""

    body = [
        {"type": "text", "text": title or "ข่าว", "weight": "bold", "wrap": True, "size": "md"},
        {"type": "text", "text": f"ประเทศ: {country}", "wrap": True, "size": "sm", "color": "#555555"},
        {"type": "text", "text": f"โครงการ: {project}", "wrap": True, "size": "sm", "color": "#555555"},
        {"type": "text", "text": f"ประเภท: {category}", "wrap": True, "size": "sm", "color": "#555555"},
    ]
    if src_txt:
        body.append({"type": "text", "text": src_txt, "wrap": True, "size": "xs", "color": "#888888"})

    if impact:
        body.append({"type": "separator", "margin": "md"})
        body.append({"type": "text", "text": impact, "wrap": True, "size": "sm"})

    bubble = {
        "type": "bubble",
        "hero": {"type": "image", "url": hero, "size": "full", "aspectMode": "cover", "aspectRatio": "20:13"},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "button", "style": "primary", "action": {"type": "uri", "label": "อ่านข่าว", "uri": link or "https://news.google.com"}}
        ]},
    }
    return {"type": "flex", "altText": title or "Project Impact", "contents": bubble}

def send_to_line(messages: List[Dict[str, Any]]) -> None:
    if DRY_RUN:
        print("[DRY_RUN] messages =", len(messages))
        for m in messages[:3]:
            print(json.dumps(m, ensure_ascii=False)[:800], "...\n")
        return

    if not messages:
        return

    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

    BATCH = 5
    sent = 0
    for i in range(0, len(messages), BATCH):
        chunk = messages[i:i+BATCH]
        payload = {"messages": chunk}
        r = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=60)
        if r.status_code >= 400:
            print("[LINE ERROR]", r.status_code, r.text[:500])
            r.raise_for_status()
        sent += len(chunk)
        time.sleep(0.2)

    print(f"ส่งสำเร็จ: {sent} messages")

# ============================================================================================================
# Prepare items
# ============================================================================================================

def prepare_items(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for n in raw:
        link = n.get("link", "")
        if not link:
            continue
        final_url = resolve_final_url(link)
        hero = extract_og_image(final_url) or DEFAULT_HERO_URL
        sc = source_score(final_url)
        n2 = dict(n)
        n2["final_url"] = final_url
        n2["hero"] = hero
        n2["source_score"] = sc
        out.append(n2)
    return out

# ============================================================================================================
# Project runner (ONLY)
# ============================================================================================================

def run_project_mode_only(selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    if USE_KEYWORD_GATE:
        selected = [x for x in selected if keyword_hit(x)]
    selected = [x for x in selected if float(x.get("source_score", 0.0)) >= MIN_SOURCE_SCORE]

    # ✅ สร้าง Daily Focus จากชุดข่าวเดียวกัน (ไม่ส่งออก)
    daily_focus = build_daily_focus(selected)
    print("\n[DAILY_FOCUS]\n" + daily_focus + "\n")

    tags = groq_batch_tag_and_filter(selected, daily_focus=daily_focus, chunk_size=LLM_BATCH_SIZE)

    passed = []
    for n, t in zip(selected, tags):
        if not isinstance(t, dict) or not t.get("pass"):
            continue
        n2 = dict(n)
        n2["country"] = (t.get("country") or n.get("feed_country") or "Global").strip()
        n2["project"] = (t.get("project") or "-").strip()
        n2["category"] = (t.get("category") or "Other").strip()
        n2["impact"] = enforce_thai((t.get("impact") or "").strip())
        passed.append(n2)

    passed.sort(key=lambda x: x.get("published") or datetime.min.replace(tzinfo=bangkok_tz), reverse=True)
    passed = passed[:PROJECT_SEND_LIMIT]

    if not passed:
        return (create_text_messages("ไม่พบข่าวที่มีผลกระทบต่อโครงการตามเงื่อนไข"), [])

    msgs = [create_flex(n) for n in passed]
    links = [x.get("final_url") or x.get("link") for x in passed if (x.get("final_url") or x.get("link"))]
    return (msgs, links)

# ============================================================================================================
# Main
# ============================================================================================================

def main() -> None:
    print("ดึงข่าว...")
    raw = load_news()
    print("จำนวนข่าวดิบทั้งหมด:", len(raw))

    sent = load_sent_links()
    raw = dedupe_news(raw, sent)
    print("หลังตัดข่าวซ้ำ/เคยส่ง:", len(raw))

    selected = raw[:max(1, SELECT_LIMIT)]
    selected = prepare_items(selected)

    all_msgs: List[Dict[str, Any]] = []
    all_links: List[str] = []

    if ADD_SECTION_HEADERS:
        all_msgs += create_text_messages("📌 สรุปข่าวผลกระทบต่อโครงการ (Project Impact)")

    msgs, links = run_project_mode_only(selected)
    all_msgs += msgs
    all_links += links

    send_to_line(all_msgs)
    save_sent_links(all_links)

if __name__ == "__main__":
    main()
