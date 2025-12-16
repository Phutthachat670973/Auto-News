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
    specific_hit = any(k in joined for k in SPECIFIC_HINTS)
    return generic_hit and (not specific_hit)

def _diversify_bullets(bullets):
    if not bullets:
        return bullets
    starters = []
    for b in bullets:
        s = (b or "").strip()
        starters.append(re.sub(r"\s+", "", s[:10]))
    if len(set(starters)) == 1 and len(bullets) >= 2:
        variants = ["อาจทำให้", "เสี่ยงที่", "คาดว่า", "มีโอกาส", "อาจต้อง"]
        new = []
        for i, b in enumerate(bullets):
            bb = (b or "").strip()
            bb = re.sub(r"^(คาดว่า|มีโอกาส|เป็นเหตุการณ์ที่|อาจ)\s*", "", bb)
            new.append(f"{variants[i % len(variants)]} {bb}".strip())
        return new
    return bullets

def rewrite_impact_bullets(news, country, projects, bullets):
    prompt = f"""
คุณคือ Analyst ของ PTTEP
ช่วย "เขียนใหม่" bullet ผลกระทบให้เป็นภาษาไทยแบบภาษาคนและเฉพาะเจาะจงขึ้น (2–4 bullets)

ข้อห้าม (สำคัญ):
- ห้ามใช้ประโยคแม่แบบกว้าง ๆ เช่น "อาจกระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง" แบบรวม ๆ
- ห้ามเขียนซ้ำโครงเดิมทุกบรรทัด (เช่นขึ้นต้นว่า "เป็นเหตุการณ์..." ทุกบรรทัด)

สิ่งที่ต้องมี:
- ทุก bullet ต้องมี "กลไก" อย่างน้อย 1 อย่าง: ใบอนุญาต / ภาษี-PSC / ความปลอดภัย / โลจิสติกส์-ขนส่ง / แรงงาน-ผู้รับเหมา / ประกันภัย / การเงิน-FX / ศุลกากร / คว่ำบาตร
- ถ้าไม่แน่ใจ ให้ใช้คำว่า "คาดว่า/มีโอกาส/เสี่ยงที่" + เหตุผลสั้น ๆ 1 วลี
- แต่ละ bullet 1 ประโยค ไม่เกิน ~24 คำ

ตัวอย่างสไตล์ที่ดี (ตัวอย่างเท่านั้น):
- "เสี่ยงที่งานภาคสนามต้องเพิ่มมาตรการความปลอดภัย ทำให้ค่าใช้จ่ายผู้รับเหมาสูงขึ้น"
- "คาดว่าการอนุมัติใบอนุญาตอาจช้าลง ถ้ารัฐออกข้อกำหนดใหม่ในช่วงนี้"

ข้อมูลข่าว:
ประเทศ: {country}
โครงการ: {", ".join(projects) if projects else "ALL"}
หัวข้อ: {news.get("title","")}
สรุปจาก RSS: {news.get("summary","")}

bullet เดิม:
{json.dumps(bullets, ensure_ascii=False)}

ตอบกลับเป็น JSON เท่านั้น:
{{"impact_bullets": ["...","..."]}}
"""
    r = call_gemini(prompt, want_json=True, temperature=0.75)
    raw = (getattr(r, "text", "") or "").strip()
    data = _extract_json_object(raw)
    if isinstance(data, dict) and isinstance(data.get("impact_bullets"), list):
        out = [str(x).strip() for x in data["impact_bullets"] if str(x).strip()]
        return _diversify_bullets(out[:6])
    return _diversify_bullets(bullets)

FALLBACK_IMPACTS = [
    "เสี่ยงที่ขั้นตอนอนุมัติ/ใบอนุญาตจะช้าลง หากหน่วยงานรัฐออกมาตรการเพิ่ม",
    "อาจต้องเพิ่มงบความปลอดภัย/ประกันภัยของทีมงานและผู้รับเหมา ถ้าสถานการณ์ตึงตัว",
    "โลจิสติกส์ (ท่าเรือ/ขนส่ง/ศุลกากร) อาจสะดุดระยะสั้น ทำให้เลื่อนส่งมอบของบางรายการ",
    "มีโอกาสกระทบเงื่อนไขภาษี/PSC/กฎพลังงาน ต้องติดตามประกาศอย่างเป็นทางการ",
    "ค่าเงิน/ต้นทุนการเงินอาจผันผวน ทำให้สัญญาจัดซื้อบางส่วนต้องเผื่อส่วนต่าง",
]

