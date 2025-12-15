# ============================================================================================================
# PTTEP Domestic News Bot (Keep "domestic + strict country" behavior, but fix:
# 1) too many raw news -> cap per feed / per country / total raw
# 2) cover image -> resolve Google News link to publisher + fetch og:image
# 3) projects != ALL -> enforce country->projects list + fallback
# 4) add short summary in Flex
# ============================================================================================================

import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, quote_plus, unquote

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
MAX_RETRIES = 6
SLEEP_BETWEEN_CALLS = (0.5, 1.0)

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# -------------------------
# ลดข่าวดิบตั้งแต่ต้น
# -------------------------
# จำกัดจำนวน entry ที่อ่านจาก RSS ต่อประเทศ (กัน 200+)
MAX_ITEMS_PER_FEED = int(os.getenv("MAX_ITEMS_PER_FEED", "12"))     # แนะนำ 10-15
# จำกัดจำนวนข่าวที่จะส่งเข้า LLM ต่อประเทศ
MAX_PER_COUNTRY = int(os.getenv("MAX_PER_COUNTRY", "3"))           # แนะนำ 2-3
# จำกัดจำนวนรวมที่จะส่งเข้า LLM
MAX_LLM_ITEMS = int(os.getenv("MAX_LLM_ITEMS", "24"))              # แนะนำ 18-24
# จำกัดข่าวจาก legacy/global feeds (ถ้าเปิด)
MAX_GLOBAL_ITEMS = int(os.getenv("MAX_GLOBAL_ITEMS", "4"))         # แนะนำ 0-6
# จำกัดจำนวนข่าวที่ส่ง LINE
MAX_SEND = int(os.getenv("MAX_SEND", "10"))

# window แบบ rolling กัน “เจอ 0” เวลา GH Actions รันไม่ตรงช่วงดึก
HOURS_BACK = int(os.getenv("HOURS_BACK", "12"))

bangkok_tz = pytz.timezone("Asia/Bangkok")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ============================================================================================================
# COUNTRIES + PROJECTS
# ============================================================================================================
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

PROJECTS_BY_COUNTRY = {
    "Thailand": ["G1/61", "G2/61", "Arthit", "Sinphuhorm", "MTJDA Block A-18"],
    "Myanmar": ["Zawtika", "Yadana", "Yetagun"],
    "Vietnam": ["Block B & 48/95", "Block 52/97", "16-1 (Te Giac Trang)"],
    "Malaysia": ["MTJDA Block A-18", "SK309", "SK311", "SK410B"],
    "Indonesia": ["South Sageri", "South Mandar", "Malunda"],
    "UAE": ["Ghasha Concession", "Abu Dhabi Offshore"],
    "Oman": ["Oman Block 12"],
    "Algeria": ["Bir Seba", "Hirad", "Touat"],
    "Mozambique": ["Mozambique Area 1 (Rovuma LNG)"],
    "Australia": ["Montara", "Timor Sea / Browse Basin"],
    "Brazil": ["BM-ES-23", "BM-ES-24"],
    "Mexico": ["Mexico Block 12 (2.4)"],
}

PTTEP_PROJECTS_CONTEXT = r"""
[PTTEP_PROJECTS_CONTEXT]
ประเทศไทย (Thailand) - G1/61, G2/61, Arthit, Sinphuhorm, MTJDA Block A-18
เมียนมา (Myanmar) – Zawtika, Yadana, Yetagun
เวียดนาม (Vietnam) – Block B & 48/95, Block 52/97, 16-1 (Te Giac Trang)
มาเลเซีย (Malaysia) – MTJDA Block A-18, SK309, SK311, SK410B
อินโดนีเซีย (Indonesia) – South Sageri, South Mandar, Malunda
UAE – Ghasha Concession, Abu Dhabi Offshore
Oman – Oman Block 12
Algeria – Bir Seba, Hirad, Touat
Mozambique – Mozambique Area 1 (Rovuma LNG)
Australia – Montara, Timor Sea / Browse Basin
Brazil – BM-ES-23, BM-ES-24
Mexico – Mexico Block 12 (2.4)
"""

def detect_project_countries(text: str):
    t = (text or "").lower()
    hits = []
    for c, keys in PROJECT_COUNTRY_SYNONYMS.items():
        if any(k in t for k in keys):
            hits.append(c)
    return sorted(set(hits))

