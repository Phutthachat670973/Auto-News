# news.py
# ============================================================================================================
# Dual Output News Bot (Project Impact + Energy Digest) + ลด Token + กัน 429
#
# ✅ แก้ 429 หลัก ๆ ด้วย:
# 1) ลดขนาด input ต่อ request (clip title/summary)
# 2) ลด output ต่อ request (ลด max_tokens)
# 3) ทำ backoff/retry ให้ถูกต้อง + log ชัด
# 4) ปรับ batch ให้ “ไม่ใหญ่เกิน” จน 1 call กิน token เยอะ
#
# โหมดส่ง (ENV):
# - OUTPUT_MODE=both (default)       -> ส่ง 2 ชุด: Project Impact + Energy Digest
# - OUTPUT_MODE=project_only         -> เฉพาะโครงการ
# - OUTPUT_MODE=digest_only          -> เฉพาะ digest
#
# ตัวแปรสำคัญกัน 429 (ENV แนะนำ):
#   SELECT_LIMIT=25
#   LLM_BATCH_SIZE=12
#   SLEEP_MIN=8
#   SLEEP_MAX=12
#   MAX_RETRIES=10
# ============================================================================================================

import os
import re
import json
import time
import random
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
from dateutil import parser as dateutil_parser
import pytz
import requests

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# ============================================================================================================
# ENV
# ============================================================================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not GROQ_API_KEY:
    raise RuntimeError("ไม่พบ GROQ_API_KEY")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")

GROQ_MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "llama-3.1-8b-instant").strip()

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "10"))
SLEEP_BETWEEN_CALLS = (
    float(os.getenv("SLEEP_MIN", "8")),
    float(os.getenv("SLEEP_MAX", "12")),
)
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "12"))
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes", "y")

OUTPUT_MODE = os.getenv("OUTPUT_MODE", "both").strip().lower()  # both | project_only | digest_only
ADD_SECTION_HEADERS = os.getenv("ADD_SECTION_HEADERS", "true").strip().lower() in ("1", "true", "yes", "y")

# จำกัดจำนวนข่าวเข้า LLM (ถ้าไม่ใส่ SELECT_LIMIT จะ fallback ไป MAX_LLM_ITEMS หรือ 45)
SELECT_LIMIT = int(os.getenv("SELECT_LIMIT", os.getenv("MAX_LLM_ITEMS", "45")))
DIGEST_MAX_PER_SECTION = int(os.getenv("DIGEST_MAX_PER_SECTION", "8"))

# Project mode controls
PROJECT_SEND_LIMIT = int(os.getenv("PROJECT_SEND_LIMIT", "10"))
MIN_SOURCE_SCORE = float(os.getenv("MIN_SOURCE_SCORE", "0"))
SHOW_SOURCE_RATING = os.getenv("SHOW_SOURCE_RATING", "true").strip().lower() in ("1", "true", "yes", "y")
USE_KEYWORD_GATE = os.getenv("USE_KEYWORD_GATE", "false").strip().lower() in ("1", "true", "yes", "y")

# Network
DEFAULT_HERO_URL = os.getenv("DEFAULT_HERO_URL", "").strip()
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (NewsBot)").strip()

TRACK_DIR = os.getenv("TRACK_DIR", "sent_links").strip()

# Timezone
bangkok_tz = pytz.timezone("Asia/Bangkok")

# ============================================================================================================
# RSS FEEDS (เพิ่มได้)
# ============================================================================================================

RSS_FEEDS: List[Dict[str, str]] = [
    {"name": "OilPrice", "url": "https://oilprice.com/rss/main", "country": "Global"},
    {"name": "Prachachat", "url": "https://www.prachachat.net/feed", "country": "Thailand"},
    {"name": "Bangkokbiznews", "url": "https://www.bangkokbiznews.com/rss", "country": "Thailand"},
    {"name": "PostToday", "url": "https://www.posttoday.com/rss", "country": "Thailand"},
    # เพิ่มแหล่งอื่น ๆ ได้ตามต้องการ
]

# ============================================================================================================
# Token reduction helpers
# ============================================================================================================

def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def clip(s: str, n: int) -> str:
    s = clean_ws(s)
    return s if len(s) <= n else s[:n] + "…"

