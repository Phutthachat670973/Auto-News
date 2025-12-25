# news.py (FINAL)
# ============================================================================================================
# ✅ แหล่งข่าว: Google News RSS อย่างเดียว
# ✅ ส่ง LINE: Flex Message แบบ Carousel (หลายข่าวใน 1 Flex) + Bubble size mega + ปุ่มสีเขียว
# ✅ โหมด: project_only / digest_only / both (ควบคุมด้วย OUTPUT_MODE)
# ✅ Daily Focus: สร้างเพื่อเป็น prompt context ช่วยคัดข่าวโครงการ (ไม่ส่งออก)
# ✅ กัน 429: retry/backoff + sleep jitter (SLEEP_MIN/SLEEP_MAX) + batch ลดจำนวน request
# ✅ กัน 413: จำกัดขนาด prompt + adaptive split อัตโนมัติ
# ✅ Tracking: sent_links/ กันส่งซ้ำ และ commit กลับ repo (ตาม workflow)
#
# ENV (จาก workflow ของคุณ):
#   LINE_CHANNEL_ACCESS_TOKEN, GROQ_API_KEY
#   GROQ_MODEL_NAME, OUTPUT_MODE, ADD_SECTION_HEADERS
#   SELECT_LIMIT, LLM_BATCH_SIZE, MAX_RETRIES, SLEEP_MIN, SLEEP_MAX
#   PROJECT_SEND_LIMIT, MIN_SOURCE_SCORE, SHOW_SOURCE_RATING, USE_KEYWORD_GATE, ENABLE_IMPACT_REWRITE
#   DIGEST_MAX_PER_SECTION, USER_AGENT, DEFAULT_HERO_URL, TRACK_DIR, DRY_RUN
# Optional:
#   GOOGLE_NEWS_QUERY, GOOGLE_NEWS_HL, GOOGLE_NEWS_GL, GOOGLE_NEWS_CEID
#   MAX_PROMPT_CHARS, FOCUS_BUILD_LIMIT
# ============================================================================================================

from __future__ import annotations

import os
import re
import json
import time
import random
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import pytz
import requests
from dateutil import parser as dateutil_parser

# ============================================================
# ENV / CONFIG
# ============================================================

USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (NewsBot/1.0)").strip()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")
if not GROQ_API_KEY:
    raise RuntimeError("ไม่พบ GROQ_API_KEY")

# Model name: รองรับทั้ง GROQ_MODEL_NAME (ตาม workflow) และ GROQ_MODEL (เผื่อไว้)
GROQ_MODEL = (os.getenv("GROQ_MODEL_NAME") or os.getenv("GROQ_MODEL") or "llama-3.1-8b-instant").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

OUTPUT_MODE = os.getenv("OUTPUT_MODE", "project_only").strip().lower()  # both | project_only | digest_only
ADD_SECTION_HEADERS = os.getenv("ADD_SECTION_HEADERS", "true").strip().lower() == "true"

SELECT_LIMIT = int(os.getenv("SELECT_LIMIT", "25"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "10"))
PROJECT_SEND_LIMIT = int(os.getenv("PROJECT_SEND_LIMIT", "10"))
DIGEST_MAX_PER_SECTION = int(os.getenv("DIGEST_MAX_PER_SECTION", "8"))

MIN_SOURCE_SCORE = float(os.getenv("MIN_SOURCE_SCORE", "0"))
SHOW_SOURCE_RATING = os.getenv("SHOW_SOURCE_RATING", "true").strip().lower() == "true"
USE_KEYWORD_GATE = os.getenv("USE_KEYWORD_GATE", "false").strip().lower() == "true"
ENABLE_IMPACT_REWRITE = os.getenv("ENABLE_IMPACT_REWRITE", "true").strip().lower() == "true"

DEFAULT_HERO_URL = os.getenv("DEFAULT_HERO_URL", "").strip() or "https://i.imgur.com/4M34hi2.png"

TRACK_DIR = os.getenv("TRACK_DIR", "sent_links").strip()
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"

# Retry/Rate limit control (ตาม workflow)
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "7"))
SLEEP_MIN = float(os.getenv("SLEEP_MIN", "0.4"))
SLEEP_MAX = float(os.getenv("SLEEP_MAX", "0.9"))

# กัน 413 payload too large (ตั้งเป็นจำนวนตัวอักษรแบบง่าย)
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "18000"))

# Daily Focus
FOCUS_BUILD_LIMIT = int(os.getenv("FOCUS_BUILD_LIMIT", "8"))

