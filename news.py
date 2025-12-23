# news.py
# ============================================================================================================
# NEWS BOT: Dual output in one run
# 1) Project Impact (เดิม): คัดข่าว+เขียน "ผลกระทบต่อโครงการ" แล้วส่ง LINE (Text/Flex)
# 2) Energy Digest (ใหม่): ส่ง "สรุปหัวข้อข่าวพลังงาน" + "สรุปสาระสำคัญข่าวพลังงาน" แบบตัวอย่างที่ให้มา
#
# โหมดการส่ง (ENV):
# - OUTPUT_MODE=both (default)       -> ส่ง 2 ชุดติดกัน: [Project Impact] + [Energy Digest]
# - OUTPUT_MODE=project_only         -> ส่งเฉพาะโครงการ
# - OUTPUT_MODE=digest_only          -> ส่งเฉพาะแบบใหม่
#
# จำกัดจำนวนข่าว (ENV):
# - PROJECT_SEND_LIMIT=10            -> จำกัดจำนวนข่าวฝั่งโครงการ
# - DIGEST_MAX_PER_SECTION=8         -> จำกัดจำนวนข่าวต่อหมวดใน digest
#
# ตัวเลือกหัวข้อคั่น (ENV):
# - ADD_SECTION_HEADERS=true/false   -> แสดงหัวข้อคั่นก่อนส่งแต่ละชุด
#
# หมายเหตุ: โค้ดนี้ออกแบบให้ “ใช้งานได้ทันที” บน GitHub Actions / Local โดยใช้ ENV เท่านั้น
# ============================================================================================================

import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
from dateutil import parser as dateutil_parser
import pytz
import requests

# -----------------------------
# Optional dotenv (local dev)
# -----------------------------
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

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))
SLEEP_BETWEEN_CALLS = (
    float(os.getenv("SLEEP_MIN", "1.0")),
    float(os.getenv("SLEEP_MAX", "2.0")),
)
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "10"))
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes", "y")

# Project-mode controls (เดิม)
PROJECT_SEND_LIMIT = int(os.getenv("PROJECT_SEND_LIMIT", "10"))
MIN_SOURCE_SCORE = float(os.getenv("MIN_SOURCE_SCORE", "0"))
SHOW_SOURCE_RATING = os.getenv("SHOW_SOURCE_RATING", "true").strip().lower() in ("1", "true", "yes", "y")
ENABLE_IMPACT_REWRITE = os.getenv("ENABLE_IMPACT_REWRITE", "true").strip().lower() in ("1", "true", "yes", "y")
USE_KEYWORD_GATE = os.getenv("USE_KEYWORD_GATE", "false").strip().lower() in ("1", "true", "yes", "y")

# Dual output mode
OUTPUT_MODE = os.getenv("OUTPUT_MODE", "both").strip().lower()  # both | project_only | digest_only
ADD_SECTION_HEADERS = os.getenv("ADD_SECTION_HEADERS", "true").strip().lower() in ("1", "true", "yes", "y")

# Digest-mode controls (ใหม่)
DIGEST_MAX_PER_SECTION = int(os.getenv("DIGEST_MAX_PER_SECTION", "8"))

DEFAULT_HERO_URL = os.getenv("DEFAULT_HERO_URL", "").strip()
USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (NewsBot)").strip()

# Timezone
bangkok_tz = pytz.timezone("Asia/Bangkok")

# ============================================================================================================
# RSS FEEDS (ปรับ/เพิ่มได้)
# ============================================================================================================

RSS_FEEDS: List[Dict[str, str]] = [
    # International
    {"name": "OilPrice", "url": "https://oilprice.com/rss/main", "country": "Global"},
    {"name": "Reuters Energy (fallback)", "url": "https://www.reuters.com/rssFeed/energyNews", "country": "Global"},
    {"name": "Bloomberg Energy (fallback)", "url": "https://www.bloomberg.com/feed/podcast/etf-report.xml", "country": "Global"},
    # Thailand / local (ตัวอย่าง)
    {"name": "Prachachat", "url": "https://www.prachachat.net/feed", "country": "Thailand"},
    {"name": "Bangkokbiznews", "url": "https://www.bangkokbiznews.com/rss", "country": "Thailand"},
    {"name": "PostToday", "url": "https://www.posttoday.com/rss", "country": "Thailand"},
    # Add more as needed...
]

