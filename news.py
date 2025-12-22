# ============================================================================================================
# PTTEP Domestic News Bot (Best Fix) + Groq
# - 2-step pre-filter: Topic Gate + Foreign-location Gate
# - LLM returns evidence; impacts must link to title/summary
# - Only 1 project line: "โครงการที่เกี่ยวข้อง" (if ALL -> expand by country)
# - Better image fetching: follow redirects + parse og:image/twitter:image
# - Anti-hang: RSS timeout + deadline + limits
# - ✅ Groq LLM + Batch (ลดจำนวน calls)
# ============================================================================================================

import os
import re
import json
import time
import random
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, quote_plus

import feedparser
import requests
from dateutil import parser as dateutil_parser
import pytz
from groq import Groq

# ============================================================================================================
# ENV
# ============================================================================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

if not GROQ_API_KEY:
    raise RuntimeError("ไม่พบ GROQ_API_KEY")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")

GROQ_MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant"
groq_client = Groq(api_key=GROQ_API_KEY)

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "10"))

def _as_limit(env_name: str, default: str = "0"):
    """<=0 => None (unlimited)"""
    try:
        v = int(os.getenv(env_name, default))
        return None if v <= 0 else v
    except Exception:
        return None

MAX_PER_COUNTRY = _as_limit("MAX_PER_COUNTRY", "0")
MAX_GLOBAL_ITEMS = _as_limit("MAX_GLOBAL_ITEMS", "0")
MAX_LLM_ITEMS = _as_limit("MAX_LLM_ITEMS", "0")

RUN_DEADLINE_MIN = int(os.getenv("RUN_DEADLINE_MIN", "0"))  # 0 = no deadline
RSS_TIMEOUT_SEC = int(os.getenv("RSS_TIMEOUT_SEC", "15"))
ARTICLE_TIMEOUT_SEC = int(os.getenv("ARTICLE_TIMEOUT_SEC", "12"))

SLEEP_MIN = float(os.getenv("SLEEP_MIN", "0.4" if os.getenv("GITHUB_ACTIONS") else "0.8"))
SLEEP_MAX = float(os.getenv("SLEEP_MAX", "0.9" if os.getenv("GITHUB_ACTIONS") else "1.6"))
SLEEP_BETWEEN_CALLS = (max(0.0, SLEEP_MIN), max(SLEEP_MIN, SLEEP_MAX))

ENABLE_IMPACT_REWRITE = os.getenv("ENABLE_IMPACT_REWRITE", "true").strip().lower() in ["1", "true", "yes", "y"]
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ["1", "true", "yes", "y"]

MAX_ENTRIES_PER_FEED = int(os.getenv("MAX_ENTRIES_PER_FEED", "80"))

DEFAULT_ICON_URL = os.getenv(
    "DEFAULT_ICON_URL",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/News_icon.png/640px-News_icon.png"
)

bangkok_tz = pytz.timezone("Asia/Bangkok")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})

GROQ_CALLS = 0

# ============================================================================================================
# COUNTRIES
# ============================================================================================================

COUNTRY_QUERY = {
    "Thailand": "Thailand OR ไทย OR ประเทศไทย OR Bangkok",
    "Myanmar": "Myanmar OR Burma OR เมียนมา OR พม่า",
    "Vietnam": "Vietnam OR เวียดนาม",
    "Malaysia": "Malaysia OR มาเลเซีย",
    "Indonesia": "Indonesia OR อินโดนีเซีย",
    "UAE": "UAE OR \"United Arab Emirates\" OR Abu Dhabi OR Dubai OR สหรัฐอาหรับเอมิเรตส์",
    "Oman": "Oman OR โอมาน",
    "Algeria": "Algeria OR แอลจีเรีย",
    "Mozambique": "Mozambique OR โมซัมบิก OR Rovuma",
    "Australia": "Australia OR ออสเตรเลีย",
    "Brazil": "Brazil OR บราซิล",
    "Mexico": "Mexico OR เม็กซิโก",
}
PROJECT_COUNTRIES = sorted(list(COUNTRY_QUERY.keys()))