bangkok_tz = pytz.timezone("Asia/Bangkok")

# ============================================================
# Google News RSS (แหล่งเดียว)
# ============================================================

GOOGLE_NEWS_QUERY = os.getenv("GOOGLE_NEWS_QUERY", "").strip()
GOOGLE_NEWS_HL = os.getenv("GOOGLE_NEWS_HL", "th").strip()
GOOGLE_NEWS_GL = os.getenv("GOOGLE_NEWS_GL", "TH").strip()
GOOGLE_NEWS_CEID = os.getenv("GOOGLE_NEWS_CEID", "TH:th").strip()


def build_google_news_rss(query: str) -> str:
    from urllib.parse import quote_plus
    q = quote_plus(query or "")
    return f"https://news.google.com/rss/search?q={q}&hl={GOOGLE_NEWS_HL}&gl={GOOGLE_NEWS_GL}&ceid={GOOGLE_NEWS_CEID}"


RSS_FEEDS: List[Dict[str, str]] = [
    {
        "name": "GoogleNews",
        "country": "Multi",
        "url": (build_google_news_rss(GOOGLE_NEWS_QUERY) if GOOGLE_NEWS_QUERY
                else f"https://news.google.com/rss?hl={GOOGLE_NEWS_HL}&gl={GOOGLE_NEWS_GL}&ceid={GOOGLE_NEWS_CEID}")
    }
]

# ============================================================
# Helpers
# ============================================================

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
             if k.lower() not in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                                 "fbclid", "gclid")]
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


# ============================================================
# HTTP / final URL / OG image
# ============================================================

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


# ============================================================
# Source score (heuristic)
# ============================================================

HIGH_TRUST = {
    "reuters.com", "apnews.com", "bbc.com", "ft.com", "bloomberg.com",
    "wsj.com", "nytimes.com", "theguardian.com", "economist.com",
    "iea.org", "opec.org", "worldbank.org", "imf.org",
}
MID_TRUST = {
    "cnbc.com", "forbes.com", "investing.com",
    "oilprice.com", "prachachat.net", "bangkokbiznews.com", "posttoday.com",
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


# ============================================================
# Optional keyword gate
# ============================================================

KEYWORDS = [
    "oil", "gas", "lng", "energy", "opec", "refinery", "crude",
    "sanction", "geopolitic", "pipeline", "shipping", "tariff",
    "thailand", "ptt", "pttep", "gulf", "qatar", "uae", "oman", "malaysia", "myanmar",
]


def keyword_hit(n: Dict[str, Any]) -> bool:
    blob = (n.get("title", "") + " " + n.get("summary", "")).lower()
    return any(k in blob for k in KEYWORDS)


# ============================================================
# RSS loading
# ============================================================

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


# ============================================================
# Groq LLM: retry/backoff + sleep jitter
# ============================================================

def _sleep_jitter():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def groq_chat(prompt: str, temperature: float = 0.25) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "คุณคือผู้ช่วยวิเคราะห์ข่าวภาษาไทยแบบกระชับและแม่นยำ ห้ามเดานอกข้อมูล"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }

    backoff = 2.0
    for attempt in range(MAX_RETRIES):
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)

        if r.status_code == 429:
            retry_after = r.headers.get("retry-after")
            try:
                wait_s = float(retry_after) if retry_after else backoff
            except Exception:
                wait_s = backoff
            print(f"[429] rate limited -> sleep {wait_s:.1f}s (attempt {attempt+1}/{MAX_RETRIES})")
            time.sleep(wait_s)
            backoff = min(backoff * 1.8, 35.0)
            continue

        if r.status_code == 413:
            raise requests.HTTPError("413 Payload Too Large", response=r)

        if r.status_code >= 500:
            print(f"[{r.status_code}] server error -> sleep {backoff:.1f}s")
            time.sleep(backoff)
            backoff = min(backoff * 1.8, 35.0)
            continue

        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()

    raise RuntimeError("Groq: retry แล้วแต่ยังไม่สำเร็จ")


def parse_json_loose(s: str) -> Optional[Any]:
    try:
        s2 = s.strip()
        s2 = re.sub(r"^```(json)?", "", s2).strip()
        s2 = re.sub(r"```$", "", s2).strip()
        return json.loads(s2)
    except Exception:
        return None


# ============================================================
# Daily Focus (ใช้เป็น prompt context ไม่ส่งออก)
# ============================================================