# ============================================================================================================
# STYLE LEARNING EXAMPLES (Few-shot)
# ให้ LLM ยึดโทนและรูปแบบตามตัวอย่างของคุณ
# ============================================================================================================

STYLE_EXAMPLES = """
ตัวอย่างรูปแบบที่ถูกต้อง (ต้องเขียนเลียนแบบโทน/สำนวน/ความยาว):

[ตัวอย่างหัวข้อข่าว]
🔸ข่าวนโยบายพลังงาน
1. พลังงานคุมเข้มแท่นขุดเจาะอ่าวไทย สกัดโดรนป่วน ไม่กระทบการผลิต
2. ‘โซลาร์ประชาชน’ รอ ครม.ชุดใหม่ ห่วงแผนพลังงานไทยเดินบนเส้นบาง ๆ

[ตัวอย่างสาระสำคัญข่าว]
🔸ข่าวนโยบายพลังงาน
1.กระทรวงพลังงานสั่งยกระดับมาตรการรักษาความปลอดภัยรอบแท่นขุดเจาะปิโตรเลียมในอ่าวไทย หลังพบโดรนและเรือไม่ทราบฝ่ายรุกล้ำพื้นที่ โดยร่วมกับกองทัพเรือเฝ้าระวัง 24 ชม. พร้อมใช้ 5 มาตรการเข้มงวด แต่ยืนยันว่าการผลิตพลังงานและโครงสร้างพื้นฐานยังดำเนินไปตามปกติ ไม่ได้รับผลกระทบจากเหตุการณ์นี้
(ตามด้วยลิงก์บรรทัดถัดไป)
2.ความกังวลเกี่ยวกับโครงการ “โซลาร์ประชาชน” ที่ต้องรอการอนุมัติจากคณะรัฐมนตรีชุดใหม่ และประเด็นเกี่ยวกับทิศทางแผนพลังงานไทยยังคงอยู่บนเส้นบาง ๆ ระหว่างความมั่นคงพลังงานและการเปลี่ยนผ่านไปสู่พลังงานสะอาด
(ตามด้วยลิงก์บรรทัดถัดไป)

กติกาสไตล์:
- headline_th: เป็นหัวข้อสั้น 1 บรรทัด (แนวข่าวตัวอย่าง)
- summary_th: 2–4 ประโยค โทนรายงานข่าวแบบตัวอย่าง เน้น “เกิดอะไรขึ้น/ใคร/ผลต่อพลังงานหรือความมั่นคง/ทิศทาง”
- ห้ามเดาข้อมูลนอก title/summary
"""

# ============================================================================================================
# Helpers: URL normalize / dedupe
# ============================================================================================================

TRACK_DIR = os.getenv("TRACK_DIR", "sent_links").strip()

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def normalize_url(url: str) -> str:
    try:
        u = url.strip()
        if not u:
            return u
        p = urlparse(u)
        # remove tracking params
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid")]
        new_query = urlencode(q)
        p2 = p._replace(query=new_query, fragment="")
        return urlunparse(p2)
    except Exception:
        return url.strip()

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
    headers = {"User-Agent": USER_AGENT}
    return requests.get(url, headers=headers, timeout=timeout)

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
# GROQ API
# ============================================================================================================

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def _sleep_jitter():
    a, b = SLEEP_BETWEEN_CALLS
    time.sleep(random.uniform(a, b))

def call_groq_with_retries(prompt: str, temperature: float = 0.25, max_tokens: int = 1200) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL_NAME,
        "messages": [
            {"role": "system", "content": "คุณเป็นผู้ช่วยที่ตอบภาษาไทยเป็นหลัก และตอบตามข้อมูลที่ให้เท่านั้น"},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _sleep_jitter()
            r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
            if r.status_code == 429:
                time.sleep(2.0 * attempt)
                continue
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Groq call failed: {last_err}")

def _extract_json_object(text: str) -> Any:
    # try direct json
    t = text.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    # attempt to find first { ... } block
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

# ============================================================================================================
# Credibility scoring (simple heuristic)
# ============================================================================================================

HIGH_TRUST_DOMAINS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "nytimes.com",
    "theguardian.com", "bbc.co.uk", "bbc.com", "oilprice.com",
    "prachachat.net", "bangkokbiznews.com", "posttoday.com",
    "energynewscenter.com", "mgronline.com", "matichon.co.th",
}

