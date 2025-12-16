# ============================================================================================================
# PTTEP Domestic-by-Project-Countries News Bot (WITH Legacy Sources)
# - คัดข่าว: ต้องเป็น “เหตุการณ์ในประเทศ” ที่อยู่ใน PROJECT_COUNTRIES เท่านั้น (strict)
# - สรุป “ผลกระทบต่อโครงการ” เป็นภาษาไทยแบบภาษาคน 2–4 bullets และพยายามไม่ให้ซ้ำรูปแบบ
# - ส่ง LINE เป็น Flex Carousel
# ============================================================================================================

import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, quote_plus

import feedparser
import requests
from dateutil import parser as dateutil_parser
import pytz
import google.generativeai as genai

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# ============================================================================================================
# ENV / SETTINGS
# ============================================================================================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash"))

GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))

# 0 = ไม่จำกัด
def _as_limit(env_name: str, default: str = "0"):
    try:
        v = int(os.getenv(env_name, default))
        return None if v <= 0 else v
    except Exception:
        return None

MAX_PER_COUNTRY = _as_limit("MAX_PER_COUNTRY", "0")      # จำกัดข่าวต่อประเทศ (0 = ไม่จำกัด)
MAX_GLOBAL_ITEMS = _as_limit("MAX_GLOBAL_ITEMS", "0")    # จำกัดข่าวจาก legacy feeds (0 = ไม่จำกัด)
MAX_LLM_ITEMS = _as_limit("MAX_LLM_ITEMS", "0")          # จำกัดจำนวนข่าวที่ส่งเข้า LLM (0 = ไม่จำกัด)

# ป้องกัน workflow ค้างยาว: ตัดจบเมื่อใช้เวลาเกิน X วินาที (0 = ปิด)
HARD_DEADLINE_SEC = int(os.getenv("HARD_DEADLINE_SEC", "1200"))  # 20 นาที default
if HARD_DEADLINE_SEC < 0:
    HARD_DEADLINE_SEC = 0

# timeouts (ช่วยแก้ปัญหา run ค้างเป็นชั่วโมง)
RSS_TIMEOUT_SEC = float(os.getenv("RSS_TIMEOUT_SEC", "20"))
ARTICLE_TIMEOUT_SEC = float(os.getenv("ARTICLE_TIMEOUT_SEC", "12"))
LINE_TIMEOUT_SEC = float(os.getenv("LINE_TIMEOUT_SEC", "20"))

# spacing ระหว่างเรียก LLM/ส่ง request (ลดเวลารันใน GitHub Actions)
if os.getenv("GITHUB_ACTIONS", "").strip().lower() in ("1", "true", "yes"):
    _default_sleep_min, _default_sleep_max = 0.4, 0.9
else:
    _default_sleep_min, _default_sleep_max = 0.8, 1.6

SLEEP_MIN = float(os.getenv("SLEEP_MIN", str(_default_sleep_min)))
SLEEP_MAX = float(os.getenv("SLEEP_MAX", str(_default_sleep_max)))
SLEEP_BETWEEN_CALLS = (max(0.0, SLEEP_MIN), max(SLEEP_MIN, SLEEP_MAX))

DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ["1", "true", "yes", "y"]
ENABLE_IMPACT_REWRITE = os.getenv("ENABLE_IMPACT_REWRITE", "true").strip().lower() in ["1", "true", "yes", "y"]

DEFAULT_ICON_URL = os.getenv(
    "DEFAULT_ICON_URL",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/News_icon.png/640px-News_icon.png",
)

# ต่อ 1 feed เก็บ entry สูงสุดเท่านี้ (กัน RSS หนาเกิน)
MAX_ENTRIES_PER_FEED = int(os.getenv("MAX_ENTRIES_PER_FEED", "80"))

bangkok_tz = pytz.timezone("Asia/Bangkok")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})

GEMINI_CALLS = 0


# ============================================================================================================
# Project countries
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
    "Myanmar": ["myanmar", "burma", "เมียนมา", "พม่า", "naypyidaw", "yangon"],
    "Vietnam": ["vietnam", "viet nam", "ฮานอย", "ho chi minh", "เวียดนาม"],
    "Malaysia": ["malaysia", "kuala lumpur", "มาเลเซีย", "กัวลาลัมเปอร์"],
    "Indonesia": ["indonesia", "jakarta", "อินโดนีเซีย", "จาการ์ตา"],
    "UAE": ["uae", "united arab emirates", "dubai", "abu dhabi", "สหรัฐอาหรับเอมิเรตส์", "ดูไบ", "อาบูดาบี"],
    "Oman": ["oman", "muscat", "โอมาน", "มัสกัต"],
    "Algeria": ["algeria", "algiers", "แอลจีเรีย", "แอลเจียร์"],
    "Mozambique": ["mozambique", "maputo", "rovuma", "โมซัมบิก", "มาปูโต"],
    "Australia": ["australia", "perth", "sydney", "aussie", "ออสเตรเลีย"],
    "Brazil": ["brazil", "brasil", "rio", "sao paulo", "บราซิล"],
    "Mexico": ["mexico", "mexico city", "เม็กซิโก", "เม็กซิโกซิตี้"],
}

