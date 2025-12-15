# ============================================================================================================
# PTTEP Domestic-by-Project-Countries News Bot (WITH Legacy Sources)
# - ไม่ใช้ topic keyword filter (energy/econ/politics) เป็นเงื่อนไขหลัก
# - ส่งเฉพาะข่าวที่ "อยู่ในประเทศ" ของประเทศที่มีโครงการเท่านั้น
# - รวมแหล่งข่าว: Google News RSS (แยกประเทศ) + เว็บเดิม (global feeds)
# ============================================================================================================

import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, quote_plus

import feedparser
from dateutil import parser as dateutil_parser
import pytz
import requests
import google.generativeai as genai

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash"))

GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))
MAX_RETRIES = 6
SLEEP_BETWEEN_CALLS = (0.5, 1.0)

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# จำกัดจำนวนข่าวที่ส่งเข้า LLM ต่อรัน (รวมทุกแหล่ง)
MAX_LLM_ITEMS = int(os.getenv("MAX_LLM_ITEMS", "24"))
# จำกัดจำนวนข่าวต่อประเทศ (เฉพาะ Google News ต่อประเทศ)
MAX_PER_COUNTRY = int(os.getenv("MAX_PER_COUNTRY", "4"))
# จำกัดจำนวนข่าวจากเว็บเดิม (global feeds) เพื่อไม่กินโควต้า
MAX_GLOBAL_ITEMS = int(os.getenv("MAX_GLOBAL_ITEMS", "6"))

bangkok_tz = pytz.timezone("Asia/Bangkok")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ------------------------------------------------------------------------------------------------------------
# Countries with PTTEP projects
# ------------------------------------------------------------------------------------------------------------
PROJECT_COUNTRIES = [
    "Thailand", "Myanmar", "Vietnam", "Malaysia", "Indonesia",
    "UAE", "Oman", "Algeria", "Mozambique", "Australia", "Brazil", "Mexico"
]

PROJECT_COUNTRY_SYNONYMS = {
    "Thailand": ["thailand", "thai", "bangkok", "ประเทศไทย", "ไทย"],
    "Myanmar": ["myanmar", "burma", "เมียนมา", "พม่า"],
    "Vietnam": ["vietnam", "viet nam", "เวียดนาม"],
    "Malaysia": ["malaysia", "malaysian", "มาเลเซีย"],
    "Indonesia": ["indonesia", "indonesian", "อินโดนีเซีย"],
    "UAE": ["uae", "united arab emirates", "abu dhabi", "dubai", "สหรัฐอาหรับเอมิเรตส์"],
    "Oman": ["oman", "โอมาน"],
    "Algeria": ["algeria", "algerian", "แอลจีเรีย"],
    "Mozambique": ["mozambique", "rovuma", "โมซัมบิก"],
    "Australia": ["australia", "australian", "ออสเตรเลีย"],
    "Brazil": ["brazil", "brazilian", "บราซิล"],
    "Mexico": ["mexico", "mexican", "เม็กซิโก"],
}

def detect_project_countries(text: str):
    t = (text or "").lower()
    hits = []
    for c, keys in PROJECT_COUNTRY_SYNONYMS.items():
        if any(k in t for k in keys):
            hits.append(c)
    return sorted(set(hits))

# ------------------------------------------------------------------------------------------------------------
# Google News RSS per country (domestic-ish)
# ------------------------------------------------------------------------------------------------------------
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

def google_news_rss(q: str, hl="en", gl="US", ceid="US:en"):
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"

# ------------------------------------------------------------------------------------------------------------
# Legacy sources (global feeds)
# NOTE: ข่าวจากพวกนี้จะ "ผ่าน" ได้เฉพาะถ้าเกี่ยวกับประเทศโครงการ + เป็นเหตุการณ์ในประเทศนั้นจริง ๆ
# ------------------------------------------------------------------------------------------------------------
LEGACY_FEEDS = [
    ("Oilprice", "GLOBAL", "https://oilprice.com/rss/main"),
    ("CleanTechnica", "GLOBAL", "https://cleantechnica.com/feed/"),
    ("HydrogenFuelNews", "GLOBAL", "https://www.hydrogenfuelnews.com/feed/"),
    ("Economist", "GLOBAL", "https://www.economist.com/latest/rss.xml"),
    ("YahooFinance", "GLOBAL", "https://finance.yahoo.com/news/rssindex"),
]

