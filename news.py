# ============================================================================================================
# PTTEP Domestic News Bot (Google News SEARCH RSS - NO MAX LIMITS)
# - Pull ALL news (no MAX_ITEMS_PER_FEED / MAX_PER_COUNTRY / MAX_LLM_ITEMS / SEND_MAX)
# - Resolve Google News link -> publisher
# - Fetch source context (og:description/meta/lead paragraphs)
# - Gemini summarizes impact based on source context (no hallucination)
# - Send ALL items to LINE (split 10 bubbles per flex carousel automatically)
# ============================================================================================================

import os
import re
import json
import time
import random
import html as _html
from datetime import datetime, timedelta
from urllib.parse import (
    urlparse, urlunparse, parse_qsl, urlencode,
    quote_plus, unquote
)

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
SLEEP_BETWEEN_CALLS = (0.6, 1.2)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# Rolling window (ยังคงไว้เพื่อไม่ให้ RSS เก่ามากเกิน)
HOURS_BACK = int(os.getenv("HOURS_BACK", "12"))

# บริบทจากแหล่งข่าว (ตัดความยาวเพื่อกัน token)
SOURCE_CONTEXT_MAX_CHARS = int(os.getenv("SOURCE_CONTEXT_MAX_CHARS", "1400"))

# หน่วงตอนยิง LINE กันถี่เกิน
SLEEP_BETWEEN_LINE_SEND = float(os.getenv("SLEEP_BETWEEN_LINE_SEND", "0.25"))