def rule_fallback(news):
    feed_country = (news.get("feed_country") or "").strip()

    if feed_country == "GLOBAL":
        hints = news.get("countries_hint") or []
        if len(hints) != 1:
            return {"is_relevant": False}
        c = hints[0]
    else:
        if feed_country not in PROJECT_COUNTRIES:
            return {"is_relevant": False}
        c = feed_country

    bullets = random.sample(FALLBACK_IMPACTS, k=min(2, len(FALLBACK_IMPACTS)))
    return {
        "is_relevant": True,
        "summary": "",
        "topic_type": "other",
        "region": "other",
        "impact_bullets": _diversify_bullets(bullets)[:4],
        "impact_level": "unknown",
        "country": c,
        "projects": ["ALL"],
    }

def gemini_tag_and_filter(news):
    schema = {
        "type": "object",
        "properties": {
            "is_relevant": {"type": "boolean"},
            "summary": {"type": "string"},
            "topic_type": {
                "type": "string",
                "enum": ["supply_disruption", "price_move", "policy", "investment", "geopolitics", "other"],
            },
            "region": {"type": "string", "enum": ["global", "asia", "europe", "middle_east", "us", "other"]},
            "impact_bullets": {"type": "array", "items": {"type": "string"}},
            "impact_level": {"type": "string", "enum": ["low", "medium", "high", "unknown"]},
            "country": {"type": "string"},
            "projects": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["is_relevant"],
    }

    feed_country = (news.get("feed_country") or "").strip()
    countries_hint = news.get("countries_hint") or []
    allowed = PROJECT_COUNTRIES

    prompt = f"""
คุณเป็นผู้ช่วยคัดกรองข่าวสำหรับ PTTEP

รายการประเทศที่อนุญาต (ALLOWED) = {allowed}
feed_country (โหมดแหล่งข่าว) = {feed_country}
Hints จากข้อความข่าว (ชื่อประเทศที่จับได้) = {countries_hint}

กติกาแบบเข้ม (STRICT):
1) ห้ามประเทศนอกลิสต์:
   - ถ้าข่าวหลักเกี่ยวกับประเทศที่ไม่อยู่ในรายการ → is_relevant = false
2) ต้องเป็น "เหตุการณ์ในประเทศนั้น" จริง ๆ:
   - ถ้าข่าวเป็น global/ตลาดโลก/หลายประเทศ และไม่ใช่เหตุการณ์ที่เกิดในประเทศใดประเทศหนึ่งชัดเจน → is_relevant = false
3) ถ้าโหมด per_country:
   - ถ้าประเทศหลักของข่าวไม่ใช่ "{feed_country}" → is_relevant = false
   - ถ้า is_relevant = true → country ต้องเป็น "{feed_country}" เท่านั้น
4) ถ้าโหมด global:
   - ต้องเลือก country เป็นประเทศหลักเพียง 1 ประเทศใน ALLOWED
   - ถ้าไม่มั่นใจประเทศหลัก → is_relevant = false

ถ้า is_relevant = true ให้เติม:
- country: ชื่อประเทศตามลิสต์ที่อนุญาต
- projects: ถ้ากระทบภาพรวมประเทศ ให้ใส่ ["ALL"]
- impact_bullets: 2–4 bullet ภาษาไทย "ภาษาคน"
  (a) ห้ามประโยคแม่แบบรวม ๆ
  (b) ทุก bullet ต้องมี "กลไก" อย่างน้อย 1 อย่าง (ใบอนุญาต/ภาษี-PSC/ความปลอดภัย/โลจิสติกส์/ผู้รับเหมา/ประกันภัย/FX/ศุลกากร/คว่ำบาตร)
  (c) ถ้าไม่แน่ใจ ให้ใช้คำว่า "คาดว่า/มีโอกาส/เสี่ยงที่" + เหตุผลสั้น ๆ
  (d) ห้ามขึ้นต้นซ้ำรูปแบบทุกบรรทัด
- impact_level: low/medium/high/unknown
- summary: ไทย 2–4 ประโยค (ถ้าไม่มั่นใจให้สั้น ๆ)

อินพุตข่าว:
หัวข้อ: {news.get("title","")}
สรุปจาก RSS: {news.get("summary","")}

ตอบกลับเป็น JSON เท่านั้น ตาม schema นี้:
{json.dumps(schema, ensure_ascii=False)}
"""
    try:
        r = call_gemini(prompt, want_json=True, temperature=0.35)
        raw = (getattr(r, "text", "") or "").strip()
        data = _extract_json_object(raw)
        if not isinstance(data, dict):
            return rule_fallback(news)

        if "projects" in data and not isinstance(data.get("projects"), list):
            data["projects"] = [str(data["projects"])]

        bullets = data.get("impact_bullets")
        if isinstance(bullets, str):
            bullets = _impact_to_bullets(bullets)
        if not isinstance(bullets, list):
            bullets = []
        bullets = [str(x).strip() for x in bullets if str(x).strip()]
        data["impact_bullets"] = _diversify_bullets(bullets[:6])

        if "impact_level" not in data:
            data["impact_level"] = "unknown"

        return data
    except Exception:
        return rule_fallback(news)


# ============================================================================================================
# FETCH NEWS WINDOW (21:00 yesterday -> 06:00 today, Bangkok time)
# ============================================================================================================
def fetch_news_window(start_ts: float):
    now_local = datetime.now(bangkok_tz)
    start = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end = now_local.replace(hour=6, minute=0, second=0, microsecond=0)

    out = []
    for site, feed_country, url in NEWS_FEEDS:
        if _deadline_check(start_ts):
            break

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

                link = _normalize_link(getattr(e, "link", None) or "")
                if not link:
                    continue

                title = (getattr(e, "title", "") or "").strip()
                summary = getattr(e, "summary", "") or ""
                summary = re.sub(r"\s+", " ", re.sub("<.*?>", " ", summary)).strip()

                hints = detect_project_countries(f"{title}\n{summary}")
                out.append({
                    "site": site,
                    "feed_country": feed_country,
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": dt_local,
                    "date": dt_local.strftime("%d/%m/%Y %H:%M"),
                    "countries_hint": hints,
                })
        except Exception as ex:
            print(f"[WARN] feed failed: {site}/{feed_country} -> {type(ex).__name__}: {ex}")
            continue

    uniq, seen = [], set()
    for n in out:
        k = _normalize_link(n.get("link", ""))
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)

    uniq.sort(key=lambda x: x["published"], reverse=True)
    return uniq