# รวมทั้งหมดเป็น NEWS_FEEDS
NEWS_FEEDS = []
for c in PROJECT_COUNTRIES:
    NEWS_FEEDS.append(("GoogleNews", c, google_news_rss(COUNTRY_QUERY[c])))
NEWS_FEEDS.extend(LEGACY_FEEDS)

# ============================================================================================================
# HELPERS
# ============================================================================================================
def _normalize_link(url: str) -> str:
    try:
        p = urlparse(url)
        netloc = p.netloc.lower()
        scheme = (p.scheme or "https").lower()

        drop = {"fbclid", "gclid", "ref", "mc_cid", "mc_eid"}
        new_q = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not (k.startswith("utm_") or k in drop)
        ]
        return urlunparse(p._replace(scheme=scheme, netloc=netloc, query=urlencode(new_q)))
    except Exception:
        return (url or "").strip()

def get_sent_links_file(date=None):
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")

def load_sent_links():
    sent = set()
    today_str = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    p = get_sent_links_file(today_str)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                u = _normalize_link(line.strip())
                if u:
                    sent.add(u)
    return sent

def save_sent_links(links):
    p = get_sent_links_file()
    with open(p, "a", encoding="utf-8") as f:
        for l in links:
            f.write(_normalize_link(l) + "\n")

def _impact_to_bullets(text: str):
    if not text:
        return ["ไม่ระบุผลกระทบต่อโครงการ"]
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        lines = [text.strip()]
    bullets = []
    for line in lines:
        s = line.strip()
        s = re.sub(r"^[\u2022\*\-\u00b7·•\s]+", "", s)
        s = re.sub(r"^\d+[\.\)]\s*", "", s)
        if s.startswith(".*"):
            s = s[2:].lstrip()
        if s.startswith("*"):
            s = s[1:].lstrip()
        if s:
            bullets.append(s)
    return bullets or ["ไม่ระบุผลกระทบต่อโครงการ"]

def has_meaningful_impact(impact_text: str) -> bool:
    if not impact_text:
        return False
    t = impact_text.lower().replace(" ", "")
    bad = ["ยังไม่พบผลกระทบ", "ไม่พบผลกระทบ", "ไม่ระบุผลกระทบ", "ไม่เกี่ยวข้อง", "ข้อมูลไม่เพียงพอ"]
    if any(x.replace(" ", "") in t for x in bad):
        return False
    return len(impact_text.strip()) >= 20

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