MED_TRUST_DOMAINS = {
    "msn.com", "yahoo.com", "investing.com", "seekingalpha.com", "marketwatch.com",
}

def domain_of(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
        if h.startswith("www."):
            h = h[4:]
        return h
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
    # fallback: treat unknown as low-mid
    return 0.45

# ============================================================================================================
# Parse RSS feeds
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
    url = feed["url"]
    country = feed.get("country", "").strip() or "Global"
    name = feed.get("name", "feed").strip()

    d = feedparser.parse(url)
    items = []
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
            "feed_name": name,
            "feed_country": country,
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
            all_items.extend(fetch_feed(f))
        except Exception as e:
            print("Feed error:", f.get("name"), e)
    # basic sort newest first
    all_items.sort(key=lambda x: x.get("published") or datetime.min.replace(tzinfo=bangkok_tz), reverse=True)
    return all_items

def dedupe_news(items: List[Dict[str, Any]], sent: set) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for n in items:
        link = normalize_url(n.get("link", ""))
        if not link:
            continue
        if link in sent:
            continue
        if link in seen:
            continue
        seen.add(link)
        out.append(n)
    return out

# ============================================================================================================
# Project-mode LLM: tag & filter + impact rewrite (เดิม)
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

def groq_batch_tag_and_filter(news_list: List[Dict[str, Any]], chunk_size: int = 10) -> List[Dict[str, Any]]:
    """
    คืน list ขนานกับ news_list:
    {
      "pass": true/false,
      "country": "...",
      "project": "...",
      "impact": "..."   # bullet เดียว
      "category": "..."
    }
    """
    results: List[Dict[str, Any]] = []
    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]
        payload = []
        for idx, n in enumerate(chunk):
            payload.append({
                "id": idx,
                "feed_country": (n.get("feed_country") or "").strip(),
                "title": n.get("title", ""),
                "summary": n.get("summary", ""),
            })

        prompt = f"""
คุณคือผู้ช่วยคัดกรองข่าวเพื่อ "ผลกระทบต่อโครงการพลังงานตามประเทศ/โครงการ"
ให้คัดเฉพาะข่าวที่มีผลต่อ: พลังงาน การเมือง การเงิน ค่าเงิน โลจิสติกส์ ห่วงโซ่อุปทาน น้ำมัน/ก๊าซ/LNG/ค่าไฟ ฯลฯ
ถ้าไม่เกี่ยวข้องเลยให้ pass=false

เมื่อ pass=true ให้ระบุ:
- country: ประเทศที่ข่าวส่งผลชัดเจน (ถ้าไม่แน่ให้ใช้ feed_country หรือ "Global")
- project: ชื่อโครงการ (ถ้าไม่ทราบให้ใส่ "-")
- category: เลือก 1 จากรายการ {json.dumps(PROJECT_CATEGORIES, ensure_ascii=False)}
- impact: bullet เดียว ภาษาไทย และ "ยาวพอ" (3-5 ประโยค) อธิบายผลกระทบต่อโครงการ/ประเทศเชิงปฏิบัติ

ข้อห้าม:
- ห้ามเดาข้อมูลนอก title/summary
- ห้ามใส่คำว่า PTTEP ใน impact

ตอบเป็น JSON เท่านั้น:
{{
  "items":[
    {{
      "id":0,
      "pass":true,
      "country":"Thailand",
      "project":"-",
      "category":"Power / Electricity",
      "impact":"..."
    }}
  ]
}}

ข่าวชุดนี้:
{json.dumps(payload, ensure_ascii=False)}
"""
        text = call_groq_with_retries(prompt, temperature=0.3, max_tokens=1400)
        data = _extract_json_object(text)

        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            for _ in chunk:
                results.append({"pass": False})
            continue

        by_id = {}
        for it in data["items"]:
            if isinstance(it, dict) and "id" in it:
                by_id[it.get("id")] = it

        for idx, _n in enumerate(chunk):
            it = by_id.get(idx, {"pass": False})
            if not isinstance(it, dict):
                it = {"pass": False}
            results.append(it)

    return results

