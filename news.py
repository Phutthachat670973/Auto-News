# ============================================================================================================
# PTTEP Domestic-by-Project-Countries News Bot (WITH Legacy Sources)
# - คัดข่าว: ต้องเป็น “เหตุการณ์ในประเทศ” ที่อยู่ใน PROJECT_COUNTRIES เท่านั้น
# - ปรับ “ผลกระทบต่อโครงการ” ให้เป็นภาษาคน + หลากหลาย (2–4 bullets) และมี rewrite ถ้าจืดเกินไป
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

# ค่าเริ่มต้นเป็น “ไม่จำกัด” (0 = ไม่จำกัด)
def _as_limit(env_name: str, default: str = "0"):
    try:
        v = int(os.getenv(env_name, default))
        return None if v <= 0 else v
    except Exception:
        return None

MAX_PER_COUNTRY = _as_limit("MAX_PER_COUNTRY", "0")      # จำกัดข่าวต่อประเทศ (0 = ไม่จำกัด)
MAX_GLOBAL_ITEMS = _as_limit("MAX_GLOBAL_ITEMS", "0")    # จำกัดข่าวจาก legacy feeds (0 = ไม่จำกัด)
MAX_LLM_ITEMS = _as_limit("MAX_LLM_ITEMS", "0")          # จำกัดจำนวนข่าวที่ส่งเข้า LLM (0 = ไม่จำกัด)

SLEEP_BETWEEN_CALLS = (6.0, 7.0)
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ["1", "true", "yes", "y"]
ENABLE_IMPACT_REWRITE = os.getenv("ENABLE_IMPACT_REWRITE", "true").strip().lower() in ["1", "true", "yes", "y"]

DEFAULT_ICON_URL = os.getenv(
    "DEFAULT_ICON_URL",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/News_icon.png/640px-News_icon.png"
)

bangkok_tz = pytz.timezone("Asia/Bangkok")
S = requests.Session()

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

# synonyms ใช้ช่วย “จับประเทศ” จากข้อความสำหรับโหมด GLOBAL feeds
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
        # ลบ tracking params ทั่วไป
        for k in list(q.keys()):
            lk = k.lower()
            if lk.startswith("utm_") or lk in ["fbclid", "gclid", "mc_cid", "mc_eid"]:
                q.pop(k, None)
        query = urlencode(sorted(q.items()))
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url or ""


def get_sent_links_file():
    # แยกไฟล์ตามวัน เพื่อกันซ้ำ “รายวัน”
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
    t = impact_text.strip()
    # ตัดสัญลักษณ์ • และแบ่งบรรทัด
    t = t.replace("\r\n", "\n")
    parts = [p.strip() for p in re.split(r"\n+|•", t) if p.strip()]
    # เอา bullet ที่สั้น/ไร้สาระออก
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


def fetch_article_image(url: str):
    try:
        if not url or not url.startswith(("http://", "https://")):
            return None
        r = S.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
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
            if any(x in str(e).lower() for x in ["429", "unavailable", "deadline", "503", "500"]) and i < MAX_RETRIES:
                time.sleep(5 * i)
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