DAILY_FOCUS_PROMPT = """
คุณจะได้รับรายการข่าวหลายชิ้น (title + summary) ให้สรุปเป็น "Daily Focus" เพื่อใช้เป็นบริบทช่วยคัดข่าวโครงการ

โฟกัส: พลังงาน/การเมือง/การเงิน/ซัพพลายเชน/ราคาน้ำมัน-ก๊าซ/มาตรการรัฐ/ความเสี่ยงภูมิรัฐศาสตร์
สิ่งที่ต้องการ:
- ตอบเป็นข้อความล้วน (ไม่ต้องเป็น JSON)
- 4–6 bullet สั้น ๆ (ขึ้นต้นด้วย "• ")
- เขียนเป็นแนว "ประเด็นที่ควรโฟกัสวันนี้" ไม่ต้องเล่าข่าวรายชิ้น
- ห้ามเดานอกข้อมูลจาก title/summary ที่ให้

ข่าว (รายการ):
{items_text}

Daily Focus:
"""


def build_daily_focus(items: List[Dict[str, Any]]) -> str:
    sample = items[:max(1, FOCUS_BUILD_LIMIT)]
    lines = []
    for i, n in enumerate(sample, 1):
        lines.append(f"[{i}] {clip(n.get('title',''), 150)} | {clip(n.get('summary',''), 210)}")
    items_text = "\n".join(lines).strip()

    prompt = DAILY_FOCUS_PROMPT.format(items_text=items_text)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]

    _sleep_jitter()
    raw = groq_chat(prompt, temperature=0.2)
    raw_lines = [l.strip() for l in raw.splitlines() if l.strip()]
    return "\n".join(raw_lines[:8]).strip()


# ============================================================
# Project Impact batch (กัน 413 ด้วย adaptive split)
# ============================================================

PROJECT_BATCH_PROMPT_TMPL = """
คุณจะได้รับ "Daily Focus" และรายการข่าวหลายชิ้น ให้ตอบเป็น JSON array เท่านั้น (ห้ามมีข้อความอื่น)

Daily Focus:
{daily_focus}

กติกาสำหรับแต่ละข่าว:
- คืน object ที่มี fields: idx, pass, country, project, category, impact
- idx ต้องตรงกับหมายเลขข่าว (integer)
- pass: true/false
- country: ประเทศที่เกี่ยวข้อง (ถ้าไม่รู้ "-")
- project: ชื่อโครงการ (ถ้าไม่รู้ "-")
- category: Energy/Politics/Finance/SupplyChain/Other
- impact: bullet เดียว (ขึ้นต้นด้วย "• ") 3–4 ประโยค ภาษาไทย อธิบายผลกระทบแบบไม่เดามั่ว

ข่าว:
{items_text}

ตอบเป็น JSON array เท่านั้น
"""


def _format_items_for_batch(items: List[Dict[str, Any]]) -> str:
    lines = []
    for i, n in enumerate(items, 1):
        lines.append(
            f"({i}) TITLE: {clip(n.get('title',''), 180)}\n"
            f"SUMMARY: {clip(n.get('summary',''), 240)}\n"
            f"URL: {n.get('final_url') or n.get('link') or ''}\n"
        )
    return "\n".join(lines).strip()


def _make_project_batch_prompt(items: List[Dict[str, Any]], daily_focus: str) -> str:
    prompt = PROJECT_BATCH_PROMPT_TMPL.format(
        daily_focus=daily_focus or "• ไม่มีข้อมูลแนวโน้มของวัน",
        items_text=_format_items_for_batch(items),
    )
    if len(prompt) > MAX_PROMPT_CHARS:
        base = PROJECT_BATCH_PROMPT_TMPL.format(
            daily_focus=daily_focus or "• ไม่มีข้อมูลแนวโน้มของวัน",
            items_text="",
        )
        budget = max(2000, MAX_PROMPT_CHARS - len(base) - 50)
        items_text = _format_items_for_batch(items)[:budget]
        prompt = PROJECT_BATCH_PROMPT_TMPL.format(
            daily_focus=daily_focus or "• ไม่มีข้อมูลแนวโน้มของวัน",
            items_text=items_text,
        )
        prompt = prompt[:MAX_PROMPT_CHARS]
    return prompt


def groq_tag_project_batch(items: List[Dict[str, Any]], daily_focus: str) -> List[Dict[str, Any]]:
    prompt = _make_project_batch_prompt(items, daily_focus)
    _sleep_jitter()
    raw = groq_chat(prompt, temperature=0.25)
    js = parse_json_loose(raw)
    return [x for x in js if isinstance(x, dict)] if isinstance(js, list) else []