def enforce_thai(text: str) -> str:
    # บังคับภาษาไทยเท่านั้น + ถ้าอังกฤษหลุด จะ rewrite ให้เป็นไทย
    if not text:
        return text
    # หากมีตัวอักษรอังกฤษจำนวนมาก ให้ rewrite
    eng = re.findall(r"[A-Za-z]{3,}", text)
    if len(eng) >= 4:
        prompt = f"""
ช่วยเขียนใหม่ให้เป็นภาษาไทยล้วน อ่านลื่น และคงความหมายเดิม
ข้อความ:
{text}
"""
        try:
            out = call_groq_with_retries(prompt, temperature=0.2, max_tokens=900)
            return out.strip()
        except Exception:
            return text
    return text

# ============================================================================================================
# Digest-mode LLM: energy digest classify + summarize (ใหม่)
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

def groq_batch_energy_digest(news_list: List[Dict[str, Any]], chunk_size: int = 10) -> List[Dict[str, Any]]:
    """
    คืน list ขนานกับ news_list:
    {
      "is_energy": true/false,
      "bucket": one of DIGEST_CATEGORIES,
      "headline_th": "...",
      "summary_th": "..."   # 2-4 ประโยค โทนแบบตัวอย่าง
    }
    """
    results: List[Dict[str, Any]] = []
    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]
        payload = []
        for idx, n in enumerate(chunk):
            payload.append({
                "id": idx,
                "feed_country": (n.get("feed_country") or "").strip(),
                "title": n.get("title", ""),
                "summary": n.get("summary", ""),
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
- headline_th: หัวข้อไทย 1 บรรทัด (สั้น กระชับ แบบข่าว)
- summary_th: 2–4 ประโยค แบบตัวอย่างด้านบน (ข่าวรายงาน) และต้องมีคำ/วลีจาก title/summary อย่างน้อย 1 จุด
ข้อห้าม:
- ห้ามเดาข้อมูลนอก title/summary
- ห้ามมีภาษาอังกฤษยาว ๆ (ยกเว้นคำย่อที่จำเป็นเช่น LNG, AI)

ตอบเป็น JSON เท่านั้น:
{{
  "items":[
    {{
      "id":0,
      "is_energy":true,
      "bucket":"domestic_policy",
      "headline_th":"...",
      "summary_th":"..."
    }}
  ]
}}

ข่าวชุดนี้:
{json.dumps(payload, ensure_ascii=False)}
"""
        text = call_groq_with_retries(prompt, temperature=0.25, max_tokens=1500)
        data = _extract_json_object(text)

        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            for _ in chunk:
                results.append({"is_energy": False})
            continue

        by_id = {}
        for it in data["items"]:
            if isinstance(it, dict) and "id" in it:
                by_id[it.get("id")] = it

        for idx, _n in enumerate(chunk):
            it = by_id.get(idx, {"is_energy": False})
            if not isinstance(it, dict):
                it = {"is_energy": False}
            # enforce thai on outputs
            if it.get("is_energy"):
                it["headline_th"] = enforce_thai((it.get("headline_th") or "").strip())
                it["summary_th"] = enforce_thai((it.get("summary_th") or "").strip())
            results.append(it)

    return results

# ============================================================================================================
# Digest text formatting
# ============================================================================================================

THAI_MONTH_ABBR = ["ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.","ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]

def thai_date_str(dt: datetime) -> str:
    dt = dt.astimezone(bangkok_tz)
    day = dt.day
    mon = THAI_MONTH_ABBR[dt.month - 1]
    year_be = dt.year + 543
    return f"{day} {mon} {year_be}"

def news_items_by_bucket(items: List[Dict[str, Any]], bucket: str) -> List[Dict[str, Any]]:
    xs = [x for x in items if (x.get("bucket") == bucket)]
    xs.sort(key=lambda z: z.get("published") or datetime.min.replace(tzinfo=bangkok_tz), reverse=True)
    return xs[:DIGEST_MAX_PER_SECTION]

def _render_section(items: List[Dict[str, Any]], with_summary: bool) -> str:
    if not items:
        return "-"

    lines = []
    for i, n in enumerate(items, 1):
        head = (n.get("headline_th") or n.get("title") or "").strip()
        summ = (n.get("summary_th") or "").strip()
        link = (n.get("final_url") or n.get("link") or "").strip()

        if with_summary:
            # ✅ สาระสำคัญ: "1.กระทรวง..." (ไม่เว้นวรรค)
            text = summ if summ else head
            lines.append(f"{i}.{text}")
            if link:
                lines.append(link)
        else:
            # ✅ หัวข้อข่าว: "1. พลังงาน..." (เว้นวรรค)
            lines.append(f"{i}. {head if head else (n.get('title') or '')}")

    return "\n".join(lines)

def build_energy_digest_text(news_items: List[Dict[str, Any]], report_dt: datetime, with_summary: bool) -> str:
    date_txt = thai_date_str(report_dt)
    title = "สรุปสาระสำคัญข่าวพลังงาน" if with_summary else "สรุปหัวข้อข่าวพลังงาน"
    out = [f"{title} วันที่ {date_txt}"]

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
        print("[DRY_RUN] send_to_line messages:", json.dumps(messages, ensure_ascii=False)[:800], "...")
        return

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    if LINE_TARGET == "user":
        if not LINE_USER_ID:
            raise RuntimeError("LINE_TARGET=user แต่ไม่พบ LINE_USER_ID")
        payload = {"to": LINE_USER_ID, "messages": messages}
        url = LINE_PUSH_URL
    else:
        payload = {"messages": messages}
        url = LINE_BROADCAST_URL

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"LINE API error {r.status_code}: {r.text}")

# ============================================================================================================
# Optional: Flex message builder (เดิม) - เก็บไว้ให้ compatibility
# ============================================================================================================

def create_flex(news: Dict[str, Any]) -> Dict[str, Any]:
    # Minimal flex based on impact content
    hero = news.get("hero") or DEFAULT_HERO_URL
    title = (news.get("title") or "")[:80]
    impact = (news.get("impact") or "").strip()
    country = (news.get("country") or "-").strip()
    project = (news.get("project") or "-").strip()
    category = (news.get("category") or "-").strip()
    link = (news.get("final_url") or news.get("link") or "").strip()

    # Source rating
    score = news.get("source_score", 0.0)
    src_txt = f"ความน่าเชื่อถือ: {score:.2f}" if SHOW_SOURCE_RATING else ""

    body_contents = [
        {"type": "text", "text": title, "weight": "bold", "wrap": True, "size": "md"},
        {"type": "text", "text": f"ประเทศ: {country}", "wrap": True, "size": "sm", "color": "#555555"},
        {"type": "text", "text": f"โครงการ: {project}", "wrap": True, "size": "sm", "color": "#555555"},
        {"type": "text", "text": f"ประเภท: {category}", "wrap": True, "size": "sm", "color": "#555555"},
    ]

    if src_txt:
        body_contents.append({"type": "text", "text": src_txt, "wrap": True, "size": "xs", "color": "#888888"})

    body_contents.append({"type": "separator", "margin": "md"})
    body_contents.append({"type": "text", "text": impact, "wrap": True, "size": "sm"})

    flex = {
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
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [c for c in body_contents if c],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {"type": "uri", "label": "อ่านข่าว", "uri": link or news.get("link") or ""},
                    }
                ],
                "flex": 0,
            },
        },
    }
    return flex

# ============================================================================================================
# Keyword gate (optional) (เดิม)
# ============================================================================================================

KEYWORDS = [
    "oil", "crude", "gas", "lng", "opec", "power", "electricity", "sanction",
    "pipeline", "refinery", "diesel", "gasoline", "brent", "wti", "dubai",
    "ค่าไฟ", "น้ำมัน", "ก๊าซ", "LNG", "พลังงาน", "โรงไฟฟ้า", "คว่ำบาตร"
]

def keyword_hit(n: Dict[str, Any]) -> bool:
    t = (n.get("title") or "") + " " + (n.get("summary") or "")
    tl = t.lower()
    for kw in KEYWORDS:
        if kw.lower() in tl:
            return True
    return False

# ============================================================================================================
# Main pipeline
# ============================================================================================================

def prepare_items(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Resolve final URLs & hero images & source score
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

def run_project_mode(selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Returns:
      messages (LINE messages list)
      sent_links (links to track)
    """
    # Optional keyword gate (เดิม)
    if USE_KEYWORD_GATE:
        selected = [x for x in selected if keyword_hit(x)]

    # Score filter
    selected = [x for x in selected if (x.get("source_score", 0.0) >= MIN_SOURCE_SCORE)]

    # LLM tag & filter
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

    # Limit output
    passed.sort(key=lambda x: x.get("published") or datetime.min.replace(tzinfo=bangkok_tz), reverse=True)
    passed = passed[:PROJECT_SEND_LIMIT]

    if not passed:
        return (create_text_messages("ไม่พบข่าวที่มีผลกระทบต่อโครงการตามเงื่อนไข"), [])

    # Build LINE messages: ส่งเป็น Flex ทีละข่าว (หรือจะเปลี่ยนเป็น Text ก็ได้)
    msgs: List[Dict[str, Any]] = []
    for n in passed:
        msgs.append(create_flex(n))

    links = [x.get("link") for x in passed if x.get("link")]
    return (msgs, links)

def run_digest_mode(selected: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Returns:
      messages (LINE messages list)  -> text digest (2 ชุด: สาระสำคัญ + หัวข้อข่าว)
      sent_links (links to track)
    """
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

    report_dt = max(
        [x.get("published") for x in digest_items if x.get("published")],
        default=datetime.now(bangkok_tz),
    )

    text_full = build_energy_digest_text(digest_items, report_dt, with_summary=True)
    text_titles = build_energy_digest_text(digest_items, report_dt, with_summary=False)

    msgs: List[Dict[str, Any]] = []
    msgs += create_text_messages(text_full)
    msgs += create_text_messages("━━━━━━━━━━━━━━━━━━")
    msgs += create_text_messages(text_titles)

    links = [x.get("link") for x in digest_items if x.get("link")]
    return (msgs, links)

def main():
    print("ดึงข่าว...")
    raw = load_news()
    print("จำนวนข่าวดิบทั้งหมด:", len(raw))

    sent = load_sent_links()
    raw = dedupe_news(raw, sent)
    print("หลังตัดข่าวซ้ำ/เคยส่ง:", len(raw))

    # เลือกชุดข่าวที่จะส่งเข้า LLM (คุณปรับได้)
    # เลือกข่าวล่าสุด 80 รายการเป็นต้น
    selected = raw[:80]
    selected = prepare_items(selected)

    all_msgs: List[Dict[str, Any]] = []
    all_links: List[str] = []

    if OUTPUT_MODE not in ("both", "project_only", "digest_only"):
        print("OUTPUT_MODE ไม่ถูกต้อง -> ใช้ both")
        mode = "both"
    else:
        mode = OUTPUT_MODE

    if mode in ("both", "project_only"):
        if ADD_SECTION_HEADERS:
            all_msgs += create_text_messages("📌 สรุปข่าวผลกระทบต่อโครงการ (Project Impact)")
        msgs, links = run_project_mode(selected)
        all_msgs += msgs
        all_links += links

    if mode == "both":
        if ADD_SECTION_HEADERS:
            all_msgs += create_text_messages("")

    if mode in ("both", "digest_only"):
        if ADD_SECTION_HEADERS:
            all_msgs += create_text_messages("📰 สรุปข่าวพลังงานรายวัน (Energy Digest)")
        msgs, links = run_digest_mode(selected)
        all_msgs += msgs
        all_links += links

    # ส่ง LINE
    send_to_line(all_msgs)

    # บันทึกกันส่งซ้ำ
    save_sent_links([normalize_url(x) for x in all_links if x])

    print("ส่งสำเร็จ:", len(all_msgs), "messages")

if __name__ == "__main__":
    main()