bangkok_tz = pytz.timezone("Asia/Bangkok")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ============================================================================================================
# CONTEXT: Countries & Projects
# ============================================================================================================
PROJECT_COUNTRIES = [
    "Thailand", "Myanmar", "Vietnam", "Malaysia", "Indonesia",
    "UAE", "Oman", "Algeria", "Mozambique", "Australia", "Brazil", "Mexico"
]

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
Thailand - G1/61, G2/61, Arthit, Sinphuhorm, MTJDA Block A-18
Myanmar – Zawtika, Yadana, Yetagun
Vietnam – Block B & 48/95, Block 52/97, 16-1 (Te Giac Trang)
Malaysia – MTJDA Block A-18, SK309, SK311, SK410B
Indonesia – South Sageri, South Mandar, Malunda
UAE – Ghasha Concession, Abu Dhabi Offshore
Oman – Oman Block 12
Algeria – Bir Seba, Hirad, Touat
Mozambique – Mozambique Area 1 (Rovuma LNG)
Australia – Montara, Timor Sea / Browse Basin
Brazil – BM-ES-23, BM-ES-24
Mexico – Mexico Block 12 (2.4)
"""

# ============================================================================================================
# Google News SEARCH RSS (old style) + broad topic guardrail
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

TOPIC_GUARDRAIL = (
    "(economy OR economic OR inflation OR gdp OR currency OR rate OR bond OR trade OR tariff OR "
    "politics OR election OR government OR policy OR tax OR regulation OR ministry OR "
    "energy OR oil OR gas OR lng OR pipeline OR power OR electricity OR upstream OR "
    "sanction OR protest OR strike OR conflict OR security)"
)

def google_news_search_rss(q: str, hl="en", gl="US", ceid="US:en"):
    q2 = f"({q}) {TOPIC_GUARDRAIL} when:1d"
    return f"https://news.google.com/rss/search?q={quote_plus(q2)}&hl={hl}&gl={gl}&ceid={ceid}"

NEWS_FEEDS = [("GoogleNews", c, google_news_search_rss(COUNTRY_QUERY[c])) for c in PROJECT_COUNTRIES]

# ============================================================================================================
# HELPERS
# ============================================================================================================
def clean_text(s: str) -> str:
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

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
        return ["ยังไม่พบผลกระทบที่ชัดเจนจากแหล่งข่าว (ส่งเพื่อให้ติดตามต่อ)"]
    text = clean_text(text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        lines = [text.strip()]
    bullets = []
    for line in lines:
        s = line.strip()
        s = re.sub(r"^[\u2022\*\-\u00b7·•\s]+", "", s)
        s = re.sub(r"^\d+[\.\)]\s*", "", s)
        if s:
            bullets.append(s)
    return bullets or ["ยังไม่พบผลกระทบที่ชัดเจนจากแหล่งข่าว (ส่งเพื่อให้ติดตามต่อ)"]

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
    first, last = s.find("{"), s.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(s[first:last + 1])
        except Exception:
            return None
    return None

# ============================================================================================================
# Resolve Google News -> publisher link
# ============================================================================================================
def resolve_google_news_url(url: str) -> str:
    if not url:
        return ""
    if "news.google.com" not in url:
        return url
    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        html = r.text or ""
        m = re.search(r'https?://www\.google\.[^/]+/url\?[^"\']*url=([^&"\']+)', html)
        if m:
            return unquote(m.group(1))
        m = re.search(r'href="(https?://[^"]+)"', html, flags=re.I)
        if m and "news.google.com" not in m.group(1):
            return m.group(1)
        if r.url and "news.google.com" not in r.url:
            return r.url
    except Exception:
        pass
    return url

# ============================================================================================================
# Fetch source context (og:description / meta description / first paragraphs)
# ============================================================================================================
def fetch_source_context(url: str) -> str:
    if not url:
        return ""
    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return ""
        html = r.text or ""

        def _meta(patterns):
            for pat in patterns:
                m = re.search(pat, html, re.I)
                if m:
                    return clean_text(m.group(1))
            return ""

        og_title = _meta([r'<meta[^>]+property=[\'"]og:title[\'"][^>]+content=[\'"]([^\'"]+)[\'"]'])
        og_desc  = _meta([r'<meta[^>]+property=[\'"]og:description[\'"][^>]+content=[\'"]([^\'"]+)[\'"]'])
        meta_desc = _meta([r'<meta[^>]+name=[\'"]description[\'"][^>]+content=[\'"]([^\'"]+)[\'"]'])

        paras = re.findall(r"<p[^>]*>(.*?)</p>", html, flags=re.I | re.S)
        para_texts = []
        for p in paras[:10]:
            t = clean_text(p)
            if len(t) >= 40:
                para_texts.append(t)
        lead = " ".join(para_texts[:3])

        parts = []
        if og_title: parts.append(f"Title: {og_title}")
        if og_desc: parts.append(f"OG_Desc: {og_desc}")
        if meta_desc and meta_desc != og_desc: parts.append(f"Meta_Desc: {meta_desc}")
        if lead: parts.append(f"Lead: {lead}")

        ctx = "\n".join(parts).strip()
        if len(ctx) > SOURCE_CONTEXT_MAX_CHARS:
            ctx = ctx[:SOURCE_CONTEXT_MAX_CHARS].rstrip() + "…"
        return ctx
    except Exception:
        return ""

# ============================================================================================================
# Fetch og:image
# ============================================================================================================
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
# Gemini wrapper
# ============================================================================================================
GEMINI_CALLS = 0

def call_gemini(prompt: str, want_json: bool = False):
    global GEMINI_CALLS
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError("เกินโควต้า Gemini ประจำวัน")

    last_error = None
    for i in range(1, MAX_RETRIES + 1):
        try:
            gen_cfg = {"temperature": 0.2, "max_output_tokens": 950}
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

def gemini_score_and_impact(news):
    feed_country = (news.get("feed_country") or "").strip()
    allowed_projects = PROJECTS_BY_COUNTRY.get(feed_country, [])

    schema = {
        "type": "object",
        "properties": {
            "is_relevant": {"type": "boolean"},
            "relevance_score": {"type": "integer", "minimum": 0, "maximum": 100},
            "impact_strength": {"type": "string", "enum": ["high", "medium", "low"]},
            "summary": {"type": "string"},
            "impact_reason": {"type": "string"},
            "country": {"type": "string"},
            "projects": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["is_relevant", "relevance_score", "impact_strength"],
    }

    prompt = f"""
{PTTEP_PROJECTS_CONTEXT}