def rewrite_impact_bullets(news, country, projects, bullets):
    prompt = f"""
คุณคือ Analyst ของ PTTEP
ช่วย "เขียนใหม่" bullet ผลกระทบให้เป็นภาษาคนและเฉพาะเจาะจงขึ้น (2–4 bullets)

ข้อห้าม:
- ห้ามใช้สำนวนแม่แบบกว้าง ๆ เช่น "อาจกระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง" แบบรวม ๆ
- ทุก bullet ต้องมี "กลไก" อย่างน้อย 1 อย่าง:
  ใบอนุญาต / ภาษี-PSC / ความปลอดภัย / โลจิสติกส์-ขนส่ง / แรงงาน-ผู้รับเหมา / ประกันภัย / การเงิน-FX / ศุลกากร / คว่ำบาตร
- ถ้าไม่แน่ใจ ให้ใช้คำว่า "คาดว่า" หรือ "มีโอกาส" + เหตุผลสั้น ๆ 1 วลี
- แต่ละ bullet 1 ประโยค ไม่เกิน ~24 คำ

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
        return out[:6]
    return bullets


FALLBACK_IMPACTS = [
    "คาดว่าเรื่องนี้ทำให้ขั้นตอนอนุมัติ/ใบอนุญาตของหน่วยงานรัฐช้าลง ถ้ามีการออกมาตรการใหม่",
    "มีโอกาสเพิ่มค่าใช้จ่ายด้านประกันภัย/ความปลอดภัยของทีมงานและผู้รับเหมา หากสถานการณ์ตึงตัวขึ้น",
    "คาดว่าโลจิสติกส์ (ท่าเรือ/ขนส่ง/ศุลกากร) อาจติดขัดช่วงสั้น ๆ ถ้าเกิดความไม่แน่นอนในประเทศ",
    "มีโอกาสกระทบเงื่อนไขภาษี/PSC/กฎระเบียบพลังงาน ต้องติดตามประกาศอย่างเป็นทางการ",
    "คาดว่าอัตราแลกเปลี่ยน/ต้นทุนการเงินอาจผันผวน ทำให้แผนจัดซื้อและสัญญาบางส่วนต้องเผื่อส่วนต่าง",
]


def rule_fallback(news):
    """
    ใช้เมื่อ LLM parse ไม่ได้/ล้มเหลว: ให้ผลกระทบ “ไม่ซ้ำรูปแบบ” และอ่านเป็นภาษาคนขึ้น
    """
    feed_country = (news.get("feed_country") or "").strip()

    # GLOBAL feeds ต้องมี hints ชัด ๆ อย่างน้อย 1 ประเทศ
    if feed_country == "GLOBAL":
        hints = news.get("countries_hint") or []
        if len(hints) != 1:
            return {"is_relevant": False}
        c = hints[0]
    else:
        if feed_country not in PROJECT_COUNTRIES:
            return {"is_relevant": False}
        c = feed_country

    bullets = [random.choice(FALLBACK_IMPACTS), random.choice(FALLBACK_IMPACTS)]
    bullets = list(dict.fromkeys(bullets))  # unique

    return {
        "is_relevant": True,
        "summary": "",
        "topic_type": "other",
        "region": "other",
        "impact_bullets": bullets[:4],
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
            "region": {
                "type": "string",
                "enum": ["global", "asia", "europe", "middle_east", "us", "other"],
            },
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

    per_country_mode = feed_country in PROJECT_COUNTRIES
    global_mode = (feed_country == "GLOBAL")

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
  กติกาสไตล์:
  (a) ห้ามใช้ประโยคแม่แบบกว้าง ๆ เช่น "อาจกระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง" แบบรวม ๆ
  (b) ทุก bullet ต้องมี "กลไก" อย่างน้อย 1 อย่าง เช่น:
      ใบอนุญาต/ภาษี-PSC/ความปลอดภัย/โลจิสติกส์-ขนส่ง/แรงงาน-ผู้รับเหมา/ประกันภัย/การเงิน-FX/ศุลกากร/คว่ำบาตร
  (c) ถ้าไม่แน่ใจ ให้ใช้คำว่า "คาดว่า" หรือ "มีโอกาส" + เหตุผลสั้น ๆ 1 วลี
  (d) แต่ละ bullet 1 ประโยค ไม่เกิน ~24 คำ
- impact_level: low/medium/high/unknown
- summary: ไทย 2–4 ประโยค (ถ้าไม่มั่นใจให้สั้น ๆ)

อินพุตข่าว:
หัวข้อ: {news.get("title","")}
สรุปจาก RSS: {news.get("summary","")}
ข้อมูลเพิ่มเติม: {news.get("detail","")}

ตอบกลับเป็น JSON เท่านั้น ตาม schema นี้:
{json.dumps(schema, ensure_ascii=False)}
"""

    try:
        r = call_gemini(prompt, want_json=True, temperature=0.35)
        raw = (getattr(r, "text", "") or "").strip()
        data = _extract_json_object(raw)
        if not isinstance(data, dict):
            return rule_fallback(news)

        # normalize list fields
        if "projects" in data and not isinstance(data.get("projects"), list):
            data["projects"] = [str(data["projects"])]

        bullets = data.get("impact_bullets")
        if isinstance(bullets, str):
            bullets = _impact_to_bullets(bullets)
        if not isinstance(bullets, list):
            bullets = []

        bullets = [str(x).strip() for x in bullets if str(x).strip()]
        data["impact_bullets"] = bullets[:6]

        if "impact_level" not in data:
            data["impact_level"] = "unknown"

        return data
    except Exception:
        return rule_fallback(news)


