# ============================================================================================================
# PTTEP Domestic-by-Project-Countries News Bot (FULL FIXED)
# - กรองข่าวไม่เกี่ยวด้วย Topic Gate ก่อนเข้า LLM (ลดมั่ว + เร็วขึ้น)
# - ใส่ "โครงการในประเทศนี้" (mapping) + ให้ LLM เลือก "โครงการที่เกี่ยวข้อง" จากลิสต์ประเทศนั้น
# - ผลกระทบเป็นภาษาคน 2–4 bullets + rewrite เฉพาะข่าวที่ generic
# - กันค้าง: RSS timeout, deadline, limit เข้า LLM, ปรับ sleep ด้วย env
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
# ENV
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

def _as_limit(env_name: str, default: str = "0"):
    """0 หรือ <=0 = ไม่จำกัด"""
    try:
        v = int(os.getenv(env_name, default))
        return None if v <= 0 else v
    except Exception:
        return None

MAX_PER_COUNTRY = _as_limit("MAX_PER_COUNTRY", "0")
MAX_GLOBAL_ITEMS = _as_limit("MAX_GLOBAL_ITEMS", "0")
MAX_LLM_ITEMS = _as_limit("MAX_LLM_ITEMS", "0")

SLEEP_MIN = float(os.getenv("SLEEP_MIN", "0.4" if os.getenv("GITHUB_ACTIONS") else "0.8"))
SLEEP_MAX = float(os.getenv("SLEEP_MAX", "0.9" if os.getenv("GITHUB_ACTIONS") else "1.6"))
SLEEP_BETWEEN_CALLS = (max(0.0, SLEEP_MIN), max(SLEEP_MIN, SLEEP_MAX))

RUN_DEADLINE_MIN = int(os.getenv("RUN_DEADLINE_MIN", "0"))  # 0 = ปิด
RSS_TIMEOUT_SEC = int(os.getenv("RSS_TIMEOUT_SEC", "15"))
ARTICLE_TIMEOUT_SEC = int(os.getenv("ARTICLE_TIMEOUT_SEC", "12"))

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

GEMINI_CALLS = 0