บทบาทของคุณ: Analyst + News Screener ของ PTTEP

กติกา (STRICT):
- ประเมินเฉพาะข่าวที่เป็น "เหตุการณ์ในประเทศ {feed_country}" หรือกระทบประเทศนี้โดยตรง
- ถ้าไม่ใช่ประเทศนี้ → is_relevant=false, relevance_score ต่ำ
- ให้คะแนน relevance_score (0-100) ตาม "โอกาสกระทบโครงการในประเทศนี้"
- impact_strength:
  - high: มีเหตุ/นโยบาย/ความไม่สงบ/กฎระเบียบ/ภาษี/แรงงาน/พลังงาน ที่ชี้ชัดว่าเสี่ยงต่อ cost/schedule/supply/security
  - medium: มีสัญญาณกระทบทางเศรษฐกิจ/การเมือง/พลังงานที่น่าเชื่อมโยง แต่ยังไม่ specific มาก
  - low: ข่าวทั่วไป/soft news หรือเชื่อมโยงยาก

สำคัญ: impact_reason ต้อง “อิงจากข้อมูลในแหล่งข่าว” ที่ให้ด้านล่าง
- ห้ามตอบลอย ๆ โดยไม่โยงเหตุผลจาก source_context
- เขียนเป็น bullet 1–3 บรรทัด อธิบายว่า "กระทบโครงการในประเทศนี้ยังไง" (ต้นทุน/กฎระเบียบ/ความเสี่ยง/การดำเนินงาน)
- ถ้าแหล่งข่าวไม่ระบุผลกระทบโดยตรง ให้เขียนว่า "ยังไม่พบผลกระทบโดยตรงจากแหล่งข่าว" + อธิบายว่าข้อมูลในข่าวพูดถึงอะไร

projects:
- ห้ามใช้ ALL
- เลือกจากรายการนี้เท่านั้น: {allowed_projects}
- ถ้าไม่แน่ใจ ให้เลือก 1–2 โครงการที่เป็นตัวแทนประเทศนี้

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุปจาก RSS: {news['summary']}
source_context: {news.get('source_context','')}