# ============================================================================================================
# GOOGLE NEWS (แบบเดิม: search RSS) + TOPIC GUARDRAIL แบบกว้าง (ช่วยลดข่าวกีฬา/ดารา)
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

# กว้างพอสำหรับ econ/politics/energy + security แต่ลดกีฬา/ไลฟ์สไตล์ได้เยอะ
TOPIC_GUARDRAIL = (
    "(economy OR economic OR inflation OR gdp OR currency OR rate OR bond OR trade OR tariff OR "
    "politics OR election OR government OR policy OR tax OR regulation OR ministry OR "
    "energy OR oil OR gas OR lng OR pipeline OR power OR electricity OR upstream OR "
    "sanction OR protest OR strike OR conflict OR security)"
)

def google_news_search_rss(q: str, hl="en", gl="US", ceid="US:en"):
    # when:1d ลดข่าวเก่า / ช่วยคุมจำนวน
    q2 = f"({q}) {TOPIC_GUARDRAIL} when:1d"
    return f"https://news.google.com/rss/search?q={quote_plus(q2)}&hl={hl}&gl={gl}&ceid={ceid}"

# ============================================================================================================
# (Optional) legacy/global feeds (ปิด by default เพื่อลดข่าวดิบ)
# เปิดได้ด้วย ENABLE_LEGACY_FEEDS=true
# ============================================================================================================
ENABLE_LEGACY_FEEDS = os.getenv("ENABLE_LEGACY_FEEDS", "false").lower() == "true"

LEGACY_FEEDS = [
    ("Oilprice", "GLOBAL", "https://oilprice.com/rss/main"),
    ("Economist", "GLOBAL", "https://www.economist.com/latest/rss.xml"),
    ("YahooFinance", "GLOBAL", "https://finance.yahoo.com/news/rssindex"),
]

NEWS_FEEDS = []
for c in PROJECT_COUNTRIES:
    NEWS_FEEDS.append(("GoogleNews", c, google_news_search_rss(COUNTRY_QUERY[c])))

if ENABLE_LEGACY_FEEDS:
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
    p = get_sent_links_file(datetime.now(bangkok_tz).strftime("%Y-%m-%d"))
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
    bullets = []
    for line in lines:
        s = re.sub(r"^[\u2022\*\-\u00b7·•\s]+", "", line.strip())
        s = re.sub(r"^\d+[\.\)]\s*", "", s)
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
        try:
            return json.loads(s[first:last + 1])
        except Exception:
            return None
    return None

# ============================================================================================================
# Resolve Google News link -> Publisher link (ทำให้รูปปกไม่เป็น Google)
# ============================================================================================================
def resolve_google_news_url(url: str) -> str:
    if not url:
        return ""
    if "news.google.com" not in url:
        return url

    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        html = r.text or ""

        # pattern 1: google redirect url=...
        m = re.search(r'https?://www\.google\.[^/]+/url\?[^"\']*url=([^&"\']+)', html)
        if m:
            return unquote(m.group(1))

        # pattern 2: sometimes publisher appears in "href" full url
        m = re.search(r'href="(https?://[^"]+)"', html, flags=re.I)
        if m and "news.google.com" not in m.group(1):
            return m.group(1)

        # fallback: if requests ended on publisher domain already
        if r.url and "news.google.com" not in r.url:
            return r.url
    except Exception:
        pass

    return url