# ============================================================================================================
# STYLE EXAMPLES (ย่อแล้ว ลด token)
# ============================================================================================================

STYLE_EXAMPLES = """
ตัวอย่างสไตล์ที่ต้องการ:

[หัวข้อข่าว]
1. พลังงานคุมเข้มแท่นขุดเจาะอ่าวไทย สกัดโดรนป่วน ไม่กระทบการผลิต

[สาระสำคัญข่าว]
1.กระทรวงพลังงานสั่งยกระดับมาตรการความปลอดภัยรอบแท่นขุดเจาะในอ่าวไทย หลังพบความพยายามรุกล้ำพื้นที่ จับตาเฝ้าระวังต่อเนื่อง แต่ยืนยันการผลิตยังดำเนินปกติ ไม่ได้รับผลกระทบ
(ตามด้วยลิงก์บรรทัดถัดไป)

กติกา: headline_th 1 บรรทัด / summary_th 2–4 ประโยค โทนรายงานข่าว ห้ามเดานอก title/summary
"""

# ============================================================================================================
# URL normalize / dedupe
# ============================================================================================================

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

# ============================================================================================================
# HTTP utilities
# ============================================================================================================

def http_get(url: str, timeout: int = 15) -> requests.Response:
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)

def resolve_final_url(url: str) -> str:
    try:
        r = http_get(url, timeout=15)
        return normalize_url(r.url or url)
    except Exception:
        return normalize_url(url)