# ============================================================================================================
# FETCH NEWS WINDOW (21:00 yesterday -> 06:00 today, Bangkok time)
# ============================================================================================================
def fetch_news_window():
    now_local = datetime.now(bangkok_tz)
    start = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end = now_local.replace(hour=6, minute=0, second=0, microsecond=0)

    out = []
    for site, feed_country, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                pub = getattr(e, "published", None) or getattr(e, "updated", None)
                if not pub:
                    continue

                dt = dateutil_parser.parse(pub)
                if dt.tzinfo is None:
                    dt = bangkok_tz.localize(dt)
                dt_local = dt.astimezone(bangkok_tz)

                # เฉพาะช่วงเวลา
                if not (start <= dt_local <= end):
                    continue

                link = getattr(e, "link", None) or ""
                link = _normalize_link(link)
                if not link:
                    continue

                title = getattr(e, "title", "") or ""
                summary = getattr(e, "summary", "") or ""

                # hints (ใช้กับ global feeds)
                hints = detect_project_countries(f"{title}\n{summary}")

                out.append({
                    "site": site,
                    "feed_country": feed_country,   # ประเทศของ feed (per-country) หรือ "GLOBAL"
                    "title": title.strip(),
                    "summary": re.sub(r"\s+", " ", re.sub("<.*?>", " ", summary)).strip(),
                    "link": link,
                    "published": dt_local,
                    "countries_hint": hints,
                })
        except Exception:
            continue

    # dedupe ตาม link
    uniq = []
    seen = set()
    for n in out:
        k = _normalize_link(n["link"])
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

        link = n.get("link") or ""
        if not (isinstance(link, str) and link.startswith(("http://", "https://"))):
            link = "https://news.google.com/"

        img = n.get("image") or DEFAULT_ICON_URL
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL

        country_txt = (n.get("country") or "ไม่ระบุ").strip()
        project_txt = ", ".join(n.get("projects") or ["ALL"])

        header_box = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": n.get("title", "")[:120], "wrap": True, "weight": "bold", "size": "lg"},
                {
                    "type": "box",
                    "layout": "baseline",
                    "spacing": "md",
                    "contents": [
                        {"type": "text", "text": n.get("published").strftime("%d/%m/%Y %H:%M"), "size": "sm", "color": "#666666", "flex": 0},
                        {"type": "text", "text": f"{country_txt} | {n.get('site','')}", "size": "sm", "color": "#1E90FF", "wrap": True},
                    ],
                },
                {"type": "text", "text": f"โครงการ: {project_txt} | ประเทศ: {country_txt}", "size": "sm", "color": "#666666", "wrap": True},
            ],
        }

        impact_box = {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": (
                [{"type": "text", "text": "ผลกระทบต่อโครงการ", "size": "lg", "weight": "bold", "color": "#000000"}]
                + [
                    {"type": "text", "text": f"• {b}", "wrap": True, "size": "md", "color": "#000000", "weight": "bold", "margin": "xs"}
                    for b in bullets[:6]
                ]
            ),
        }

        body_contents = [header_box, impact_box]

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "contents": body_contents},
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
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    for i, msg in enumerate(messages, 1):
        payload = {"messages": [msg]}
        print("=== LINE PAYLOAD ===")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if DRY_RUN:
            print("[DRY_RUN] ไม่ส่งจริง เพราะ DRY_RUN = true")
            continue

        r = S.post(url, headers=headers, json=payload, timeout=15)
        print(f"Send {i}: {r.status_code}")
        print("Response body:", r.text)
        if r.status_code >= 300:
            break


# ============================================================================================================
# MAIN WORKFLOW
# ============================================================================================================
def main():
    print("ดึงข่าว...")
    all_news = fetch_news_window()
    print("จำนวนข่าวดิบทั้งหมด:", len(all_news))
    if not all_news:
        print("ไม่พบข่าวในช่วงเวลา")
        return

    sent = load_sent_links()

    # 1) เลือก candidates จาก GoogleNews per-country (ถ้า MAX_PER_COUNTRY=None = ไม่จำกัด)
    per_country_count = {c: 0 for c in PROJECT_COUNTRIES}
    candidates = []
    global_candidates = []

    for n in all_news:
        link_norm = _normalize_link(n["link"])
        if link_norm in sent:
            continue

        feed_country = (n.get("feed_country") or "").strip()
        if feed_country in PROJECT_COUNTRIES:
            if MAX_PER_COUNTRY is not None and per_country_count[feed_country] >= MAX_PER_COUNTRY:
                continue
            per_country_count[feed_country] += 1
            candidates.append(n)
        else:
            global_candidates.append(n)

    # 2) คุม global_candidates (ถ้า MAX_GLOBAL_ITEMS=None = ไม่จำกัด)
    if MAX_GLOBAL_ITEMS is not None:
        global_candidates = global_candidates[:MAX_GLOBAL_ITEMS]

    # รวมเป็นรายการสุดท้ายก่อนเข้า LLM
    selected = candidates + global_candidates
    selected.sort(key=lambda x: x["published"], reverse=True)

    # 3) คุมจำนวนข่าวเข้า LLM (ถ้า MAX_LLM_ITEMS=None = ไม่จำกัด)
    if MAX_LLM_ITEMS is not None:
        selected = selected[:MAX_LLM_ITEMS]

    print("จำนวนข่าวที่จะส่งเข้า LLM:", len(selected))

    # 4) LLM tag + filter + rewrite impact ถ้าจืด
    final = []
    for idx, n in enumerate(selected, 1):
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
            # per-country mode ต้องตรง feed_country
            if country_llm != feed_country:
                time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
                continue
        else:
            # global mode: กัน LLM เดา
            hints = n.get("countries_hint") or []
            if country_llm not in hints:
                time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
                continue

        bullets = tag.get("impact_bullets") or []
        if not isinstance(bullets, list):
            bullets = _impact_to_bullets(str(bullets))

        # rewrite เฉพาะข่าวที่ generic
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

    # 5) ใส่รูป (ถ้าไม่เจอ ใช้ DEFAULT_ICON_URL)
    for n in final:
        img = fetch_article_image(n.get("link", ""))
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL
        n["image"] = img
        time.sleep(0.2)

    msgs = create_flex(final)
    send_to_line(msgs)

    save_sent_links([n["link"] for n in final])
    print("เสร็จสิ้น")


if __name__ == "__main__":
    main()