PROJECT_COUNTRY_SYNONYMS = {
    "Thailand": ["thailand", "thai", "bangkok", "ประเทศไทย", "ไทย", "กรุงเทพ"],
    "Myanmar": ["myanmar", "burma", "เมียนมา", "พม่า", "yangon", "naypyidaw"],
    "Vietnam": ["vietnam", "viet nam", "เวียดนาม", "hanoi", "ho chi minh"],
    "Malaysia": ["malaysia", "มาเลเซีย", "kuala lumpur"],
    "Indonesia": ["indonesia", "อินโดนีเซีย", "jakarta"],
    "UAE": ["uae", "united arab emirates", "dubai", "abu dhabi", "สหรัฐอาหรับเอมิเรตส์", "ดูไบ", "อาบูดาบี"],
    "Oman": ["oman", "โอมาน", "muscat", "มัสกัต"],
    "Algeria": ["algeria", "แอลจีเรีย", "algiers", "แอลเจียร์"],
    "Mozambique": ["mozambique", "โมซัมบิก", "rovuma", "maputo", "มาปูโต"],
    "Australia": ["australia", "ออสเตรเลีย", "sydney", "melbourne", "perth"],
    "Brazil": ["brazil", "brasil", "บราซิล", "rio", "sao paulo"],
    "Mexico": ["mexico", "เม็กซิโก", "mexico city", "toluca", "guadalajara", "monterrey"],
}

def detect_project_countries(text: str):
    t = (text or "").lower()
    hits = []
    for c, keys in PROJECT_COUNTRY_SYNONYMS.items():
        if any(k in t for k in keys):
            hits.append(c)
    return sorted(set(hits))

# ============================================================================================================
# PROJECTS (แก้ชื่อให้ตรงของจริงได้เลย)
# ============================================================================================================

PROJECTS_BY_COUNTRY = {
    "Thailand": ["G1/61 (Erawan)", "G2/61 (Bongkot)", "Arthit", "S1", "Contract 4", "B8/32", "9A", "Sinphuhorm", "MTJDA A-18"],
    "Myanmar": ["Zawtika", "Yadana", "Yetagun"],
    "Vietnam": ["Block B & 48/95", "Block 52/97", "16-1 (Te Giac Trang)"],
    "Malaysia": ["SK309", "SK311", "SK410B", "MTJDA A-18"],
    "Indonesia": ["South Sageri", "South Mandar", "Malunda"],
    "UAE": ["Ghasha Concession", "Abu Dhabi Offshore"],
    "Oman": ["Block 12"],
    "Algeria": ["Bir Seba", "Hassi Bir Rekaiz (HBR)", "Touat"],
    "Mozambique": ["Area 1 (Rovuma LNG)"],
    "Australia": ["Montara", "Timor Sea assets"],
    "Brazil": ["BM-ES-23", "BM-ES-24"],
    "Mexico": ["Mexico Block 12"],
}

def projects_for_country(country: str):
    return PROJECTS_BY_COUNTRY.get(country, [])

# ============================================================================================================
# RSS
# ============================================================================================================

def google_news_rss(q: str, hl="en", gl="US", ceid="US:en"):
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"

LEGACY_FEEDS = [
    ("Oilprice", "GLOBAL", "https://oilprice.com/rss/main"),
    ("Economist", "GLOBAL", "https://www.economist.com/latest/rss.xml"),
    ("YahooFinance", "GLOBAL", "https://finance.yahoo.com/news/rssindex"),
]

NEWS_FEEDS = []
for c in PROJECT_COUNTRIES:
    NEWS_FEEDS.append(("GoogleNews", c, google_news_rss(COUNTRY_QUERY[c])))
NEWS_FEEDS.extend(LEGACY_FEEDS)

# ============================================================================================================
# TOPIC GATE
# ============================================================================================================

TOPIC_KEYWORDS = [
    "oil","gas","lng","crude","petroleum","upstream","offshore","rig","drilling","pipeline",
    "refinery","psc","concession","opec","energy","power","electricity","renewable",
    "tax","royalty","permit","license","sanction","tariff","regulation","policy","election",
    "inflation","currency","fx","interest rate","central bank",
    "border","conflict","protest","strike","security","attack","port","shipping","customs","logistics","insurance",
    "น้ำมัน","ก๊าซ","ปิโตรเลียม","สำรวจ","ผลิต","แท่นขุด","ท่อส่ง","สัมปทาน","psc",
    "ภาษี","ใบอนุญาต","กฎระเบียบ","คว่ำบาตร","การเลือกตั้ง","ค่าเงิน","ดอกเบี้ย",
    "ชายแดน","ความไม่สงบ","ประท้วง","นัดหยุดงาน","ท่าเรือ","ขนส่ง","ศุลกากร","โลจิสติกส์","ประกันภัย",
]