def extract_og_image(url: str) -> Optional[str]:
    try:
        r = http_get(url, timeout=15)
        if r.status_code >= 400 or not r.text:
            return None
        html = r.text
        m = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        m = re.search(r'name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None
    except Exception:
        return None

# ============================================================================================================
# GROQ API (PATCHED)
# ============================================================================================================

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def _sleep_jitter():
    a, b = SLEEP_BETWEEN_CALLS
    time.sleep(random.uniform(a, b))

def call_groq_with_retries(prompt: str, temperature: float = 0.25, max_tokens: int = 900) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL_NAME,
        "messages": [
            {"role": "system", "content": "คุณเป็นผู้ช่วยที่ตอบภาษาไทยเป็นหลัก และตอบตามข้อมูลที่ให้เท่านั้น"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_err: Optional[Exception] = None
    last_status: Optional[int] = None
    last_body: str = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _sleep_jitter()
            r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
            last_status = r.status_code
            last_body = (r.text or "")[:500]

            if r.status_code == 429:
                last_err = RuntimeError(f"429 Too Many Requests: {last_body}")
                ra = r.headers.get("retry-after")
                if ra and ra.strip().isdigit():
                    wait_s = int(ra.strip())
                else:
                    wait_s = min(60, 5 * attempt + random.uniform(0.0, 2.0))
                print(f"[GROQ] 429 rate limited. attempt={attempt}/{MAX_RETRIES}, sleep={wait_s:.1f}s")
                time.sleep(wait_s)
                continue

            if r.status_code in (401, 403):
                raise RuntimeError(f"GROQ auth failed {r.status_code}: {last_body}")

            if r.status_code >= 500:
                last_err = RuntimeError(f"GROQ server error {r.status_code}: {last_body}")
                wait_s = min(60, 3 * attempt + random.uniform(0.0, 2.0))
                print(f"[GROQ] {r.status_code} server error. attempt={attempt}/{MAX_RETRIES}, sleep={wait_s:.1f}s")
                time.sleep(wait_s)
                continue

            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError(f"GROQ empty content: {json.dumps(data)[:300]}")
            return content

        except Exception as e:
            last_err = e
            wait_s = min(60, 3 * attempt + random.uniform(0.0, 2.0))
            print(f"[GROQ] error: {type(e).__name__}: {e}. attempt={attempt}/{MAX_RETRIES}, sleep={wait_s:.1f}s")
            time.sleep(wait_s)

    raise RuntimeError(
        f"Groq call failed after {MAX_RETRIES} retries (last_status={last_status}): {last_err} | body={last_body}"
    )

def _extract_json_object(text: str) -> Any:
    t = (text or "").strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

# ============================================================================================================
# Credibility scoring (heuristic)
# ============================================================================================================

HIGH_TRUST_DOMAINS = {
    "reuters.com","bloomberg.com","wsj.com","ft.com","bbc.com","bbc.co.uk",
    "oilprice.com","prachachat.net","bangkokbiznews.com","posttoday.com","energynewscenter.com","matichon.co.th"
}
MED_TRUST_DOMAINS = {"msn.com","yahoo.com","investing.com","marketwatch.com"}

def domain_of(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""

def source_score(url: str) -> float:
    d = domain_of(url)
    if not d:
        return 0.3
    if d in HIGH_TRUST_DOMAINS:
        return 0.85
    if d in MED_TRUST_DOMAINS:
        return 0.6
    return 0.45

# ============================================================================================================
# RSS parsing
# ============================================================================================================

def parse_datetime(dt_str: str) -> Optional[datetime]:
    try:
        dt = dateutil_parser.parse(dt_str)
        if not dt.tzinfo:
            dt = bangkok_tz.localize(dt)
        return dt.astimezone(bangkok_tz)
    except Exception:
        return None

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
            "title": title,
            "summary": summary,
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

def dedupe_news(items: List[Dict[str, Any]], sent: set) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for n in items:
        link = normalize_url(n.get("link", ""))
        if not link or link in sent or link in seen:
            continue
        seen.add(link)
        out.append(n)
    return out

# ============================================================================================================
# Thai enforcement (optional rewrite if english leak)
# ============================================================================================================

def enforce_thai(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text
    eng = re.findall(r"[A-Za-z]{3,}", text)
    if len(eng) >= 4:
        prompt = f"ช่วยเขียนใหม่ให้เป็นภาษาไทยล้วน อ่านลื่น และคงความหมายเดิม\nข้อความ:\n{text}"
        try:
            out = call_groq_with_retries(prompt, temperature=0.2, max_tokens=450)
            return out.strip()
        except Exception:
            return text
    return text

# ============================================================================================================
# Project-mode LLM
# ============================================================================================================

PROJECT_CATEGORIES = [
    "Energy Policy / Regulation",
    "Oil & Gas / Upstream",
    "Gas / LNG",
    "Power / Electricity",
    "Finance / FX / Macro",
    "Geopolitics / Sanctions",
    "Technology / Transition",
    "Other",
]

def groq_batch_tag_and_filter(news_list: List[Dict[str, Any]], chunk_size: int = 12) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]
        payload = []
        for idx, n in enumerate(chunk):
            payload.append({
                "id": idx,
                "feed_country": (n.get("feed_country") or "").strip(),
                # ✅ ลด token: clip input
                "title": clip(n.get("title", ""), 160),
                "summary": clip(n.get("summary", ""), 420),
            })

        prompt = f"""
คุณคือผู้ช่วยคัดกรองข่าวเพื่อ "ผลกระทบต่อโครงการพลังงานตามประเทศ/โครงการ"
ถ้าไม่เกี่ยวข้องเลยให้ pass=false

เมื่อ pass=true ให้ระบุ:
- country: ประเทศที่ข่าวส่งผลชัดเจน (ถ้าไม่แน่ให้ใช้ feed_country หรือ "Global")
- project: ชื่อโครงการ (ถ้าไม่ทราบให้ใส่ "-")
- category: เลือก 1 จากรายการ {json.dumps(PROJECT_CATEGORIES, ensure_ascii=False)}
- impact: bullet เดียว ภาษาไทย 3–4 ประโยค อธิบายผลกระทบเชิงปฏิบัติ

ข้อห้าม:
- ห้ามเดาข้อมูลนอก title/summary
- ห้ามใส่คำว่า PTTEP ใน impact

ตอบเป็น JSON เท่านั้น:
{{"items":[{{"id":0,"pass":true,"country":"Thailand","project":"-","category":"Power / Electricity","impact":"..."}}]}}

ข่าวชุดนี้:
{json.dumps(payload, ensure_ascii=False)}
"""
        # ✅ ลด token: ลด max_tokens
        text = call_groq_with_retries(prompt, temperature=0.28, max_tokens=800)
        data = _extract_json_object(text)

        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            for _ in chunk:
                results.append({"pass": False})
            continue

        by_id = {it.get("id"): it for it in data["items"] if isinstance(it, dict) and "id" in it}
        for idx in range(len(chunk)):
            it = by_id.get(idx, {"pass": False})
            results.append(it if isinstance(it, dict) else {"pass": False})

    return results

# ============================================================================================================
# Digest-mode LLM
# ============================================================================================================

DIGEST_CATEGORIES = [
    "domestic_policy",
    "domestic_lng",
    "domestic_tech_other",
    "intl_situation",
    "intl_lng",
    "intl_tech_other",
]

BUCKET_LABELS = {
    "domestic_policy": "🔸ข่าวนโยบายพลังงาน",
    "domestic_lng": "🔸ข่าวธุรกิจก๊าซธรรมชาติและ LNG",
    "domestic_tech_other": "🔸ข่าวเทคโนโลยีพลังงาน และอื่นๆ",
    "intl_situation": "🔸ข่าวสถานการณ์พลังงาน",
    "intl_lng": "🔸ข่าวธุรกิจก๊าซธรรมชาติและ LNG",
    "intl_tech_other": "🔸ข่าวเทคโนโลยีพลังงาน และอื่นๆ",
}

def groq_batch_energy_digest(news_list: List[Dict[str, Any]], chunk_size: int = 12) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]
        payload = []
        for idx, n in enumerate(chunk):
            payload.append({
                "id": idx,
                "feed_country": (n.get("feed_country") or "").strip(),
                # ✅ ลด token: clip input
                "title": clip(n.get("title", ""), 160),
                "summary": clip(n.get("summary", ""), 420),
            })

        prompt = f"""
คุณเป็นบรรณาธิการสรุปข่าวพลังงานรายวัน ภาษาไทย

{STYLE_EXAMPLES}

งาน:
- คัดเฉพาะข่าวที่เข้าข่ายพลังงาน หรือปัจจัยที่กระทบต้นทุนพลังงาน/ค่าไฟ/ราคาน้ำมัน/ก๊าซ/LNG/โลจิสติกส์/ค่าเงิน อย่างชัดเจน
- ถ้าไม่เข้าเกณฑ์ is_energy=false

การจัดหมวด bucket (เลือกได้แค่นี้):
{json.dumps(DIGEST_CATEGORIES, ensure_ascii=False)}
กติกา bucket:
- ถ้า feed_country เป็น Thailand -> domestic_*
- ถ้าไม่ใช่ Thailand -> intl_*
- policy = นโยบาย/รัฐ/กกพ./ค่าไฟ/ภาษี/มาตรการรัฐ/การเลือกตั้งที่โยงพลังงานชัด
- lng = LNG/ก๊าซ/สัญญาซื้อขาย/โครงสร้างพื้นฐานก๊าซ
- situation = สถานการณ์ตลาด/คว่ำบาตร/ความตึงเครียด/อุปทาน-อุปสงค์/ขนส่งน้ำมัน
- tech_other = เทคโนโลยีพลังงาน/AI/โซลาร์/แบต/ดาต้าเซนเตอร์ หรือข่าวพลังงานอื่นๆ

ผลลัพธ์สำหรับข่าวที่เข้าเกณฑ์:
- headline_th: หัวข้อไทย 1 บรรทัด
- summary_th: 2–4 ประโยค โทนรายงานข่าวแบบตัวอย่าง
ข้อห้าม: ห้ามเดาข้อมูลนอก title/summary

ตอบเป็น JSON เท่านั้น:
{{"items":[{{"id":0,"is_energy":true,"bucket":"domestic_policy","headline_th":"...","summary_th":"..."}}]}}

ข่าวชุดนี้:
{json.dumps(payload, ensure_ascii=False)}
"""
        # ✅ ลด token: ลด max_tokens
        text = call_groq_with_retries(prompt, temperature=0.22, max_tokens=850)
        data = _extract_json_object(text)

        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            for _ in chunk:
                results.append({"is_energy": False})
            continue

        by_id = {it.get("id"): it for it in data["items"] if isinstance(it, dict) and "id" in it}
        for idx in range(len(chunk)):
            it = by_id.get(idx, {"is_energy": False})
            if isinstance(it, dict) and it.get("is_energy"):
                it["headline_th"] = enforce_thai(it.get("headline_th", ""))
                it["summary_th"] = enforce_thai(it.get("summary_th", ""))
            results.append(it if isinstance(it, dict) else {"is_energy": False})

    return results

# ============================================================================================================
# Digest formatting
# ============================================================================================================

THAI_MONTH_ABBR = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]

def thai_date_str(dt: datetime) -> str:
    dt = dt.astimezone(bangkok_tz)
    return f"{dt.day} {THAI_MONTH_ABBR[dt.month-1]} {dt.year+543}"

def news_items_by_bucket(items: List[Dict[str, Any]], bucket: str) -> List[Dict[str, Any]]:
    xs = [x for x in items if x.get("bucket") == bucket]
    xs.sort(key=lambda z: z.get("published") or datetime.min.replace(tzinfo=bangkok_tz), reverse=True)
    return xs[:DIGEST_MAX_PER_SECTION]

def _render_section(items: List[Dict[str, Any]], with_summary: bool) -> str:
    if not items:
        return "-"
    lines = []
    for i, n in enumerate(items, 1):
        head = clean_ws(n.get("headline_th") or n.get("title") or "")
        summ = clean_ws(n.get("summary_th") or "")
        link = (n.get("final_url") or n.get("link") or "").strip()
        if with_summary:
            lines.append(f"{i}.{summ if summ else head}")     # no space after dot
            if link:
                lines.append(link)
        else:
            lines.append(f"{i}. {head}")                      # space after dot
    return "\n".join(lines)

def build_energy_digest_text(news_items: List[Dict[str, Any]], report_dt: datetime, with_summary: bool) -> str:
    title = "สรุปสาระสำคัญข่าวพลังงาน" if with_summary else "สรุปหัวข้อข่าวพลังงาน"
    out = [f"{title} วันที่ {thai_date_str(report_dt)}"]

    out.append("🔹ข่าวในประเทศ\u202f ")
    for b in ["domestic_policy", "domestic_lng", "domestic_tech_other"]:
        out.append(BUCKET_LABELS[b])
        out.append(_render_section(news_items_by_bucket(news_items, b), with_summary))

    out.append("")
    out.append("🔹ข่าวต่างประเทศ\u202f ")
    for b in ["intl_situation", "intl_lng", "intl_tech_other"]:
        out.append(BUCKET_LABELS[b])
        out.append(_render_section(news_items_by_bucket(news_items, b), with_summary))

    return "\n".join(out).strip()

def chunk_text_for_line(text: str, max_chars: int = 4500) -> List[str]:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > max_chars:
            if buf.strip():
                parts.append(buf.strip())
            buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf.strip():
        parts.append(buf.strip())
    return parts

def create_text_messages(text: str) -> List[Dict[str, Any]]:
    return [{"type": "text", "text": t} for t in chunk_text_for_line(text)]

# ============================================================================================================
# LINE Messaging API
# ============================================================================================================

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
LINE_TARGET = os.getenv("LINE_TARGET", "broadcast").strip().lower()  # broadcast | user
LINE_USER_ID = os.getenv("LINE_USER_ID", "").strip()

def send_to_line(messages: List[Dict[str, Any]]) -> None:
    if DRY_RUN:
        print("[DRY_RUN] messages:", json.dumps(messages, ensure_ascii=False)[:900], "...")
        return

    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}
    if LINE_TARGET == "user":
        if not LINE_USER_ID:
            raise RuntimeError("LINE_TARGET=user แต่ไม่พบ LINE_USER_ID")
        url = LINE_PUSH_URL
        payload = {"to": LINE_USER_ID, "messages": messages}
    else:
        url = LINE_BROADCAST_URL
        payload = {"messages": messages}

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"LINE API error {r.status_code}: {r.text}")