# ============================================================================================================
# Fetch hero image
# ============================================================================================================
def fetch_article_image(url: str) -> str:
    if not url:
        return ""
    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return ""
        html = r.text
        m = re.search(r'<meta[^>]+property=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
        if m:
            return m.group(1)
        m = re.search(r'<meta[^>]+name=[\'"]twitter:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
        if m:
            return m.group(1)
        m = re.search(r'<img[^>]+src=[\'"]([^\'"]+)[\'"]', html, re.I)
        if m:
            src = m.group(1)
            if src.startswith("//"):
                parsed = urlparse(url)
                return f"{parsed.scheme}:{src}"
            if src.startswith("/"):
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}{src}"
            return src
        return ""
    except Exception:
        return ""

# ============================================================================================================
# CONTEXT
# ============================================================================================================
PTTEP_PROJECTS_CONTEXT = r"""
[PTTEP_PROJECTS_CONTEXT]

ประเทศไทย (Thailand)
- G1/61 (Erawan, Platong, Satun, Funan)
- G2/61 (Bongkot และแหล่งใกล้เคียง)
- Arthit, S1, Contract 4, B8/32, 9A, Sinphuhorm, MTJDA Block A-18

เมียนมา (Myanmar) – Zawtika, Yadana, Yetagun
เวียดนาม (Vietnam) – Block B & 48/95, Block 52/97, 16-1 (Te Giac Trang)
มาเลเซีย (Malaysia) – MTJDA Block A-18, SK309, SK311, SK410B ฯลฯ
อินโดนีเซีย (Indonesia) – South Sageri, South Mandar, Malunda ฯลฯ
UAE – Ghasha Concession, Abu Dhabi Offshore
Oman – Oman Block 12
Algeria – Bir Seba, Hirad, Touat ฯลฯ
Mozambique – Mozambique Area 1 (Rovuma LNG)
Australia – Montara และโครงการอื่น ๆ ใน Timor Sea / Browse Basin
Brazil – BM-ES-23, BM-ES-24 ฯลฯ
Mexico – Mexico Block 12 (2.4) และบล็อกอื่น ๆ
"""

PARTNERS_CONTEXT = r"""
[พันธมิตร / ผู้ร่วมทุนที่พบบ่อย]
- Chevron, ExxonMobil, TotalEnergies, Shell, BP, ENI, Sonatrach, Petrobras,
  ADNOC, Petronas และบริษัทพลังงานแห่งชาติอื่น ๆ
"""

# ============================================================================================================
# GEMINI CALL WRAPPER
# ============================================================================================================
GEMINI_CALLS = 0

def call_gemini(prompt: str, want_json: bool = False):
    global GEMINI_CALLS
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError("เกินโควต้า Gemini ประจำวัน")

    last_error = None
    for i in range(1, MAX_RETRIES + 1):
        try:
            gen_cfg = {"temperature": 0.2, "max_output_tokens": 900}
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
            if any(x in str(e) for x in ["429", "unavailable", "deadline", "503", "500"]) and i < MAX_RETRIES:
                time.sleep(5 * i)
                continue
            raise e
    raise last_error

def rule_fallback(news):
    feed_country = (news.get("feed_country") or "").strip()
    # สำหรับ legacy/global: จะไม่มี feed_country จริง ๆ -> ต้องให้มี countries_hint ชัด ๆ อย่างน้อย 1
    if feed_country == "GLOBAL":
        hints = news.get("countries_hint") or []
        if len(hints) != 1:
            return {"is_relevant": False}
        return {
            "is_relevant": True,
            "summary": "",
            "topic_type": "other",
            "region": "other",
            "impact_reason": "• เป็นเหตุการณ์ที่อาจกระทบสภาพแวดล้อมทางนโยบาย/เศรษฐกิจ/พลังงานภายในประเทศ ซึ่งอาจกระทบต้นทุน/ตารางงาน/ความต่อเนื่องของโครงการในประเทศนั้น",
            "country": hints[0],
            "projects": ["ALL"],
        }

    # สำหรับ GoogleNews per-country: feed_country เป็นประเทศนั้น
    if feed_country not in PROJECT_COUNTRIES:
        return {"is_relevant": False}
    return {
        "is_relevant": True,
        "summary": "",
        "topic_type": "other",
        "region": "other",
        "impact_reason": "• เป็นเหตุการณ์ภายในประเทศที่อาจกระทบต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยงต่อการดำเนินงานของโครงการในประเทศนี้",
        "country": feed_country,
        "projects": ["ALL"],
    }

# ============================================================================================================
# GEMINI TAG + FILTER
# ============================================================================================================
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
            "impact_reason": {"type": "string"},
            "country": {"type": "string"},
            "projects": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["is_relevant"],
    }

    feed_country = (news.get("feed_country") or "").strip()
    countries_hint = news.get("countries_hint") or []

    # โหมดแหล่งข่าว:
    # - GoogleNews per-country: feed_country เป็นชื่อประเทศ
    # - Legacy feeds: feed_country = "GLOBAL" (ต้องให้ LLM ระบุประเทศจาก allowed list)
    mode = "per_country" if (feed_country in PROJECT_COUNTRIES) else "global"

    prompt = f"""
{PTTEP_PROJECTS_CONTEXT}
{PARTNERS_CONTEXT}

บทบาทของคุณ: Analyst + News Screener ของ PTTEP
โจทย์: ต้องการ "ข่าวภายในประเทศ" เฉพาะประเทศที่มีโครงการ (ไม่ต้องอิง keyword หมวดข่าว)

ประเทศที่อนุญาต (มีโครงการ): {PROJECT_COUNTRIES}

โหมดแหล่งข่าว: {mode}
- ถ้าโหมด per_country: ข่าวนี้มาจาก feed ของประเทศ = {feed_country}
- ถ้าโหมด global: ข่าวนี้มาจากเว็บ global (ต้องระบุประเทศหลักเอง แต่ต้องอยู่ในรายการประเทศที่อนุญาต)

Hints จากข้อความข่าว (ชื่อประเทศที่จับได้):
countries_hint = {countries_hint}

กติกาแบบเข้ม (STRICT):
1) ห้ามประเทศนอกลิสต์:
   - ถ้าข่าวหลักเกี่ยวกับประเทศที่ไม่อยู่ในรายการ → is_relevant = false
2) ต้องเป็น "ภายในประเทศนั้น" จริง ๆ:
   - ถ้าข่าวเป็น global/ข้ามประเทศ/ตลาดโลกอย่างเดียว และไม่ได้เป็นเหตุการณ์ในประเทศใดประเทศหนึ่งชัดเจน → is_relevant = false
3) ถ้าโหมด per_country:
   - ถ้าประเทศหลักของข่าวไม่ใช่ {feed_country} → is_relevant = false
   - ถ้า is_relevant = true → country ต้องเท่ากับ "{feed_country}" เท่านั้น
4) ถ้าโหมด global:
   - ต้องเลือก country = ประเทศหลักเพียง 1 ประเทศในรายการที่อนุญาต
   - ถ้าไม่มั่นใจประเทศหลัก → is_relevant = false

ถ้า is_relevant = true ให้เติม:
- country: ชื่อประเทศตามลิสต์ที่อนุญาต
- projects: โครงการในประเทศนั้นจาก context (ถ้ากระทบภาพรวมประเทศ ให้ใส่ ["ALL"])
- impact_reason: bullet หลายบรรทัด "เฉพาะผลกระทบต่อโครงการ" ให้ชัดเจน (ต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง/ความต่อเนื่อง)
- summary: ไทย 2–4 ประโยค

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุปจาก RSS: {news['summary']}
ข้อมูลเพิ่มเติม: {news.get('detail','')}

ให้ตอบกลับเป็น JSON เท่านั้น ตาม schema นี้:
{json.dumps(schema, ensure_ascii=False)}
"""

    try:
        r = call_gemini(prompt, want_json=True)
        raw = (getattr(r, "text", "") or "").strip()
        data = _extract_json_object(raw)
        if not isinstance(data, dict):
            return rule_fallback(news)

        if "projects" in data and not isinstance(data.get("projects"), list):
            data["projects"] = [str(data["projects"])]

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
                    dt = pytz.UTC.localize(dt)
                dt = dt.astimezone(bangkok_tz)

                if start <= dt <= end:
                    title = getattr(e, "title", "") or ""
                    summary = getattr(e, "summary", "") or ""
                    text = f"{title} {summary}"
                    out.append({
                        "site": site,
                        "feed_country": feed_country,  # ประเทศของ feed หรือ "GLOBAL"
                        "title": title,
                        "summary": summary,
                        "link": getattr(e, "link", "") or "",
                        "published": dt,
                        "date": dt.strftime("%d/%m/%Y %H:%M"),
                        "countries_hint": detect_project_countries(text),
                    })
        except Exception:
            pass

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
        bullets = _impact_to_bullets(n.get("impact_reason", ""))

        link = n.get("link") or ""
        if not (isinstance(link, str) and link.startswith(("http://", "https://"))):
            link = "https://news.google.com/"

        img = n.get("image") or DEFAULT_ICON_URL
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL

        country_txt = (n.get("country") or "ไม่ระบุ").strip()
        projects = n.get("projects") or []
        proj_txt = ", ".join(projects[:3]) if isinstance(projects, list) and projects else "ไม่ระบุ"

        body_contents = [
            {"type": "text", "text": n["title"], "weight": "bold", "size": "lg", "wrap": True},
            {"type": "text", "text": f"🗓 {n['date']}", "size": "xs", "color": "#888888", "margin": "sm"},
            {"type": "text", "text": f"🌍 {country_txt} | {n['site']}", "size": "xs", "color": "#448AFF", "margin": "xs"},
            {"type": "text", "text": f"โครงการ: {proj_txt} | ประเทศ: {country_txt}", "size": "xs", "color": "#555555", "margin": "sm", "wrap": True},
        ]

        impact_box = {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": [{"type": "text", "text": "ผลกระทบต่อโครงการ", "size": "lg", "weight": "bold", "color": "#000000"}]
            + [{"type": "text", "text": f"• {b}", "wrap": True, "size": "md", "color": "#000000", "weight": "bold", "margin": "xs"} for b in bullets],
        }
        body_contents.append(impact_box)

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "contents": body_contents},
            "footer": {"type": "box", "layout": "vertical", "contents": [
                {"type": "button", "style": "primary", "color": "#1DB446",
                 "action": {"type": "uri", "label": "อ่านต่อ", "uri": link}}
            ]},
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

    # กันส่งซ้ำรายวัน
    sent = load_sent_links()

    # 1) เลือก candidates จาก GoogleNews per-country (คุมต่อประเทศ)
    per_country_count = {c: 0 for c in PROJECT_COUNTRIES}
    candidates = []
    global_candidates = []

    for n in all_news:
        link_norm = _normalize_link(n.get("link", ""))
        if link_norm and link_norm in sent:
            continue

        feed_country = (n.get("feed_country") or "").strip()

        if feed_country in PROJECT_COUNTRIES:
            # per-country feeds
            if per_country_count.get(feed_country, 0) >= MAX_PER_COUNTRY:
                continue
            candidates.append(n)
            per_country_count[feed_country] = per_country_count.get(feed_country, 0) + 1
        else:
            # legacy/global feeds
            # รับเฉพาะถ้าจับประเทศโครงการได้ "ชัด" (อย่างน้อย 1; ถ้ามากกว่า 1 จะปล่อยให้ LLM ชี้ขาด แต่โอกาสหลุดสูง)
            global_candidates.append(n)

    # คุมจำนวน global feeds
    # แนะนำ: ถ้า hint มีมากกว่า 1 ประเทศ ให้ลดความสำคัญ (เอาท้าย ๆ)
    global_candidates.sort(key=lambda x: (len(x.get("countries_hint") or []), x["published"]), reverse=False)
    global_candidates = global_candidates[:MAX_GLOBAL_ITEMS]

    # รวม candidates แล้วคุมจำนวนรวมต่อรัน
    combined = candidates + global_candidates
    combined = combined[:MAX_LLM_ITEMS]

    print("จำนวนข่าวที่ส่งเข้า LLM:", len(combined),
          f"(per-country={len(candidates)}, global={len(global_candidates)})")

    tagged = []
    for idx, n in enumerate(combined, 1):
        print(f"[{idx}/{len(combined)}] LLM tag+filter: ({n.get('feed_country')}) {n['title'][:80]}...")
        n["detail"] = n["title"] if len(n.get("summary","")) < 50 else ""

        tag = gemini_tag_and_filter(n)
        if not tag.get("is_relevant"):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        # ---- Final strict checks (ห้ามหลุดประเทศอื่น) ----
        country_llm = (tag.get("country") or "").strip()
        if country_llm not in PROJECT_COUNTRIES:
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        feed_country = (n.get("feed_country") or "").strip()
        if feed_country in PROJECT_COUNTRIES:
            # per-country mode: ต้องตรงกับ feed_country
            if country_llm != feed_country:
                time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
                continue
        else:
            # global mode: ต้องมีประเทศที่ชัดในข้อความอย่างน้อย 1 (กัน LLM เดา)
            hints = n.get("countries_hint") or []
            if country_llm not in hints:
                time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
                continue

        impact = tag.get("impact_reason", "") or ""
        if not has_meaningful_impact(impact):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        n["topic_type"] = tag.get("topic_type", "other")
        n["region"] = tag.get("region", "other")
        n["impact_reason"] = impact
        n["summary_llm"] = tag.get("summary", "") or n.get("summary","") or n["title"]
        n["country"] = country_llm
        n["projects"] = tag.get("projects", []) or []

        tagged.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    print("จำนวนข่าวที่ผ่าน (domestic + strict country):", len(tagged))
    if not tagged:
        print("ไม่มีข่าวที่มีผลกระทบต่อโครงการอย่างชัดเจน")
        return

    # เลือกสูงสุด 10 ข่าว
    selected = tagged[:10]

    # หา hero image
    for n in selected:
        img = fetch_article_image(n.get("link", ""))
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL
        n["image"] = img
        time.sleep(0.25)

    msgs = create_flex(selected)
    send_to_line(msgs)
    save_sent_links([n["link"] for n in selected])

    print("เสร็จสิ้น")

if __name__ == "__main__":
    main()