NON_PROJECTY_KEYWORDS = [
    "shooting","murder","rape","assault","crime","sentenced","prison","court","police",
    "victims","holocaust","celebrity","fashion","sports","festival","heritage","conservation",
    "watch videos","entertainment","gossip",
    "คดี","ศาล","ตำรวจ","จำคุก","คนร้าย","เหยื่อ","บันเทิง","ดารา","กีฬา","แฟชั่น","เทศกาล","มรดก","อนุรักษ์",
]

def passes_topic_gate(title: str, summary: str) -> bool:
    t = f"{title or ''} {summary or ''}".lower()
    if any(k in t for k in TOPIC_KEYWORDS):
        return True
    if any(k in t for k in NON_PROJECTY_KEYWORDS):
        return False
    return False

# ============================================================================================================
# FOREIGN-LOCATION GATE
# ============================================================================================================

FOREIGN_MARKERS = [
    "united states","u.s.","usa","kansas","texas","new york","washington",
    "united kingdom","uk","england","london",
    "canada","toronto",
    "germany","france","italy","spain","european",
]

def likely_foreign_event(feed_country: str, title: str, summary: str) -> bool:
    if feed_country not in PROJECT_COUNTRIES:
        return False
    t = f"{title or ''} {summary or ''}".lower()
    foreign_hit = any(m in t for m in FOREIGN_MARKERS)
    if not foreign_hit:
        return False
    target_keys = PROJECT_COUNTRY_SYNONYMS.get(feed_country, [])
    target_hit = any(k in t for k in target_keys)
    return (not target_hit)

# ============================================================================================================
# HELPERS
# ============================================================================================================

def _normalize_link(url: str) -> str:
    try:
        p = urlparse(url)
        scheme = (p.scheme or "https").lower()
        netloc = (p.netloc or "").lower()
        path = p.path or ""
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        for k in list(q.keys()):
            lk = k.lower()
            if lk.startswith("utm_") or lk in ["fbclid","gclid","mc_cid","mc_eid"]:
                q.pop(k, None)
        query = urlencode(sorted(q.items()))
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url or ""

def get_sent_links_file():
    d = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    os.makedirs("sent_links", exist_ok=True)
    return os.path.join("sent_links", f"sent_links_{d}.txt")

def load_sent_links():
    fp = get_sent_links_file()
    if not os.path.exists(fp):
        return set()
    with open(fp, "r", encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())

def save_sent_links(links):
    fp = get_sent_links_file()
    existing = load_sent_links()
    existing.update(_normalize_link(x) for x in links if x)
    with open(fp, "w", encoding="utf-8") as f:
        for x in sorted(existing):
            f.write(x + "\n")

