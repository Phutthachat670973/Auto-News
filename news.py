# ============================================================================================================
# PTTEP Domestic-by-Project-Countries News Bot (Google News SEARCH RSS - old style)
# - ใช้ Google News RSS แบบ search?q=... (แบบเก่า)
# - ลดข่าวดิบ 200+ ด้วยการจำกัดต่อ feed / ต่อประเทศ / รวม
# - แกะลิงก์ Google News -> ลิงก์ต้นฉบับ (เพื่อรูป og:image)
# - projects: บังคับเลือกจากโครงการของประเทศนั้น (ไม่ใช้ ALL)
# - เพิ่มสรุปข่าวสั้น ๆ ใน Flex
# ============================================================================================================

import os, re, json, time, random
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

# =========================================
# ENV
# =========================================
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

# ลดข่าวบวม
MAX_ITEMS_PER_FEED = int(os.getenv("MAX_ITEMS_PER_FEED", "18"))   # ต่อประเทศ (ตอน parse)
MAX_PER_COUNTRY = int(os.getenv("MAX_PER_COUNTRY", "2"))          # ต่อประเทศ (ส่งเข้า LLM)
MAX_LLM_ITEMS = int(os.getenv("MAX_LLM_ITEMS", "18"))             # รวมทุกประเทศ
MAX_SEND = int(os.getenv("MAX_SEND", "10"))

# เวลา: ใช้ rolling window เพื่อไม่เจอ 0 ง่ายเวลา GH Actions run ไม่ตรง 21-06
HOURS_BACK = int(os.getenv("HOURS_BACK", "12"))

bangkok_tz = pytz.timezone("Asia/Bangkok")

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# =========================================
# Countries + project mapping
# =========================================
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

# =========================================
# Google News SEARCH RSS (แบบเก่า)
# =========================================
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

def google_news_search_rss(q: str, hl="en", gl="US", ceid="US:en"):
    # ใส่ when:1d ช่วยลดจำนวนข่าวให้เป็นช่วงล่าสุด (ยังค่อนข้างกว้าง)
    q2 = f"({q}) when:1d"
    return f"https://news.google.com/rss/search?q={quote_plus(q2)}&hl={hl}&gl={gl}&ceid={ceid}"

NEWS_FEEDS = []
for c in PROJECT_COUNTRIES:
    NEWS_FEEDS.append(("GoogleNews", c, google_news_search_rss(COUNTRY_QUERY[c])))

# =========================================
# Helpers
# =========================================
def _normalize_link(url: str) -> str:
    try:
        p = urlparse(url)
        netloc = p.netloc.lower()
        scheme = (p.scheme or "https").lower()
        drop = {"fbclid", "gclid", "ref", "mc_cid", "mc_eid"}
        new_q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not (k.startswith("utm_") or k in drop)]
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
    first, last = s.find("{"), s.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            return json.loads(s[first:last + 1])
        except Exception:
            return None
    return None

# =========================================
# แกะลิงก์ Google News -> publisher link (เพื่อรูปปก)
# =========================================
def resolve_google_news_url(url: str) -> str:
    if not url or "news.google.com" not in url:
        return url
    try:
        r = S.get(url, timeout=TIMEOUT, allow_redirects=True)
        html = r.text or ""

        # ลิงก์ที่พบบ่อย: https://www.google.com/url?...&url=<publisher>
        m = re.search(r'https?://www\.google\.com/url\?[^"\']*url=([^&"\']+)', html)
        if m:
            return unquote(m.group(1))

        # fallback: ถ้า redirect ไปโดเมนอื่นแล้ว
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

# =========================================
# Gemini wrapper
# =========================================
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
ประเทศเป้าหมายของข่าวนี้: {feed_country}

กติกา:
- ให้ถือว่าเราต้องการ "ข่าวที่เกิดในประเทศ {feed_country}" หรือกระทบประเทศนี้โดยตรง
- ถ้าไม่ใช่ประเทศนี้ → is_relevant=false
- ไม่จำกัดหมวดข่าว (เศรษฐกิจ/การเมือง/พลังงาน/กฎหมาย/แรงงาน/ความมั่นคง ฯลฯ)
- ถ้าเป็น soft news (กีฬา/ดารา/ไวรัล) ที่ไม่โยงผลกระทบเชิงนโยบาย/เศรษฐกิจ/พลังงาน/ความมั่นคง → false

ถ้า is_relevant=true ต้องให้:
- country ต้องเท่ากับ "{feed_country}"
- projects: ต้องเลือกจากรายการนี้เท่านั้น (ห้ามใช้ ALL): {allowed_projects}
  ถ้าไม่แน่ใจ ให้เลือก 1-2 โครงการที่ใกล้เคียงที่สุดในประเทศนี้