# ============================================================================================================
# FLEX MESSAGE
# ============================================================================================================
def create_flex(news_items):
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    for n in news_items:
        bullets = n.get("impact_bullets")
        if not isinstance(bullets, list) or not bullets:
            bullets = _impact_to_bullets(n.get("impact_reason", ""))

        link = n.get("link") or "https://news.google.com/"
        if not (isinstance(link, str) and link.startswith(("http://", "https://"))):
            link = "https://news.google.com/"

        img = n.get("image") or DEFAULT_ICON_URL
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL

        country_txt = (n.get("country") or "ไม่ระบุ").strip()
        projects = n.get("projects") or []
        proj_txt = ", ".join(projects[:3]) if isinstance(projects, list) and projects else "ALL"

        header_box = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": n.get("title", ""), "weight": "bold", "size": "lg", "wrap": True},
                {"type": "text", "text": f"🗓 {n.get('date','')}", "size": "xs", "color": "#888888", "margin": "sm"},
                {"type": "text", "text": f"🌍 {country_txt} | {n.get('site','')}", "size": "xs", "color": "#448AFF", "margin": "xs"},
                {"type": "text", "text": f"โครงการ: {proj_txt} | ประเทศ: {country_txt}", "size": "xs", "color": "#666666", "margin": "sm", "wrap": True},
            ],
        }

        impact_box = {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": (
                [{"type": "text", "text": "ผลกระทบต่อโครงการ", "size": "lg", "weight": "bold", "color": "#000000"}]
                + [{"type": "text", "text": f"• {b}", "wrap": True, "size": "md", "color": "#000000", "weight": "bold", "margin": "xs"} for b in bullets[:6]]
            ),
        }

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "contents": [header_box, impact_box]},
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
# BROADCAST LINE
# ============================================================================================================
def send_to_line(messages):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}

    for i, msg in enumerate(messages, 1):
        payload = {"messages": [msg]}
        print("=== LINE PAYLOAD (truncated) ===")
        print(json.dumps({"messages": [{"type": msg.get("type"), "altText": msg.get("altText")}]} , ensure_ascii=False))

        if DRY_RUN:
            print("[DRY_RUN] ไม่ส่งจริง เพราะ DRY_RUN = true")
            continue

        r = S.post(url, headers=headers, json=payload, timeout=LINE_TIMEOUT_SEC)
        print(f"Send {i}: {r.status_code}")
        if r.status_code >= 300:
            print("Response body:", r.text[:1000])
            break