def detect_project_countries(text: str):
    t = (text or "").lower()
    hits = []
    for c, keys in PROJECT_COUNTRY_SYNONYMS.items():
        if any(k in t for k in keys):
            hits.append(c)
    return sorted(set(hits))


# ============================================================================================================
# RSS sources
# ============================================================================================================
def google_news_rss(q: str, hl="en", gl="US", ceid="US:en"):
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"

LEGACY_FEEDS = [
    ("Oilprice", "GLOBAL", "https://oilprice.com/rss/main"),
    ("CleanTechnica", "GLOBAL", "https://cleantechnica.com/feed/"),
    ("HydrogenFuelNews", "GLOBAL", "https://www.hydrogenfuelnews.com/feed/"),
    ("Economist", "GLOBAL", "https://www.economist.com/latest/rss.xml"),
    ("YahooFinance", "GLOBAL", "https://finance.yahoo.com/news/rssindex"),
]

NEWS_FEEDS = []
for c in PROJECT_COUNTRIES:
    NEWS_FEEDS.append(("GoogleNews", c, google_news_rss(COUNTRY_QUERY[c])))
NEWS_FEEDS.extend(LEGACY_FEEDS)


# ============================================================================================================
# Helpers
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
            if lk.startswith("utm_") or lk in ["fbclid", "gclid", "mc_cid", "mc_eid", "ref"]:
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

def _impact_to_bullets(impact_text: str):
    if not impact_text:
        return []
    t = impact_text.strip().replace("\r\n", "\n")
    parts = [p.strip() for p in re.split(r"\n+|•", t) if p.strip()]
    out = [p for p in parts if len(p) >= 8]
    return out[:6]

def has_meaningful_impact(impact) -> bool:
    if not impact:
        return False
    if isinstance(impact, list):
        txt = " ".join([str(x) for x in impact if str(x).strip()])
    else:
        txt = str(impact)
    t = txt.lower().replace(" ", "")
    bad = ["ยังไม่พบผลกระทบ", "ไม่พบผลกระทบ", "ไม่ระบุผลกระทบ", "ไม่เกี่ยวข้อง", "ข้อมูลไม่เพียงพอ"]
    if any(x.replace(" ", "") in t for x in bad):
        return False
    return len(txt.strip()) >= 25

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
        try:
            return json.loads(candidate)
        except Exception:
            return None
    return None

def _deadline_check(start_ts: float) -> bool:
    if HARD_DEADLINE_SEC <= 0:
        return False
    return (time.time() - start_ts) > HARD_DEADLINE_SEC

def parse_feed_with_timeout(url: str):
    """
    feedparser.parse(url) บางครั้งค้างนาน/ไม่จบใน GitHub Actions
    -> ดึงด้วย requests (มี timeout) แล้วค่อย feedparser.parse(text)
    """
    r = S.get(url, timeout=RSS_TIMEOUT_SEC, allow_redirects=True)
    r.raise_for_status()
    return feedparser.parse(r.text)

def fetch_article_image(url: str):
    try:
        if not url or not url.startswith(("http://", "https://")):
            return None
        r = S.get(url, timeout=ARTICLE_TIMEOUT_SEC, headers={"User-Agent": "Mozilla/5.0"})
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
# Gemini
# ============================================================================================================
def call_gemini(prompt: str, want_json: bool = False, temperature: float = 0.35):
    global GEMINI_CALLS
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError("เกินโควต้า Gemini ประจำวัน")

    last_error = None
    for i in range(1, MAX_RETRIES + 1):
        try:
            gen_cfg = {"temperature": float(temperature), "max_output_tokens": 900}
            if want_json:
                gen_cfg["response_mime_type"] = "application/json"
            try:
                r = model.generate_content(prompt, generation_config=gen_cfg)
            except TypeError:
                r = model.generate_content(prompt)
            GEMINI_CALLS += 1
            return r
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            if any(x in msg for x in ["429", "unavailable", "deadline", "503", "500"]) and i < MAX_RETRIES:
                time.sleep(3 * i)
                continue
            raise e
    raise last_error


GENERIC_PATTERNS = [
    "อาจกระทบต้นทุน", "อาจกระทบกฎระเบียบ", "อาจกระทบตารางงาน",
    "ความเสี่ยงต่อการดำเนินงาน", "กระทบโครงการในประเทศนี้",
    "กระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง",
]
SPECIFIC_HINTS = [
    "ใบอนุญาต", "ภาษี", "psc", "สัมปทาน", "ประกัน", "ผู้รับเหมา", "แรงงาน",
    "ท่าเรือ", "ขนส่ง", "ศุลกากร", "นำเข้า", "ค่าเงิน", "fx", "ความปลอดภัย",
    "คว่ำบาตร", "sanction", "ประท้วง", "นัดหยุดงาน", "ความไม่สงบ", "ก่อการร้าย",
]

def looks_generic_bullets(bullets) -> bool:
    if not bullets or not isinstance(bullets, list):
        return True
    joined = " ".join([str(x) for x in bullets]).lower()
    generic_hit = any(p.replace(" ", "") in joined.replace(" ", "") for p in GENERIC_PATTERNS)
    specific_hit = any(k in joined for k_