# ============================================================================================================
# Flex (Project Impact)
# ============================================================================================================

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
    body.append({"type": "separator", "margin": "md"})
    body.append({"type": "text", "text": impact, "wrap": True, "size": "sm"})

    return {
        "type": "flex",
        "altText": title or "ข่าว",
        "contents": {
            "type": "bubble",
            "hero": {
                "type": "image",
                "url": hero,
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover",
            } if hero else None,
            "body": {"type": "box", "layout": "vertical", "contents": [c for c in body if c]},
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [{
                    "type": "button",
                    "style": "link",
                    "height": "sm",
                    "action": {"type": "uri", "label": "อ่านข่าว", "uri": link},
                }],
                "flex": 0,
            },
        },
    }

# ============================================================================================================
# Optional keyword gate
# ============================================================================================================

KEYWORDS = [
    "oil","crude","gas","lng","opec","power","electricity","sanction","pipeline","refinery","diesel","gasoline","brent","wti","dubai",
    "ค่าไฟ","น้ำมัน","ก๊าซ","lng","พลังงาน","โรงไฟฟ้า","คว่ำบาตร"
]

def keyword_hit(n: Dict[str, Any]) -> bool:
    t = ((n.get("title") or "") + " " + (n.get("summary") or "")).lower()
    return any(kw in t for kw in KEYWORDS)

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
# Runners
# ============================================================================================================