def groq_tag_project_batch_safe(items: List[Dict[str, Any]], daily_focus: str) -> List[List[Dict[str, Any]]]:
    """
    คืนเป็น list ของผลลัพธ์เป็นก้อน ๆ (รองรับ split)
    - ถ้าไม่ split -> [tags]
    - ถ้า split -> [tags_left, tags_right] (และอาจ split ซ้อนเป็นหลายก้อน)
    """
    if not items:
        return [[]]
    try:
        tags = groq_tag_project_batch(items, daily_focus)
        return [tags]
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        code = getattr(resp, "status_code", None)
        if code != 413 and "413" not in str(e):
            raise
        if len(items) == 1:
            one = dict(items[0])
            one["summary"] = clip(one.get("summary", ""), 120)
            tags = groq_tag_project_batch([one], daily_focus)
            return [tags]
        mid = len(items) // 2
        print(f"[413] payload too large -> split {len(items)} into {mid}+{len(items)-mid}")
        left = groq_tag_project_batch_safe(items[:mid], daily_focus)
        right = groq_tag_project_batch_safe(items[mid:], daily_focus)
        return left + right


def groq_batch_tag_and_filter(items: List[Dict[str, Any]], daily_focus: str, batch_size: int) -> List[Dict[str, Any]]:
    """
    คืน tags ตามลำดับ items (pass=false ถ้าตีความไม่ได้)
    """
    out: List[Dict[str, Any]] = []
    start = 0
    while start < len(items):
        chunk = items[start:start + batch_size]
        res_chunks = groq_tag_project_batch_safe(chunk, daily_focus)

        cursor = 0
        for tags in res_chunks:
            # ถ้า LLM คืน tag มาน้อย/มาก ให้ยึดความยาว sub_chunk เป็นค่ากลางแบบปลอดภัย
            sub_len = max(1, min(len(chunk) - cursor, max(len(tags), 1)))
            sub_chunk = chunk[cursor:cursor + sub_len]
            cursor += sub_len

            idx_map: Dict[int, Dict[str, Any]] = {}
            for t in tags:
                try:
                    idx_map[int(t.get("idx"))] = t
                except Exception:
                    continue

            for i in range(1, len(sub_chunk) + 1):
                out.append(idx_map.get(i, {"idx": i, "pass": False}))

        start += len(chunk)

    return out


# ============================================================
# Impact rewrite (optional)
# ============================================================

PROJECT_IMPACT_REWRITE_PROMPT = """
คุณจะได้รับ bullet ผลกระทบ 1 ข้อ ให้ปรับภาษาให้เนียนขึ้น กระชับขึ้น แต่ยังคงความหมายเดิม
- ตอบเป็นข้อความล้วน
- ต้องขึ้นต้นด้วย "• "
- ห้ามเติมข้อมูลใหม่

ข้อความ:
{impact}

ผลลัพธ์:
"""


def rewrite_impact_if_enabled(text: str) -> str:
    t = clean_ws(text)
    if not ENABLE_IMPACT_REWRITE or not t:
        return t
    try:
        prompt = PROJECT_IMPACT_REWRITE_PROMPT.format(impact=t[:800])
        if len(prompt) > MAX_PROMPT_CHARS:
            prompt = prompt[:MAX_PROMPT_CHARS]
        _sleep_jitter()
        out = groq_chat(prompt, temperature=0.2)
        out = clean_ws(out)
        if not out.startswith("•"):
            out = "• " + out.lstrip("• ").strip()
        return out
    except Exception:
        return t


# ============================================================
# Digest mode (ง่าย ๆ ไม่ใช้ LLM)
# ============================================================

def simple_categorize(n: Dict[str, Any]) -> str:
    text = (n.get("title", "") + " " + n.get("summary", "")).lower()
    if any(k in text for k in ["oil", "crude", "opec", "brent", "wti"]):
        return "Oil"
    if any(k in text for k in ["gas", "lng", "pipeline"]):
        return "Gas/LNG"
    if any(k in text for k in ["sanction", "war", "geopolitic", "tariff"]):
        return "Geopolitics"
    if any(k in text for k in ["rate", "inflation", "bond", "dollar", "fed"]):
        return "Macro/Finance"
    return "Other"


# ============================================================
# LINE Sender + Flex Carousel (รวมจากไฟล์ที่คุณส่งมา)
# ============================================================

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