ตอบเป็น JSON ตาม schema เท่านั้น:
{json.dumps(schema, ensure_ascii=False)}
"""

    r = call_gemini(prompt, want_json=True)
    raw = (getattr(r, "text", "") or "").strip()
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        return {"is_relevant": False, "relevance_score": 0, "impact_strength": "low"}

    if isinstance(data.get("summary"), str):
        data["summary"] = clean_text(data["summary"])
    if isinstance(data.get("impact_reason"), str):
        data["impact_reason"] = clean_text(data["impact_reason"])

    # normalize projects
    projs = data.get("projects") or []
    if not isinstance(projs, list):
        projs = [str(projs)]
    projs = [p for p in projs if isinstance(p, str) and p.strip().lower() != "all"]
    projs = [p for p in projs if p in allowed_projects]
    if not projs:
        projs = allowed_projects[:2] if allowed_projects else []
    data["projects"] = projs

    # enforce country
    data["country"] = feed_country

    # clamp score
    try:
        sc = int(data.get("relevance_score", 0))
    except Exception:
        sc = 0
    data["relevance_score"] = max(0, min(100, sc))

    if data.get("impact_strength") not in ("high", "medium", "low"):
        data["impact_strength"] = "low"

    return data

# ============================================================================================================
# Fetch ALL news in window (no per-feed max)
# ============================================================================================================
def fetch_news_window():
    now_local = datetime.now(bangkok_tz)
    start = now_local - timedelta(hours=HOURS_BACK)
    end = now_local

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
                    title = clean_text(getattr(e, "title", "") or "")
                    summary = clean_text(getattr(e, "summary", "") or "")
                    link_google = getattr(e, "link", "") or ""
                    link_real = resolve_google_news_url(link_google)

                    out.append({
                        "site": site,
                        "feed_country": feed_country,
                        "title": title,
                        "summary": summary,
                        "link_google": link_google,
                        "link": link_real,
                        "published": dt,
                        "date": dt.strftime("%d/%m/%Y %H:%M"),
                    })
        except Exception:
            pass

    # dedupe by real link
    uniq, seen = [], set()
    for n in out:
        k = _normalize_link(n.get("link", ""))
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)

    uniq.sort(key=lambda x: x["published"], reverse=True)
    return uniq

# ============================================================================================================
# FLEX (split 10/batch)
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

        summary_txt = clean_text(n.get("summary_llm") or n.get("summary") or "")
        if len(summary_txt) > 260:
            summary_txt = summary_txt[:260].rstrip() + "…"

        score = n.get("relevance_score", 0)
        strength = n.get("impact_strength", "low")
        note = n.get("note", "")

        body_contents = [
            {"type": "text", "text": n["title"], "weight": "bold", "size": "lg", "wrap": True},
            {"type": "text", "text": f"🗓 {n['date']}", "size": "xs", "color": "#888888", "margin": "sm"},
            {"type": "text", "text": f"🌍 {country_txt} | {n['site']}", "size": "xs", "color": "#448AFF", "margin": "xs"},
            {"type": "text", "text": f"โครงการ: {proj_txt}", "size": "xs", "color": "#555555", "margin": "sm", "wrap": True},
            {"type": "text", "text": f"คะแนน: {score}/100 | ระดับ: {strength}", "size": "xs", "color": "#555555", "margin": "sm", "wrap": True},
        ]

        if note:
            body_contents.append({"type": "text", "text": note, "size": "xs", "color": "#999999", "margin": "xs", "wrap": True})

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
            "contents": [{"type": "text", "text": "ผลกระทบต่อโครงการ (อิงจากแหล่งข่าว)", "size": "lg", "weight": "bold"}]
            + [{"type": "text", "text": f"• {b}", "wrap": True, "size": "md", "weight": "bold", "margin": "xs"} for b in bullets],
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

    messages = []
    for i in range(0, len(bubbles), 10):
        chunk = bubbles[i:i + 10]
        part = (i // 10) + 1
        messages.append({
            "type": "flex",
            "altText": f"ข่าว PTTEP (Domestic) {now_txt} [{part}]",
            "contents": {"type": "carousel", "contents": chunk},
        })
    return messages

# ============================================================================================================
# LINE broadcast
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
        print(f"Send {i}/{len(messages)}: {r.status_code}")
        print("Response body:", r.text)

        if r.status_code >= 300:
            print("หยุดส่งเพราะ LINE ตอบ error")
            break

        time.sleep(SLEEP_BETWEEN_LINE_SEND)

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

    # เอาข่าวทั้งหมด (ไม่ใส่ Max) แต่ยังตัดซ้ำในวันเดียวกัน
    candidates = []
    for n in all_news:
        link_norm = _normalize_link(n.get("link", ""))
        if link_norm and link_norm in sent:
            continue
        candidates.append(n)

    print("จำนวนข่าวใหม่ (หลังตัดซ้ำวันนี้):", len(candidates))
    if not candidates:
        print("ไม่มีข่าวใหม่")
        return

    tagged = []
    quota_exhausted = False

    for idx, n in enumerate(candidates, 1):
        print(f"[{idx}/{len(candidates)}] เตรียม source_context: ({n.get('feed_country')}) {n['title'][:80]}...")

        # ดึง context จากแหล่งข่าวจริง (ช่วยให้สรุปผลกระทบ “อิงแหล่งข่าว”)
        n["source_context"] = fetch_source_context(n.get("link", ""))

        if not quota_exhausted:
            try:
                print(f"    -> Gemini score+impact (calls={GEMINI_CALLS}/{GEMINI_DAILY_BUDGET})")
                tag = gemini_score_and_impact(n)

                n["country"] = (tag.get("country") or n.get("feed_country") or "ไม่ระบุ").strip()
                n["projects"] = tag.get("projects") or PROJECTS_BY_COUNTRY.get(n["country"], [])[:2]
                n["summary_llm"] = clean_text(tag.get("summary") or n.get("summary") or n["title"])
                n["impact_reason"] = clean_text(tag.get("impact_reason") or "ยังไม่พบผลกระทบโดยตรงจากแหล่งข่าว (ส่งเพื่อให้ติดตามต่อ)")
                n["relevance_score"] = int(tag.get("relevance_score", 0) or 0)
                n["impact_strength"] = tag.get("impact_strength", "low")
                n["is_relevant"] = bool(tag.get("is_relevant", False))
            except RuntimeError as e:
                # โควต้าเต็ม -> ไม่มั่วผลกระทบต่อจากนี้
                if "โควต้า" in str(e) or "quota" in str(e).lower():
                    quota_exhausted = True
                    n["note"] = "หมายเหตุ: โควต้า Gemini เต็มแล้ว ข่าวถัดไปจะไม่ประเมินด้วย LLM"
                else:
                    n["note"] = f"หมายเหตุ: LLM error ({str(e)[:120]})"
                # fallback แบบไม่มั่ว
                n["country"] = (n.get("feed_country") or "ไม่ระบุ").strip()
                n["projects"] = PROJECTS_BY_COUNTRY.get(n["country"], [])[:2]
                n["summary_llm"] = clean_text(n.get("summary") or n["title"])
                n["impact_reason"] = "ยังไม่ได้ประเมินผลกระทบด้วย LLM (โควต้า/ข้อผิดพลาด) — โปรดอ่านแหล่งข่าว"
                n["relevance_score"] = 0
                n["impact_strength"] = "low"
                n["is_relevant"] = False
            except Exception as e:
                n["note"] = f"หมายเหตุ: LLM error ({str(e)[:120]})"
                n["country"] = (n.get("feed_country") or "ไม่ระบุ").strip()
                n["projects"] = PROJECTS_BY_COUNTRY.get(n["country"], [])[:2]
                n["summary_llm"] = clean_text(n.get("summary") or n["title"])
                n["impact_reason"] = "ยังไม่ได้ประเมินผลกระทบด้วย LLM (ข้อผิดพลาด) — โปรดอ่านแหล่งข่าว"
                n["relevance_score"] = 0
                n["impact_strength"] = "low"
                n["is_relevant"] = False
        else:
            # โควต้าเต็มแล้ว -> ส่งข่าวต่อ แต่ไม่แต่งผลกระทบ
            n["note"] = "ไม่ได้ประเมินด้วย LLM (โควต้าเต็ม) — โปรดอ่านแหล่งข่าว"
            n["country"] = (n.get("feed_country") or "ไม่ระบุ").strip()
            n["projects"] = PROJECTS_BY_COUNTRY.get(n["country"], [])[:2]
            n["summary_llm"] = clean_text(n.get("summary") or n["title"])
            n["impact_reason"] = "ยังไม่ได้ประเมินผลกระทบด้วย LLM (โควต้าเต็ม) — โปรดอ่านแหล่งข่าว"
            n["relevance_score"] = 0
            n["impact_strength"] = "low"
            n["is_relevant"] = False

        tagged.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    print("จำนวนข่าวทั้งหมดที่จะส่ง:", len(tagged))

    # รูปปก: พยายามดึงจาก publisher ถ้าไม่ได้ใช้ default
    for i, n in enumerate(tagged, 1):
        print(f"[img {i}/{len(tagged)}] ดึงรูป: {n.get('title','')[:60]}...")
        img = fetch_article_image(n.get("link", ""))
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL
        n["image"] = img
        time.sleep(0.15)

    msgs = create_flex(tagged)
    print("จำนวน flex messages ที่จะส่ง (10 ข่าว/ข้อความ):", len(msgs))

    send_to_line(msgs)
    save_sent_links([n["link"] for n in tagged])

    print("เสร็จสิ้น")

if __name__ == "__main__":
    main()