- summary: สรุปข่าวไทย 2–3 ประโยค ว่าข่าวเกี่ยวกับอะไร
- impact_reason: เขียนเป็น bullet หลายบรรทัด “เฉพาะผลกระทบต่อโครงการ” ให้ชัด (ต้นทุน/กฎระเบียบ/ตารางงาน/ความเสี่ยง/ความต่อเนื่อง)

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุปจาก RSS: {news['summary']}

ตอบเป็น JSON ตาม schema เท่านั้น:
{json.dumps(schema, ensure_ascii=False)}
"""

    try:
        r = call_gemini(prompt, want_json=True)
        data = _extract_json_object((getattr(r, "text", "") or "").strip())
        if not isinstance(data, dict) or not data.get("is_relevant"):
            return {"is_relevant": False}

        if (data.get("country") or "").strip() != feed_country:
            return {"is_relevant": False}

        projs = data.get("projects") or []
        if not isinstance(projs, list):
            projs = [str(projs)]
        projs = [p for p in projs if p in allowed_projects]
        if not projs:
            projs = allowed_projects[:2]

        data["projects"] = projs
        return data
    except Exception:
        return {"is_relevant": False}

# =========================================
# Fetch news (rolling window)
# =========================================
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
                    link_google = getattr(e, "link", "") or ""
                    link_real = resolve_google_news_url(link_google)

                    out.append({
                        "site": site,
                        "feed_country": feed_country,
                        "title": getattr(e, "title", "") or "",
                        "summary": getattr(e, "summary", "") or "",
                        "link_google": link_google,
                        "link": link_real,
                        "published": dt,
                        "date": dt.strftime("%d/%m/%Y %H:%M"),
                    })
                    added += 1
                    if added >= MAX_ITEMS_PER_FEED:
                        break
        except Exception:
            pass

    # dedupe
    uniq, seen = [], set()
    for n in out:
        k = _normalize_link(n["link"])
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)

    uniq.sort(key=lambda x: x["published"], reverse=True)
    return uniq

# =========================================
# Flex
# =========================================
def create_flex(news_items):
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    for n in news_items:
        bullets = _impact_to_bullets(n.get("impact_reason", ""))

        link = n.get("link") or "https://news.google.com/"
        img = n.get("image") or DEFAULT_ICON_URL

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
            {"type": "text", "text": f"โครงการ: {proj_txt}", "size": "xs", "color": "#555555", "margin": "sm", "wrap": True},
        ]

        if summary_txt:
            body_contents.append({"type": "text", "text": f"สรุป: {summary_txt}", "size": "sm", "wrap": True, "margin": "md"})

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

# =========================================
# LINE broadcast
# =========================================
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

# =========================================
# MAIN
# =========================================
def main():
    print("ดึงข่าว...")
    all_news = fetch_news_window()
    print("จำนวนข่าวดิบทั้งหมด:", len(all_news))
    if not all_news:
        print("ไม่พบข่าวในช่วงเวลา")
        return

    sent = load_sent_links()

    # เลือก candidates (คุมต่อประเทศ + คุมรวม)
    per_country = {c: 0 for c in PROJECT_COUNTRIES}
    candidates = []

    for n in all_news:
        if _normalize_link(n["link"]) in sent:
            continue
        c = n.get("feed_country")
        if c not in PROJECT_COUNTRIES:
            continue
        if per_country[c] >= MAX_PER_COUNTRY:
            continue

        candidates.append(n)
        per_country[c] += 1

        if len(candidates) >= MAX_LLM_ITEMS:
            break

    print("จำนวนข่าวที่ส่งเข้า LLM:", len(candidates))

    tagged = []
    for idx, n in enumerate(candidates, 1):
        print(f"[{idx}/{len(candidates)}] LLM tag+filter: ({n['feed_country']}) {n['title'][:80]}...")
        tag = gemini_tag_and_filter(n)

        if not tag.get("is_relevant"):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        n["country"] = tag.get("country", n["feed_country"])
        n["projects"] = tag.get("projects", []) or PROJECTS_BY_COUNTRY.get(n["feed_country"], [])[:2]
        n["impact_reason"] = tag.get("impact_reason", "")
        n["summary_llm"] = tag.get("summary", "")

        if not has_meaningful_impact(n["impact_reason"]):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        tagged.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    print("จำนวนข่าวที่ผ่าน:", len(tagged))
    if not tagged:
        print("ไม่มีข่าวที่มีผลกระทบชัดเจน")
        return

    final = tagged[:MAX_SEND]

    # รูปปก: ดึงจากลิงก์ต้นฉบับ
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