def fetch_article_image(url: str) -> str:
    if not url:
        return ""
    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return ""

        html = r.text or ""

        m = re.search(r'<meta[^>]+property=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
        if m:
            return m.group(1)

        m = re.search(r'<meta[^>]+name=[\'"]twitter:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]', html, re.I)
        if m:
            return m.group(1)

        return ""
    except Exception:
        return ""

# ============================================================================================================
# GEMINI WRAPPER
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

# ============================================================================================================
# GEMINI TAG+FILTER (keep strict country behavior + projects must be real names)
# ============================================================================================================
def gemini_tag_and_filter(news):
    feed_country = (news.get("feed_country") or "").strip()
    allowed_projects = PROJECTS_BY_COUNTRY.get(feed_country, [])

    schema = {
        "type": "object",
        "properties": {
            "is_relevant": {"type": "boolean"},
            "summary": {"type": "string"},
            "impact_reason": {"type": "string"},
            "country": {"type": "string"},
            "projects": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["is_relevant"],
    }

    prompt = f"""
{PTTEP_PROJECTS_CONTEXT}

บทบาทของคุณ: Analyst + News Screener ของ PTTEP

กติกา (STRICT):
- ข่าวนี้ต้องเกี่ยวกับ "เหตุการณ์ในประเทศ {feed_country}" หรือกระทบประเทศนี้โดยตรงเท่านั้น
- ถ้าไม่ใช่ประเทศนี้ → is_relevant=false
- ไม่จำกัดหมวด: เศรษฐกิจ/การเมือง/พลังงาน/กฎหมาย/ความมั่นคง/แรงงาน ฯลฯ (ได้หมด)

ถ้า is_relevant=true:
- country ต้องเป็น "{feed_country}" เท่านั้น
- projects ห้ามใช้ ["ALL"] ห้ามใช้คำว่า "ALL"
  ต้องเลือกจากรายการโครงการประเทศนี้เท่านั้น: {allowed_projects}
  ถ้าไม่แน่ใจ ให้เลือก 1-2 โครงการที่ใกล้เคียง/เกี่ยวข้องที่สุดในประเทศนี้
- summary: สรุปข่าวภาษาไทย 2–3 ประโยคว่าข่าวเกี่ยวกับอะไร
- impact_reason: bullet หลายบรรทัด เฉพาะผลกระทบต่อโครงการ (ต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง/ความต่อเนื่อง)

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุปจาก RSS: {news['summary']}

ตอบเป็น JSON ตาม schema เท่านั้น:
{json.dumps(schema, ensure_ascii=False)}
"""

    try:
        r = call_gemini(prompt, want_json=True)
        raw = (getattr(r, "text", "") or "").strip()
        data = _extract_json_object(raw)
        if not isinstance(data, dict):
            return {"is_relevant": False}

        if not data.get("is_relevant"):
            return {"is_relevant": False}

        if (data.get("country") or "").strip() != feed_country:
            return {"is_relevant": False}

        projs = data.get("projects") or []
        if not isinstance(projs, list):
            projs = [str(projs)]

        # กัน ALL
        projs = [p for p in projs if isinstance(p, str) and p.strip().lower() != "all"]

        # enforce allowed list
        projs = [p for p in projs if p in allowed_projects]

        if not projs:
            projs = allowed_projects[:2] if allowed_projects else []

        data["projects"] = projs
        return data
    except Exception:
        return {"is_relevant": False}

# ============================================================================================================
# FETCH NEWS (rolling window) + CAP per feed + resolve publisher link
# ============================================================================================================
def fetch_news_window():
    now_local = datetime.now(bangkok_tz)
    start = now_local - timedelta(hours=HOURS_BACK)
    end = now_local

    out = []
    for site, feed_country, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)

            added = 0
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
                    link_google = getattr(e, "link", "") or ""

                    link_real = resolve_google_news_url(link_google)

                    out.append({
                        "site": site,
                        "feed_country": feed_country,     # ประเทศของฟีด หรือ GLOBAL
                        "title": title,
                        "summary": summary,
                        "link_google": link_google,
                        "link": link_real,
                        "published": dt,
                        "date": dt.strftime("%d/%m/%Y %H:%M"),
                        "countries_hint": detect_project_countries(f"{title} {summary}"),
                    })

                    added += 1
                    if added >= MAX_ITEMS_PER_FEED:
                        break
        except Exception:
            pass

    # dedupe ตาม link จริง
    uniq, seen = [], set()
    for n in out:
        k = _normalize_link(n.get("link", ""))
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)

    uniq.sort(key=lambda x: x["published"], reverse=True)
    return uniq