def _shorten(items: List[str], take: int = 4) -> str:
    items = items or []
    if not items:
        return "ALL"
    if len(items) <= take:
        return ", ".join(items)
    return ", ".join(items[:take]) + f" +{len(items) - take}"


def _is_good_image_url(u: str) -> bool:
    if not u or not isinstance(u, str):
        return False
    if not u.startswith("https://"):
        return False
    if len(u) > 1200:
        return False
    try:
        host = (urlparse(u).netloc or "").lower().replace("www.", "")
    except Exception:
        host = ""
    disallowed = {"lh3.googleusercontent.com", "googleusercontent.com", "gstatic.com", "accounts.google.com", "support.google.com"}
    trackers = {"google-analytics.com", "www.google-analytics.com", "googletagmanager.com", "doubleclick.net", "stats.g.doubleclick.net", "t.co"}
    if host in disallowed or any(host.endswith(x) for x in disallowed):
        return False
    if host in trackers or any(host.endswith(x) for x in trackers):
        return False
    return True


def create_project_carousel_flex(news_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Flex แบบ Carousel (รวมหลายข่าวใน 1 flex message)
    - bubble size mega
    - ปุ่มสีเขียว
    - แสดงประเทศ/โครงการ/ความน่าเชื่อถือ
    - impact เป็น bullet เดียว
    """
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles: List[Dict[str, Any]] = []

    for n in news_items:
        impact = (n.get("impact") or "").strip()
        if impact and not impact.startswith("•"):
            impact = "• " + impact.lstrip("• ").strip()

        country = (n.get("country") or "ไม่ระบุ").strip()
        projects = n.get("projects")
        if not isinstance(projects, list):
            projects = [n.get("project")] if n.get("project") else ["ALL"]
        projects = [p for p in projects if p]
        proj_txt = _shorten(projects, take=4)

        link = (n.get("final_url") or n.get("link") or "https://news.google.com/").strip()

        img = (n.get("hero") or n.get("image") or DEFAULT_HERO_URL).strip()
        if not _is_good_image_url(img):
            img = DEFAULT_HERO_URL

        published_txt = ""
        if isinstance(n.get("published"), datetime):
            published_txt = n["published"].astimezone(bangkok_tz).strftime("%d/%m/%Y %H:%M")

        cred_txt = ""
        if SHOW_SOURCE_RATING:
            d = domain_of(link)
            try:
                sc = float(n.get("source_score", 0.0))
            except Exception:
                sc = 0.0
            cred_txt = f"ความน่าเชื่อถือ: {sc:.2f}" + (f" · {d}" if d else "")

        contents: List[Dict[str, Any]] = [
            {"type": "text", "text": (n.get("title", "")[:140] or "ข่าว"), "wrap": True, "weight": "bold", "size": "lg"},
            {
                "type": "box",
                "layout": "baseline",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": published_txt, "size": "sm", "color": "#666666", "flex": 0},
                    {"type": "text", "text": country, "size": "sm", "color": "#1E90FF", "wrap": True},
                ],
            },
            {"type": "text", "text": f"โครงการที่เกี่ยวข้อง: {proj_txt}", "size": "sm", "color": "#666666", "wrap": True, "margin": "sm"},
        ]

        if cred_txt:
            contents.append({"type": "text", "text": cred_txt, "size": "xs", "color": "#666666", "wrap": True, "margin": "sm"})

        contents.append({"type": "text", "text": "ผลกระทบต่อโครงการ", "size": "lg", "weight": "bold", "color": "#000000", "margin": "lg"})
        contents.append({
            "type": "text",
            "text": impact or "• (ไม่มีข้อความผลกระทบ)",
            "wrap": True,
            "size": "md",
            "color": "#000000",
            "weight": "bold",
            "margin": "xs"
        })

        bubbles.append({
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "contents": contents},
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "button", "style": "primary", "color": "#1DB446",
                     "action": {"type": "uri", "label": "อ่านต่อ", "uri": link}}
                ],
            },
        })

    # ถ้าบังเอิญไม่มี bubble เลย ให้ fallback เป็น text
    if not bubbles:
        return create_text_messages("ไม่พบข่าวสำหรับส่ง")

    return [{
        "type": "flex",
        "altText": f"ข่าว Project Impact {now_txt}",
        "contents": {"type": "carousel", "contents": bubbles},
    }]


def send_to_line(messages: List[Dict[str, Any]]) -> None:
    if DRY_RUN:
        print("[DRY_RUN] messages =", len(messages))
        for m in messages[:3]:
            print(json.dumps(m, ensure_ascii=False)[:1200], "...\n")
        return

    if not messages:
        return

    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

    # LINE broadcast รับได้เป็นชุด messages (จำกัดจำนวนต่อ request) — ตัดเป็นก้อนเพื่อปลอดภัย
    BATCH = 5
    sent = 0
    for i in range(0, len(messages), BATCH):
        chunk = messages[i:i + BATCH]
        payload = {"messages": chunk}
        r = requests.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=60)
        if r.status_code >= 400:
            print("[LINE ERROR]", r.status_code, r.text[:800])
            r.raise_for_status()
        sent += len(chunk)
        time.sleep(0.2)

    print(f"ส่งสำเร็จ: {sent} messages")


# ============================================================
# Prepare items (final_url + hero + score)
# ============================================================

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


# ============================================================
# Project mode
# ============================================================

def run_project_mode_only(selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    # gate เบื้องต้น
    if USE_KEYWORD_GATE:
        selected = [x for x in selected if keyword_hit(x)]
    selected = [x for x in selected if float(x.get("source_score", 0.0)) >= MIN_SOURCE_SCORE]

    if not selected:
        return (create_text_messages("ไม่พบข่าวที่ผ่านเงื่อนไขเบื้องต้น (ความน่าเชื่อถือ/คีย์เวิร์ด)"), [])

    # Daily Focus (ไม่ส่งออก)
    daily_focus = build_daily_focus(selected)
    print("\n[DAILY_FOCUS]\n" + daily_focus + "\n")

    # Tag/Filter
    tags = groq_batch_tag_and_filter(selected, daily_focus=daily_focus, batch_size=LLM_BATCH_SIZE)

    passed: List[Dict[str, Any]] = []
    for n, t in zip(selected, tags):
        if not isinstance(t, dict) or not t.get("pass"):
            continue

        n2 = dict(n)
        n2["country"] = (t.get("country") or n.get("feed_country") or "Global").strip()
        n2["project"] = (t.get("project") or "-").strip()
        n2["category"] = (t.get("category") or "Other").strip()

        impact = clean_ws((t.get("impact") or "").strip())
        if impact and not impact.startswith("•"):
            impact = "• " + impact
        n2["impact"] = rewrite_impact_if_enabled(impact)

        # (optional) ถ้าอนาคตอยากให้หลายโปรเจกต์ ใช้ field projects
        n2["projects"] = [n2["project"]] if n2.get("project") else ["ALL"]

        passed.append(n2)

    passed.sort(key=lambda x: x.get("published") or datetime.min.replace(tzinfo=bangkok_tz), reverse=True)
    passed = passed[:PROJECT_SEND_LIMIT]

    if not passed:
        return (create_text_messages("ไม่พบข่าวที่มีผลกระทบต่อโครงการตามเงื่อนไข"), [])

    # ✅ ส่งแบบ carousel 1 flex message
    msgs = create_project_carousel_flex(passed)

    links = [x.get("final_url") or x.get("link") for x in passed if (x.get("final_url") or x.get("link"))]
    return (msgs, links)


# ============================================================
# Digest mode (ข้อความล้วน)
# ============================================================

def run_digest_mode(selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not selected:
        return (create_text_messages("ไม่พบข่าวสำหรับ Digest"), [])

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for n in selected:
        cat = simple_categorize(n)
        buckets.setdefault(cat, []).append(n)

    msgs: List[Dict[str, Any]] = []
    links: List[str] = []

    for cat, items in buckets.items():
        items = items[:DIGEST_MAX_PER_SECTION]
        lines = [f"🧾 {cat}"]
        for it in items:
            lines.append(f"- {clip(it.get('title',''), 120)}")
            links.append(it.get("final_url") or it.get("link") or "")
        msgs += create_text_messages("\n".join(lines))

    return (msgs, links)


# ============================================================
# Main
# ============================================================

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

    if OUTPUT_MODE in ("both", "project_only"):
        if ADD_SECTION_HEADERS and OUTPUT_MODE == "both":
            all_msgs += create_text_messages("📌 Project Impact")
        msgs, links = run_project_mode_only(selected)
        all_msgs += msgs
        all_links += links

    if OUTPUT_MODE in ("both", "digest_only"):
        if ADD_SECTION_HEADERS and OUTPUT_MODE == "both":
            all_msgs += create_text_messages("🧾 Digest")
        msgs, links = run_digest_mode(selected)
        all_msgs += msgs
        all_links += links

    send_to_line(all_msgs)
    save_sent_links(all_links)


if __name__ == "__main__":
    main()