def run_project_mode(selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    if USE_KEYWORD_GATE:
        selected = [x for x in selected if keyword_hit(x)]
    selected = [x for x in selected if float(x.get("source_score", 0.0)) >= MIN_SOURCE_SCORE]

    tags = groq_batch_tag_and_filter(selected, chunk_size=LLM_BATCH_SIZE)

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
    links = [x.get("link") for x in passed if x.get("link")]
    return (msgs, links)

def run_digest_mode(selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    digest_tags = groq_batch_energy_digest(selected, chunk_size=LLM_BATCH_SIZE)

    digest_items = []
    for n, tag in zip(selected, digest_tags):
        if not isinstance(tag, dict) or not tag.get("is_energy"):
            continue
        bucket = (tag.get("bucket") or "").strip()
        if bucket not in DIGEST_CATEGORIES:
            continue
        n2 = dict(n)
        n2["bucket"] = bucket
        n2["headline_th"] = (tag.get("headline_th") or "").strip()
        n2["summary_th"] = (tag.get("summary_th") or "").strip()
        digest_items.append(n2)

    if not digest_items:
        return (create_text_messages("ไม่พบข่าวที่เข้าหมวดข่าวพลังงานสำหรับสรุปแบบใหม่"), [])

    report_dt = max([x.get("published") for x in digest_items if x.get("published")], default=datetime.now(bangkok_tz))

    text_full = build_energy_digest_text(digest_items, report_dt, with_summary=True)
    text_titles = build_energy_digest_text(digest_items, report_dt, with_summary=False)

    msgs: List[Dict[str, Any]] = []
    msgs += create_text_messages(text_full)
    msgs += create_text_messages("━━━━━━━━━━━━━━━━━━")
    msgs += create_text_messages(text_titles)

    links = [x.get("link") for x in digest_items if x.get("link")]
    return (msgs, links)

# ============================================================================================================
# Main
# ============================================================================================================

def main():
    print("ดึงข่าว...")
    raw = load_news()
    print("จำนวนข่าวดิบทั้งหมด:", len(raw))

    sent = load_sent_links()
    raw = dedupe_news(raw, sent)
    print("หลังตัดข่าวซ้ำ/เคยส่ง:", len(raw))

    selected = raw[:max(1, SELECT_LIMIT)]
    selected = prepare_items(selected)

    mode = OUTPUT_MODE if OUTPUT_MODE in ("both", "project_only", "digest_only") else "both"

    all_msgs: List[Dict[str, Any]] = []
    all_links: List[str] = []

    if mode in ("both", "project_only"):
        if ADD_SECTION_HEADERS:
            all_msgs += create_text_messages("📌 สรุปข่าวผลกระทบต่อโครงการ (Project Impact)")
        msgs, links = run_project_mode(selected)
        all_msgs += msgs
        all_links += links

    if mode == "both" and ADD_SECTION_HEADERS:
        all_msgs += create_text_messages("")

    if mode in ("both", "digest_only"):
        if ADD_SECTION_HEADERS:
            all_msgs += create_text_messages("📰 สรุปข่าวพลังงานรายวัน (Energy Digest)")
        msgs, links = run_digest_mode(selected)
        all_msgs += msgs
        all_links += links

    send_to_line(all_msgs)
    save_sent_links([normalize_url(x) for x in all_links if x])
    print("ส่งสำเร็จ:", len(all_msgs), "messages")

if __name__ == "__main__":
    main()