# ============================================================================================================
# FLEX MESSAGE (เพิ่มสรุปข่าว)
# ============================================================================================================
def create_flex(news_items):
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    for n in news_items:
        bullets = _impact_to_bullets(n.get("impact_reason", ""))

        link = n.get("link") or "https://news.google.com/"
        img = n.get("image") or DEFAULT_ICON_URL
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL

        country_txt = (n.get("country") or n.get("feed_country") or "ไม่ระบุ").strip()
        projects = n.get("projects") or []
        proj_txt = ", ".join(projects[:3]) if isinstance(projects, list) and projects else "ไม่ระบุ"

        summary_txt = (n.get("summary_llm") or "").strip()
        if len(summary_txt) > 260:
            summary_txt = summary_txt[:260].rstrip() + "…"

        body_contents = [
            {"type": "text", "text": n["title"], "weight": "bold", "size": "lg", "wrap": True},
            {"type": "text", "text": f"🗓 {n['date']}", "size": "xs", "color": "#888888", "margin": "sm"},
            {"type": "text", "text": f"🌍 {country_txt} | {n['site']}", "size": "xs", "color": "#448AFF", "margin": "xs"},
            {"type": "text", "text": f"โครงการ: {proj_txt} | ประเทศ: {country_txt}", "size": "xs", "color": "#555555", "margin": "sm", "wrap": True},
        ]

        if summary_txt:
            body_contents.append({
                "type": "text",
                "text": f"สรุป: {summary_txt}",
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#111111",
            })

        impact_box = {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": [{"type": "text", "text": "ผลกระทบต่อโครงการ", "size": "lg", "weight": "bold", "color": "#000000"}]
            + [{"type": "text", "text": f"• {b}", "wrap": True, "size": "md", "color": "#000000", "weight": "bold", "margin": "xs"} for b in bullets],
        }
        body_contents.append(impact_box)

        bubbles.append({
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "contents": body_contents},
            "footer": {"type": "box", "layout": "vertical", "contents": [
                {"type": "button", "style": "primary", "color": "#1DB446",
                 "action": {"type": "uri", "label": "อ่านต่อ", "uri": link}}
            ]},
        })

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
# MAIN
# ============================================================================================================
def main():
    print("ดึงข่าว...")
    all_news = fetch_news_window()
    print("จำนวนข่าวดิบทั้งหมด:", len(all_news))

    if not all_news:
        print("ไม่พบข่าวในช่วงเวลา")
        return

    sent = load_sent_links()

    # แยก per-country และ global (ถ้ามี)
    per_country_count = {c: 0 for c in PROJECT_COUNTRIES}
    candidates = []
    global_candidates = []

    for n in all_news:
        link_norm = _normalize_link(n.get("link", ""))
        if link_norm and link_norm in sent:
            continue

        feed_country = (n.get("feed_country") or "").strip()

        if feed_country in PROJECT_COUNTRIES:
            if per_country_count[feed_country] >= MAX_PER_COUNTRY:
                continue
            candidates.append(n)
            per_country_count[feed_country] += 1
        else:
            global_candidates.append(n)

    # จำกัด global feeds
    if ENABLE_LEGACY_FEEDS and global_candidates:
        # เอาเฉพาะที่ hint ประเทศชัด (ลดเดา)
        global_candidates = [g for g in global_candidates if len(g.get("countries_hint") or []) == 1]
        global_candidates = global_candidates[:MAX_GLOBAL_ITEMS]
    else:
        global_candidates = []

    combined = (candidates + global_candidates)[:MAX_LLM_ITEMS]
    print("จำนวนข่าวที่ส่งเข้า LLM:", len(combined),
          f"(per-country={len(candidates)}, global={len(global_candidates)})")

    tagged = []
    for idx, n in enumerate(combined, 1):
        print(f"[{idx}/{len(combined)}] LLM tag+filter: ({n.get('feed_country')}) {n['title'][:80]}...")

        tag = gemini_tag_and_filter(n)
        if not tag.get("is_relevant"):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        n["country"] = tag.get("country", n.get("feed_country"))
        n["projects"] = tag.get("projects", []) or PROJECTS_BY_COUNTRY.get(n["country"], [])[:2]
        n["impact_reason"] = tag.get("impact_reason", "")
        n["summary_llm"] = tag.get("summary", "")

        if not has_meaningful_impact(n["impact_reason"]):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        tagged.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    print("จำนวนข่าวที่ผ่าน (domestic + strict country):", len(tagged))
    if not tagged:
        print("ไม่มีข่าวที่มีผลกระทบชัดเจน")
        return

    final = tagged[:MAX_SEND]

    # รูปปก: ดึงจาก publisher link (ไม่ใช่ Google)
    for n in final:
        img = fetch_article_image(n.get("link", ""))
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL
        n["image"] = img
        time.sleep(0.25)

    msgs = create_flex(final)
    send_to_line(msgs)
    save_sent_links([n["link"] for n in final])

    print("เสร็จสิ้น")

if __name__ == "__main__":
    main()