# ============================================================================================================
# MAIN WORKFLOW
# ============================================================================================================
def main():
    start_ts = time.time()

    print("ดึงข่าว..")
    all_news = fetch_news_window(start_ts)
    print("จำนวนข่าวดิบทั้งหมด:", len(all_news))
    if not all_news:
        print("ไม่พบข่าวในช่วงเวลา")
        return

    sent = load_sent_links()

    per_country_count = {c: 0 for c in PROJECT_COUNTRIES}
    candidates = []
    global_candidates = []

    for n in all_news:
        if _deadline_check(start_ts):
            print("[STOP] ถึง HARD_DEADLINE ระหว่างคัด candidates")
            break

        link_norm = _normalize_link(n.get("link", ""))
        if link_norm and link_norm in sent:
            continue

        feed_country = (n.get("feed_country") or "").strip()

        if feed_country in PROJECT_COUNTRIES:
            if MAX_PER_COUNTRY is not None and per_country_count.get(feed_country, 0) >= MAX_PER_COUNTRY:
                continue
            candidates.append(n)
            per_country_count[feed_country] = per_country_count.get(feed_country, 0) + 1
        else:
            global_candidates.append(n)

    if MAX_GLOBAL_ITEMS is not None:
        global_candidates = global_candidates[:MAX_GLOBAL_ITEMS]

    combined = candidates + global_candidates
    combined.sort(key=lambda x: x["published"], reverse=True)

    selected = combined[:MAX_LLM_ITEMS] if (MAX_LLM_ITEMS is not None) else combined
    print("จำนวนข่าวที่จะส่งเข้า LLM:", len(selected))

    final = []
    for idx, n in enumerate(selected, 1):
        if _deadline_check(start_ts):
            print(f"[STOP] ถึง HARD_DEADLINE (ได้ {len(final)} ข่าวแล้ว)")
            break

        print(f"[{idx}/{len(selected)}] LLM: {n.get('title','')[:80]}")
        tag = gemini_tag_and_filter(n)

        if not tag.get("is_relevant"):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        country_llm = (tag.get("country") or "").strip()
        if country_llm not in PROJECT_COUNTRIES:
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        feed_country = (n.get("feed_country") or "").strip()
        if feed_country in PROJECT_COUNTRIES:
            if country_llm != feed_country:
                time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
                continue
        else:
            hints = n.get("countries_hint") or []
            if country_llm not in hints:
                time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
                continue

        bullets = tag.get("impact_bullets") or []
        if not isinstance(bullets, list):
            bullets = _impact_to_bullets(str(bullets))

        if ENABLE_IMPACT_REWRITE and looks_generic_bullets(bullets):
            bullets = rewrite_impact_bullets(n, country_llm, tag.get("projects") or ["ALL"], bullets)

        if not has_meaningful_impact(bullets):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        n["country"] = country_llm
        n["projects"] = tag.get("projects") or ["ALL"]
        n["topic_type"] = tag.get("topic_type", "other")
        n["region"] = tag.get("region", "other")
        n["impact_level"] = tag.get("impact_level", "unknown")
        n["impact_bullets"] = bullets[:6]

        final.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    print("จำนวนข่าวผ่านเงื่อนไข:", len(final))
    if not final:
        print("ไม่มีข่าวที่ผ่านเงื่อนไขวันนี้")
        return

    for n in final:
        if _deadline_check(start_ts):
            print("[STOP] ถึง HARD_DEADLINE ระหว่างหา image")
            break
        img = fetch_article_image(n.get("link", ""))
        n["image"] = img if (isinstance(img, str) and img.startswith(("http://", "https://"))) else DEFAULT_ICON_URL
        time.sleep(0.15)

    msgs = create_flex(final[:10])  # LINE carousel ไม่ควรเยอะเกิน
    send_to_line(msgs)

    save_sent_links([n.get("link", "") for n in final])
    print("เสร็จสิ้น (Gemini calls:", GEMINI_CALLS, ")")

if __name__ == "__main__":
    main()