def _extract_json_object(raw: str):
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(json)?", "", s, flags=re.I).strip()
        s = re.sub(r"```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    first = s.find("{")
    last = s.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = s[first:last + 1]
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(candidate)
        except Exception:
            return None
    return None

def has_meaningful_impact(bullets) -> bool:
    if not bullets or not isinstance(bullets, list):
        return False
    txt = " ".join([str(x) for x in bullets if str(x).strip()])
    bad = ["ยังไม่พบผลกระทบ","ไม่พบผลกระทบ","ไม่ระบุผลกระทบ","ไม่เกี่ยวข้อง","ข้อมูลไม่เพียงพอ"]
    t = txt.lower().replace(" ", "")
    if any(x.replace(" ", "") in t for x in bad):
        return False
    return len(txt.strip()) >= 25

def parse_feed_with_timeout(url: str):
    r = S.get(url, timeout=RSS_TIMEOUT_SEC, allow_redirects=True)
    r.raise_for_status()
    return feedparser.parse(r.text)

def fetch_article_image(url: str):
    """Follow redirects (important for Google News) then parse og:image/twitter:image."""
    try:
        if not url or not url.startswith(("http://","https://")):
            return None
        r = S.get(url, timeout=ARTICLE_TIMEOUT_SEC, allow_redirects=True)
        if r.status_code >= 300:
            return None
        html = r.text
        m = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r'name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            return m.group(1).strip()
        return None
    except Exception:
        return None

# ============================================================================================================
# GROQ LLM (retry + batch)
# ============================================================================================================

def _is_429(e: Exception) -> bool:
    s = str(e).lower()
    return ("429" in s) or ("too many requests" in s) or ("rate limit" in s)

def call_groq(prompt: str, temperature: float = 0.35) -> str:
    global GROQ_CALLS
    time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[
            {"role": "system", "content": "Return STRICT JSON only. No markdown. No extra text."},
            {"role": "user", "content": prompt},
        ],
        temperature=float(temperature),
    )
    GROQ_CALLS += 1
    return (resp.choices[0].message.content or "").strip()

def call_groq_with_retries(prompt: str, temperature: float = 0.35) -> str:
    last = None
    for i in range(1, MAX_RETRIES + 1):
        try:
            return call_groq(prompt, temperature=temperature)
        except Exception as e:
            last = e
            if _is_429(e) and i < MAX_RETRIES:
                sleep_s = min(25.0, (1.8 ** i) + random.uniform(0.5, 1.5))
                print(f"[Groq] 429 -> retry {i}/{MAX_RETRIES} in {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
            if i < MAX_RETRIES:
                sleep_s = min(12.0, (1.5 ** i) + random.uniform(0.3, 1.0))
                print(f"[Groq] error -> retry {i}/{MAX_RETRIES} in {sleep_s:.1f}s: {type(e).__name__}: {e}")
                time.sleep(sleep_s)
                continue
            raise
    raise last

GENERIC_PATTERNS = [
    "อาจกระทบต้นทุน", "อาจกระทบกฎระเบียบ", "อาจกระทบตารางงาน",
    "ความเสี่ยงต่อการดำเนินงาน", "กระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง",
]
SPECIFIC_HINTS = [
    "ใบอนุญาต","ภาษี","psc","สัมปทาน","ประกัน","ผู้รับเหมา","แรงงาน",
    "ท่าเรือ","ขนส่ง","ศุลกากร","นำเข้า","ค่าเงิน","fx","ความปลอดภัย",
    "คว่ำบาตร","sanction","ประท้วง","นัดหยุดงาน","ความไม่สงบ",
]

def looks_generic_bullets(bullets) -> bool:
    if not bullets or not isinstance(bullets, list):
        return True
    joined = " ".join([str(x) for x in bullets]).lower()
    generic_hit = any(p.replace(" ", "") in joined.replace(" ", "") for p in GENERIC_PATTERNS)
    specific_hit = any(k.lower() in joined for k in SPECIFIC_HINTS)
    return generic_hit and (not specific_hit)

def diversify_bullets(bullets):
    if not bullets:
        return bullets
    starters = [re.sub(r"\s+", "", (b or "")[:10]) for b in bullets]
    if len(set(starters)) == 1 and len(bullets) >= 2:
        variants = ["เสี่ยงที่", "คาดว่า", "มีโอกาส", "อาจทำให้", "อาจต้อง"]
        out = []
        for i, b in enumerate(bullets):
            bb = (b or "").strip()
            bb = re.sub(r"^(คาดว่า|มีโอกาส|อาจทำให้|เสี่ยงที่|อาจต้อง)\s*", "", bb)
            out.append(f"{variants[i % len(variants)]} {bb}".strip())
        return out
    return bullets

def rewrite_impact_bullets(news, country, bullets):
    prompt = f"""
คุณคือ Analyst ของ PTTEP
ช่วยเขียน bullet ผลกระทบให้เป็น “ภาษาคน + เฉพาะเจาะจง” (2–4 bullets)

ข้อกำหนด:
- ห้ามใช้ประโยคแม่แบบกว้าง ๆ เช่น "อาจกระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง" แบบรวม ๆ
- ทุก bullet ต้องมี “กลไก” อย่างน้อย 1 อย่าง เช่น ใบอนุญาต/ภาษี-PSC/ความปลอดภัย/โลจิสติกส์/ผู้รับเหมา/ประกันภัย/FX/ศุลกากร/คว่ำบาตร
- ต้องโยงกับ “หัวข้อ/สรุป” อย่างน้อย 1 จุด (ห้ามเดา)
- 1 ประโยค/บรรทัด ไม่เกิน ~24 คำ

ประเทศ: {country}
หัวข้อ: {news.get("title","")}
สรุป: {news.get("summary","")}

bullet เดิม:
{json.dumps(bullets, ensure_ascii=False)}

ตอบเป็น JSON เท่านั้น:
{{"impact_bullets": ["...","..."]}}
"""
    text = call_groq_with_retries(prompt, temperature=0.7)
    data = _extract_json_object(text)
    if isinstance(data, dict) and isinstance(data.get("impact_bullets"), list):
        out = [str(x).strip() for x in data["impact_bullets"] if str(x).strip()]
        return diversify_bullets(out[:6])
    return diversify_bullets(bullets)

def groq_batch_tag_and_filter(news_list: List[Dict[str, Any]], chunk_size: int = 10) -> List[Dict[str, Any]]:
    """
    ส่งเป็นชุด ลด calls:
    - input: list ข่าว
    - output: list tag dict (ตำแหน่งตรงกัน)
    """
    results: List[Dict[str, Any]] = []
    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]
        payload = []
        for idx, n in enumerate(chunk):
            fc = (n.get("feed_country") or "").strip()
            payload.append({
                "id": idx,
                "feed_country": fc,
                "allowed_projects": projects_for_country(fc)[:12] if fc in PROJECT_COUNTRIES else [],
                "title": n.get("title", ""),
                "summary": n.get("summary", ""),
            })

        prompt = f"""
คุณเป็นผู้ช่วยคัดกรองข่าวสำหรับการดำเนินงาน

กติกาเข้ม:
1) รับเฉพาะข่าวที่เกี่ยวกับ: พลังงาน/นโยบายรัฐ-กฎระเบียบ-ภาษี-PSC/ความมั่นคง-ความปลอดภัยที่กระทบงาน/โลจิสติกส์/คว่ำบาตร/FX
   ข่าวสังคม/ไลฟ์สไตล์/คดีทั่วไป/อุบัติเหตุทั่วไปที่ไม่เกี่ยวการดำเนินงาน → is_relevant=false
2) ต้องเป็นเหตุการณ์ “ในประเทศ feed_country” จริง ๆ
   ถ้าหัวข้อ/สรุประบุว่าเหตุเกิดนอกประเทศ แม้มีคำว่าเกี่ยวกับประเทศนั้น → is_relevant=false
3) ถ้า is_relevant=true:
   - country ต้องเท่ากับ feed_country เท่านั้น
   - projects: เลือก 0–2 โครงการจาก allowed_projects ของข่าวนั้นเท่านั้น
     ถ้าไม่ชัดเจนให้ใส่ ["ALL"]
   - evidence: 1–2 วลีสั้น ๆ (คัดจาก title/summary) เพื่อยืนยัน
   - impact_bullets: 2–4 bullets ภาษาคน และต้องโยงกับ evidence/หัวข้อ (ห้ามเดา)
     ทุก bullet ต้องมี “กลไก” อย่างน้อย 1 อย่าง

ให้ตอบเป็น JSON เท่านั้น รูปแบบ:
{{
  "items": [
    {{
      "id": 0,
      "is_relevant": true/false,
      "country": "Thailand",
      "projects": ["ALL"] or ["..."],
      "impact_bullets": ["...","..."],
      "impact_level": "low|medium|high|unknown",
      "evidence": ["...","..."],
      "why_relevant": "..."
    }}
  ]
}}

ข่าวชุดนี้:
{json.dumps(payload, ensure_ascii=False)}
"""
        text = call_groq_with_retries(prompt, temperature=0.35)
        data = _extract_json_object(text)

        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            for _ in chunk:
                results.append({"is_relevant": False})
            continue

        by_id = {}
        for it in data["items"]:
            if isinstance(it, dict) and "id" in it:
                by_id[it.get("id")] = it

        for idx, n in enumerate(chunk):
            t = by_id.get(idx, {"is_relevant": False})
            # normalize minimal
            if not isinstance(t, dict):
                t = {"is_relevant": False}
            results.append(t)

    return results

# ============================================================================================================
# FETCH WINDOW: 21:00 yesterday -> 06:00 today (Bangkok)
# ============================================================================================================

def fetch_news_window():
    now_local = datetime.now(bangkok_tz)
    start = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end = now_local.replace(hour=6, minute=0, second=0, microsecond=0)

    out = []
    for site, feed_country, url in NEWS_FEEDS:
        try:
            feed = parse_feed_with_timeout(url)
            entries = list(feed.entries or [])[:MAX_ENTRIES_PER_FEED]

            for e in entries:
                pub = getattr(e, "published", None) or getattr(e, "updated", None)
                if not pub:
                    continue

                dt = dateutil_parser.parse(pub)
                if dt.tzinfo is None:
                    dt = bangkok_tz.localize(dt)
                dt_local = dt.astimezone(bangkok_tz)

                if not (start <= dt_local <= end):
                    continue

                link = _normalize_link(getattr(e, "link", "") or "")
                if not link:
                    continue

                title = (getattr(e, "title", "") or "").strip()
                summary_raw = getattr(e, "summary", "") or ""
                summary = re.sub(r"\s+", " ", re.sub("<.*?>", " ", summary_raw)).strip()

                hints = detect_project_countries(f"{title}\n{summary}")

                out.append({
                    "site": site,
                    "feed_country": feed_country,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": dt_local,
                    "countries_hint": hints,
                })
        except Exception as ex:
            print(f"[WARN] feed failed: {site}/{feed_country} -> {type(ex).__name__}: {ex}")
            continue

    uniq, seen = [], set()
    for n in out:
        k = _normalize_link(n["link"])
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)

    uniq.sort(key=lambda x: x["published"], reverse=True)
    return uniq