# ============================================================================================================
# ประเทศ + query
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
# ✅ โครงการตามประเทศ (คุณแก้ชื่อ/เพิ่มได้ตามจริง)
# ============================================================================================================
PROJECTS_BY_COUNTRY = {
    "Thailand": [
        "G1/61 (Erawan)", "G2/61 (Bongkot)", "Arthit", "S1", "Contract 4", "B8/32", "9A", "Sinphuhorm", "MTJDA A-18"
    ],
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
# Topic Gate (กันข่าวไม่เกี่ยว)
# ============================================================================================================
TOPIC_KEYWORDS = [
    # energy / upstream
    "oil","gas","lng","crude","petroleum","upstream","offshore","rig","drilling","pipeline",
    "refinery","psc","concession","opec","energy","power","electricity","renewable",
    # policy / economy
    "tax","royalty","permit","license","sanction","tariff","regulation","policy","election",
    "inflation","currency","fx","bond","interest rate","central bank",
    # security / logistics
    "border","conflict","protest","strike","security","attack","port","shipping","customs","logistics",
    # thai
    "น้ำมัน","ก๊าซ","ปิโตรเลียม","สำรวจ","ผลิต","แท่นขุด","ท่อส่ง","สัมปทาน","psc",
    "ภาษี","ใบอนุญาต","กฎระเบียบ","คว่ำบาตร","การเลือกตั้ง","ค่าเงิน","ดอกเบี้ย",
    "ชายแดน","ความไม่สงบ","ประท้วง","นัดหยุดงาน","ท่าเรือ","ขนส่ง","ศุลกากร","โลจิสติกส์",
]

def passes_topic_gate(title: str, summary: str) -> bool:
    t = f"{title or ''} {summary or ''}".lower()
    return any(k in t for k in TOPIC_KEYWORDS)


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
            if lk.startswith("utm_") or lk in ["fbclid", "gclid", "mc_cid", "mc_eid"]:
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
        try:
            return json.loads(candidate)
        except Exception:
            return None
    return None

def has_meaningful_impact(bullets) -> bool:
    if not bullets or not isinstance(bullets, list):
        return False
    txt = " ".join([str(x) for x in bullets if str(x).strip()])
    bad = ["ยังไม่พบผลกระทบ", "ไม่พบผลกระทบ", "ไม่ระบุผลกระทบ", "ไม่เกี่ยวข้อง", "ข้อมูลไม่เพียงพอ"]
    t = txt.lower().replace(" ", "")
    if any(x.replace(" ", "") in t for x in bad):
        return False
    return len(txt.strip()) >= 25

def parse_feed_with_timeout(url: str):
    r = S.get(url, timeout=RSS_TIMEOUT_SEC, allow_redirects=True)
    r.raise_for_status()
    return feedparser.parse(r.text)

def fetch_article_image(url: str):
    try:
        if not url or not url.startswith(("http://", "https://")):
            return None
        r = S.get(url, timeout=ARTICLE_TIMEOUT_SEC)
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

    generic_hit = any(
        p.replace(" ", "") in joined.replace(" ", "")
        for p in GENERIC_PATTERNS
    )

    specific_hit = any(
        k.lower() in joined
        for k in SPECIFIC_HINTS
    )

    return generic_hit and (not specific_hit)

def _diversify_bullets(bullets):
    if not bullets:
        return bullets
    starters = [re.sub(r"\s+", "", (b or "")[:10]) for b in bullets]
    if len(set(starters)) == 1 and len(bullets) >= 2:
        variants = ["เสี่ยงที่", "คาดว่า", "มีโอกาส", "อาจทำให้", "อาจต้อง"]
        new = []
        for i, b in enumerate(bullets):
            bb = (b or "").strip()
            bb = re.sub(r"^(คาดว่า|มีโอกาส|อาจทำให้|เสี่ยงที่|อาจต้อง)\s*", "", bb)
            new.append(f"{variants[i % len(variants)]} {bb}".strip())
        return new
    return bullets

def rewrite_impact_bullets(news, country, projects, bullets):
    prompt = f"""
คุณคือ Analyst ของ PTTEP
ช่วย "เขียนใหม่" bullet ผลกระทบให้เป็นภาษาไทยแบบภาษาคนและเฉพาะเจาะจงขึ้น (2–4 bullets)

ข้อห้าม:
- ห้ามใช้ประโยคแม่แบบกว้าง ๆ เช่น "อาจกระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง" แบบรวม ๆ
- ห้ามขึ้นต้นซ้ำโครงเดิมทุกบรรทัด

สิ่งที่ต้องมี:
- ทุก bullet ต้องมี "กลไก" อย่างน้อย 1 อย่าง:
  ใบอนุญาต / ภาษี-PSC / ความปลอดภัย / โลจิสติกส์-ขนส่ง / แรงงาน-ผู้รับเหมา / ประกันภัย / การเงิน-FX / ศุลกากร / คว่ำบาตร
- ถ้าไม่แน่ใจ ใช้ "คาดว่า/มีโอกาส/เสี่ยงที่" + เหตุผลสั้น ๆ 1 วลี
- แต่ละ bullet 1 ประโยค ไม่เกิน ~24 คำ

ประเทศ: {country}
โครงการที่เกี่ยวข้อง(เดิม): {", ".join(projects) if projects else "ALL"}
หัวข้อ: {news.get("title","")}
สรุป RSS: {news.get("summary","")}

bullet เดิม:
{json.dumps(bullets, ensure_ascii=False)}

ตอบกลับเป็น JSON เท่านั้น:
{{"impact_bullets": ["...","..."]}}
"""
    r = call_gemini(prompt, want_json=True, temperature=0.75)
    data = _extract_json_object((getattr(r, "text", "") or "").strip())
    if isinstance(data, dict) and isinstance(data.get("impact_bullets"), list):
        out = [str(x).strip() for x in data["impact_bullets"] if str(x).strip()]
        return _diversify_bullets(out[:6])
    return _diversify_bullets(bullets)

FALLBACK_IMPACTS = [
    "เสี่ยงที่ขั้นตอนอนุมัติ/ใบอนุญาตจะช้าลง หากหน่วยงานรัฐออกมาตรการเพิ่ม",
    "อาจต้องเพิ่มงบความปลอดภัย/ประกันภัยของทีมงานและผู้รับเหมา หากสถานการณ์ตึงตัว",
    "โลจิสติกส์ (ท่าเรือ/ขนส่ง/ศุลกากร) อาจสะดุดระยะสั้น ทำให้เลื่อนส่งมอบของบางรายการ",
    "มีโอกาสกระทบเงื่อนไขภาษี/PSC/กฎพลังงาน ต้องติดตามประกาศอย่างเป็นทางการ",
    "ค่าเงิน/ต้นทุนการเงินอาจผันผวน ทำให้สัญญาจัดซื้อบางส่วนต้องเผื่อส่วนต่าง",
]

def rule_fallback(news, country):
    bullets = random.sample(FALLBACK_IMPACTS, k=2)
    return {
        "is_relevant": True,
        "country": country,
        "projects": ["ALL"],
        "impact_bullets": _diversify_bullets(bullets),
        "impact_level": "unknown",
    }

def gemini_tag_and_filter(news):
    feed_country = (news.get("feed_country") or "").strip()
    hints = news.get("countries_hint") or []

    # โหมดประเทศ/โหมด global
    per_country_mode = feed_country in PROJECT_COUNTRIES
    global_mode = (feed_country == "GLOBAL")

    # สร้าง “ตารางโครงการ” แบบย่อให้ LLM (กัน token บาน)
    projects_table = {c: projects_for_country(c)[:8] for c in PROJECT_COUNTRIES}

    schema = {
        "type": "object",
        "properties": {
            "is_relevant": {"type": "boolean"},
            "country": {"type": "string"},
            "projects": {"type": "array", "items": {"type": "string"}},
            "impact_bullets": {"type": "array", "items": {"type": "string"}},
            "impact_level": {"type": "string", "enum": ["low", "medium", "high", "unknown"]},
            "why_relevant": {"type": "string"},
        },
        "required": ["is_relevant"],
    }

    prompt = f"""
คุณเป็นผู้ช่วยคัดกรองข่าวสำหรับทีมปฏิบัติการ (PTTEP)

ALLOWED countries = {PROJECT_COUNTRIES}
feed_country = {feed_country}
hints_from_text = {hints}

PROJECTS_BY_COUNTRY (เลือกโครงการได้เฉพาะที่อยู่ในประเทศนั้น):
{json.dumps(projects_table, ensure_ascii=False)}

กติกาเข้ม:
1) รับเฉพาะข่าวที่ “เกี่ยวกับพลังงาน/การเมือง-ความมั่นคง/กฎระเบียบ-ภาษี-PSC/โลจิสติกส์/คว่ำบาตร/ความเสี่ยงที่กระทบการดำเนินงาน”
   ถ้าเป็นข่าวสังคม/ไลฟ์สไตล์/วัฒนธรรม/อาชญากรรมทั่วไป → is_relevant=false
2) ต้องเป็นเหตุการณ์ที่โยงประเทศใน ALLOWED แบบชัดเจน
3) โหมด per_country: ถ้าประเทศหลักไม่ใช่ "{feed_country}" → is_relevant=false และถ้าผ่าน → country ต้องเป็น "{feed_country}"
4) โหมด global: ต้องเลือก country เป็นประเทศหลักเพียง 1 ประเทศใน ALLOWED และควรอยู่ใน hints ด้วย (ถ้าไม่มั่นใจ → false)

ถ้า is_relevant=true ให้ตอบ:
- country: ประเทศหลัก
- projects: เลือก 0–3 โครงการ “จากลิสต์ของประเทศนั้นเท่านั้น”
  ถ้าไม่ชัดเจนว่าเกี่ยวกับโครงการใด ให้ใส่ ["ALL"]
- impact_bullets: 2–4 bullets ภาษาไทยแบบภาษาคน (1 ประโยค/บรรทัด)
  เงื่อนไข:
  (a) ห้ามใช้ประโยคแม่แบบกว้าง ๆ
  (b) ทุก bullet ต้องมี “กลไก” อย่างน้อย 1 อย่าง เช่น ใบอนุญาต/ภาษี-PSC/ความปลอดภัย/โลจิสติกส์/ผู้รับเหมา/ประกันภัย/FX/ศุลกากร/คว่ำบาตร
  (c) ถ้าไม่แน่ใจ ใช้ "คาดว่า/มีโอกาส/เสี่ยงที่" + เหตุผลสั้น ๆ 1 วลี
- impact_level: low/medium/high/unknown
- why_relevant: อธิบายสั้น ๆ ว่าข่าวนี้เกี่ยวอะไรกับการดำเนินงาน (1–2 ประโยค)

ข้อมูลข่าว:
title: {news.get("title","")}
summary: {news.get("summary","")}

ตอบกลับเป็น JSON เท่านั้น ตาม schema:
{json.dumps(schema, ensure_ascii=False)}
"""

    try:
        r = call_gemini(prompt, want_json=True, temperature=0.35)
        data = _extract_json_object((getattr(r, "text", "") or "").strip())
        if not isinstance(data, dict):
            # fallback แบบปลอดภัย: ให้ผ่านเฉพาะ per_country และ country แน่นอน
            if per_country_mode:
                return rule_fallback(news, feed_country)
            return {"is_relevant": False}

        # normalize
        country = (data.get("country") or "").strip()
        if not data.get("is_relevant"):
            return {"is_relevant": False}

        if country not in PROJECT_COUNTRIES:
            return {"is_relevant": False}

        if per_country_mode and country != feed_country:
            return {"is_relevant": False}

        if global_mode and hints and (country not in hints):
            return {"is_relevant": False}

        # projects filter: ห้ามนอกลิสต์
        allowed_projects = set(projects_for_country(country))
        projects = data.get("projects") or ["ALL"]
        if not isinstance(projects, list):
            projects = [str(projects)]

        projects = [str(x).strip() for x in projects if str(x).strip()]
        if not projects:
            projects = ["ALL"]

        if projects != ["ALL"]:
            projects = [p for p in projects if p in allowed_projects]
            if not projects:
                projects = ["ALL"]

        bullets = data.get("impact_bullets") or []
        if not isinstance(bullets, list):
            bullets = [str(bullets)]
        bullets = [str(x).strip() for x in bullets if str(x).strip()]
        bullets = _diversify_bullets(bullets[:6])

        return {
            "is_relevant": True,
            "country": country,
            "projects": projects[:3] if projects != ["ALL"] else ["ALL"],
            "impact_bullets": bullets[:6],
            "impact_level": data.get("impact_level", "unknown"),
            "why_relevant": (data.get("why_relevant") or "").strip(),
        }
    except Exception:
        if per_country_mode:
            return rule_fallback(news, feed_country)
        return {"is_relevant": False}


# ============================================================================================================
# FETCH NEWS WINDOW (21:00 yesterday -> 06:00 today, Bangkok)
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

    # dedupe
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
def _shorten_projects_list(items, take=4):
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

        link = n.get("link") or "https://news.google.com/"
        img = n.get("image") or DEFAULT_ICON_URL

        country_txt = (n.get("country") or "ไม่ระบุ").strip()

        related_projects = n.get("projects") or ["ALL"]
        rp_show = _shorten_projects_list(related_projects, take=3)

        country_projects = n.get("country_projects") or []
        cp_show = _shorten_projects_list(country_projects, take=4)

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
                            {"type": "text", "text": f"{country_txt} | {n.get('site','')}", "size": "sm", "color": "#1E90FF", "wrap": True},
                        ],
                    },

                    {"type": "text", "text": f"โครงการที่เกี่ยวข้อง: {rp_show}", "size": "sm", "color": "#666666", "wrap": True, "margin": "sm"},
                    {"type": "text", "text": f"โครงการในประเทศนี้: {cp_show}", "size": "sm", "color": "#666666", "wrap": True},

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
# LINE
# ============================================================================================================
def send_to_line(messages):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}

    for i, msg in enumerate(messages, 1):
        payload = {"messages": [msg]}
        print("=== LINE PAYLOAD (meta) ===")
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
    candidates = []
    global_candidates = []

    for n in all_news:
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่างคัดข่าว (หยุด)")
            break

        link_norm = _normalize_link(n["link"])
        if link_norm in sent:
            continue

        # ✅ กรอง topic ก่อนเข้า LLM (กันข่าวมั่วแบบในรูป)
        if not passes_topic_gate(n.get("title",""), n.get("summary","")):
            continue

        feed_country = (n.get("feed_country") or "").strip()
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

    final = []
    for idx, n in enumerate(selected, 1):
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่าง LLM loop (หยุด)")
            break

        print(f"[{idx}/{len(selected)}] LLM: {n.get('title','')[:90]}")

        tag = gemini_tag_and_filter(n)
        if not tag.get("is_relevant"):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        country_llm = tag.get("country")
        bullets = tag.get("impact_bullets") or []
        projects = tag.get("projects") or ["ALL"]

        # rewrite เฉพาะข่าวที่ generic
        if ENABLE_IMPACT_REWRITE and looks_generic_bullets(bullets):
            bullets = rewrite_impact_bullets(n, country_llm, projects, bullets)

        bullets = _diversify_bullets(bullets)

        if not has_meaningful_impact(bullets):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        n["country"] = country_llm
        n["projects"] = projects
        n["impact_bullets"] = bullets[:6]
        n["impact_level"] = tag.get("impact_level", "unknown")
        n["why_relevant"] = tag.get("why_relevant", "")

        # ✅ ใส่โครงการทั้งหมดของประเทศนั้น ๆ ให้โชว์ใน card
        n["country_projects"] = projects_for_country(country_llm)

        final.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    print("จำนวนข่าวผ่านเงื่อนไข:", len(final))
    if not final:
        print("ไม่มีข่าวที่ผ่านเงื่อนไขวันนี้")
        return

    # ใส่รูป (มี timeout แล้ว)
    for n in final:
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่างหา image (หยุด)")
            break
        img = fetch_article_image(n.get("link", ""))
        n["image"] = img if (isinstance(img, str) and img.startswith(("http://", "https://"))) else DEFAULT_ICON_URL
        time.sleep(0.12)

    msgs = create_flex(final[:10])  # กัน carousel ยาวเกิน
    send_to_line(msgs)

    save_sent_links([n["link"] for n in final])
    print("เสร็จสิ้น (Gemini calls:", GEMINI_CALLS, ")")


if __name__ == "__main__":
    main()