# ============================================================================================================
# FLEX
# ============================================================================================================

def _shorten(items, take=4):
    items = items or []
    if not items:
        return "ALL"
    if len(items) <= take:
        return ", ".join(items)
    return ", ".join(items[:take]) + f" +{len(items)-take}"

def create_flex(news_items):
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    for n in news_items:
        bullets = n.get("impact_bullets") or []
        if not isinstance(bullets, list):
            bullets = [str(bullets)]

        country = (n.get("country") or "ไม่ระบุ").strip()
        projects = n.get("projects") or ["ALL"]
        proj_txt = _shorten(projects, take=4)

        link = n.get("link") or "https://news.google.com/"
        img = n.get("image") or DEFAULT_ICON_URL

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": (n.get("title","")[:120]), "wrap": True, "weight": "bold", "size": "lg"},
                    {
                        "type": "box",
                        "layout": "baseline",
                        "spacing": "md",
                        "contents": [
                            {"type": "text", "text": n.get("published").strftime("%d/%m/%Y %H:%M"), "size": "sm", "color": "#666666", "flex": 0},
                            {"type": "text", "text": f"{country} | {n.get('site','')}", "size": "sm", "color": "#1E90FF", "wrap": True},
                        ],
                    },
                    {"type": "text", "text": f"โครงการที่เกี่ยวข้อง: {proj_txt}", "size": "sm", "color": "#666666", "wrap": True, "margin": "sm"},
                    {"type": "text", "text": "ผลกระทบต่อโครงการ", "size": "lg", "weight": "bold", "color": "#000000", "margin": "lg"},
                    *[
                        {"type": "text", "text": f"• {b}", "wrap": True, "size": "md", "color": "#000000", "weight": "bold", "margin": "xs"}
                        for b in bullets[:6]
                    ],
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "button", "style": "primary", "color": "#1DB446",
                     "action": {"type": "uri", "label": "อ่านต่อ", "uri": link}}
                ],
            },
        }
        bubbles.append(bubble)

    return [{
        "type": "flex",
        "altText": f"ข่าว PTTEP (Domestic) {now_txt}",
        "contents": {"type": "carousel", "contents": bubbles},
    }]

# ============================================================================================================
# LINE SEND
# ============================================================================================================

def send_to_line(messages):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}

    for i, msg in enumerate(messages, 1):
        payload = {"messages": [msg]}
        print("=== LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type": msg.get("type"), "altText": msg.get("altText")}]} , ensure_ascii=False))

        if DRY_RUN:
            print("[DRY_RUN] ไม่ส่งจริง")
            continue

        r = S.post(url, headers=headers, json=payload, timeout=15)
        print(f"Send {i}: {r.status_code}")
        if r.status_code >= 300:
            print("Response:", r.text[:1200])
            break

# ============================================================================================================
# MAIN
# ============================================================================================================

def main():
    deadline = None
    if RUN_DEADLINE_MIN > 0:
        deadline = time.time() + RUN_DEADLINE_MIN * 60

    print("ดึงข่าว...")
    all_news = fetch_news_window()
    print("จำนวนข่าวดิบทั้งหมด:", len(all_news))
    if not all_news:
        print("ไม่พบข่าวในช่วงเวลา")
        return

    sent = load_sent_links()
    per_country_count = {c: 0 for c in PROJECT_COUNTRIES}
    candidates, global_candidates = [], []

    # ---------- Pre-filter ----------
    for n in all_news:
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่าง pre-filter (หยุด)")
            break

        link_norm = _normalize_link(n["link"])
        if link_norm in sent:
            continue

        title, summary = n.get("title",""), n.get("summary","")
        feed_country = (n.get("feed_country") or "").strip()

        if not passes_topic_gate(title, summary):
            continue

        if feed_country in PROJECT_COUNTRIES and likely_foreign_event(feed_country, title, summary):
            continue

        if feed_country in PROJECT_COUNTRIES:
            if MAX_PER_COUNTRY is not None and per_country_count[feed_country] >= MAX_PER_COUNTRY:
                continue
            per_country_count[feed_country] += 1
            candidates.append(n)
        else:
            global_candidates.append(n)

    if MAX_GLOBAL_ITEMS is not None:
        global_candidates = global_candidates[:MAX_GLOBAL_ITEMS]

    selected = candidates + global_candidates
    selected.sort(key=lambda x: x["published"], reverse=True)

    if MAX_LLM_ITEMS is not None:
        selected = selected[:MAX_LLM_ITEMS]

    print("จำนวนข่าวที่จะส่งเข้า LLM:", len(selected))
    if not selected:
        print("ไม่มีข่าวให้ส่งเข้า LLM")
        return

    # ---------- LLM filter + impact (BATCH) ----------
    try:
        tags = groq_batch_tag_and_filter(selected, chunk_size=LLM_BATCH_SIZE)
    except Exception as e:
        if _is_429(e):
            print("Groq 429: งด LLM รอบนี้ (ไม่ล้มทั้งงาน)")
            tags = [{"is_relevant": False} for _ in selected]
        else:
            raise

    final = []
    for n, tag in zip(selected, tags):
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่าง LLM apply (หยุด)")
            break

        if not isinstance(tag, dict) or not tag.get("is_relevant"):
            continue

        feed_country = (n.get("feed_country") or "").strip()
        country = (tag.get("country") or "").strip()

        # strict: country must equal feed_country
        if country != feed_country:
            continue

        allowed_projects = set(projects_for_country(country))
        projects = tag.get("projects") or ["ALL"]
        if not isinstance(projects, list):
            projects = [str(projects)]
        projects = [str(x).strip() for x in projects if str(x).strip()] or ["ALL"]

        if projects != ["ALL"]:
            projects = [p for p in projects if p in allowed_projects] or ["ALL"]

        bullets = tag.get("impact_bullets") or []
        if not isinstance(bullets, list):
            bullets = [str(bullets)]
        bullets = [str(x).strip() for x in bullets if str(x).strip()]
        bullets = diversify_bullets(bullets[:6])

        evidence = tag.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        evidence = [str(x).strip() for x in evidence if str(x).strip()][:2]

        # evidence must appear in title/summary (reduce hallucination)
        text_lower = f"{n.get('title','')} {n.get('summary','')}".lower()
        if evidence and not any(ev.lower() in text_lower for ev in evidence if len(ev) >= 4):
            continue

        if projects == ["ALL"]:
            cp = projects_for_country(country)
            if cp:
                projects = cp

        if ENABLE_IMPACT_REWRITE and looks_generic_bullets(bullets):
            bullets = rewrite_impact_bullets(n, country, bullets)

        bullets = diversify_bullets(bullets)
        if not has_meaningful_impact(bullets):
            continue

        n["country"] = country
        n["projects"] = projects
        n["impact_bullets"] = bullets[:6]
        n["impact_level"] = (tag.get("impact_level") or "unknown")
        n["evidence"] = evidence
        n["why_relevant"] = (tag.get("why_relevant") or "").strip()

        final.append(n)

    print("จำนวนข่าวผ่านเงื่อนไข:", len(final))
    if not final:
        print("ไม่มีข่าวที่ผ่านเงื่อนไขวันนี้")
        return

    # ---------- Image ----------
    for n in final:
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่างหา image (หยุด)")
            break
        img = fetch_article_image(n.get("link",""))
        n["image"] = img if (isinstance(img, str) and img.startswith(("http://", "https://"))) else DEFAULT_ICON_URL
        time.sleep(0.12)

    msgs = create_flex(final[:10])
    send_to_line(msgs)

    save_sent_links([n["link"] for n in final])
    print("เสร็จสิ้น (Groq calls:", GROQ_CALLS, ")")

if __name__ == "__main__":
    main()
