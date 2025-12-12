# ============================================================================================================
# IMPORT & ENV
# ============================================================================================================
import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

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

# ให้เรียก LLM เร็วขึ้นหน่อย
SLEEP_BETWEEN_CALLS = (0.5, 1.0)

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# จำกัดจำนวน "ข่าวที่ส่งเข้า LLM" ต่อรัน
MAX_LLM_ITEMS = int(os.getenv("MAX_LLM_ITEMS", "15"))

bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

# รูป default สำหรับ hero ถ้าไม่มีรูปข่าวจริง ๆ
DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"


# ============================================================================================================
# PREFILTER KEYWORDS (ไม่ใช้ LLM)
# ============================================================================================================
ENERGY_KEYWORDS = [
    "gas",
    "natural gas",
    "lng",
    "lpg",
    "pipeline",
    "gas field",
    "gasfield",
    "oil",
    "crude",
    "upstream",
    "offshore",
    "onshore",
    "drilling",
    "rig",
    "exploration",
    "production",
    "fsru",
    "regasification",
    "lnt terminal",
    "gas supply",
    "gas export",
    "gas import",
    "strike",
    "walkout",
    "sanction",
    "embargo",
    "energy policy",
    "energy minister",
    "electricity price",
]

COUNTRY_PARTNER_KEYWORDS = [
    # ประเทศที่มีโครงการ PTTEP
    "thailand",
    "thai",
    "myanmar",
    "burma",
    "vietnam",
    "malaysia",
    "indonesia",
    "uae",
    "united arab emirates",
    "abu dhabi",
    "oman",
    "algeria",
    "mozambique",
    "australia",
    "brazil",
    "mexico",
    # ชื่อแหล่ง / โครงการสำคัญ
    "erawan",
    "bongkot",
    "arthit",
    "zawtika",
    "yadana",
    "yetagun",
    "rovuma",
    "ghasha",
    "montara",
    # ผู้ร่วมทุนหลัก
    "chevron",
    "exxon",
    "exxonmobil",
    "totalenergies",
    "shell",
    "bp",
    "eni",
    "sonatrach",
    "petrobras",
    "adnoc",
    "petronas",
]

def keyword_prefilter(news) -> bool:
    """
    กรองข่าวรอบแรกแบบไม่ใช้ LLM
    ถ้ามีคำพลังงาน หรือชื่อประเทศ/ผู้ร่วมทุนใน title+summary → ให้ผ่าน
    """
    text = (news.get("title", "") + " " + news.get("summary", "")).lower()

    if any(k in text for k in ENERGY_KEYWORDS):
        return True
    if any(k in text for k in COUNTRY_PARTNER_KEYWORDS):
        return True

    return False


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

        return urlunparse(
            p._replace(
                scheme=scheme,
                netloc=netloc,
                query=urlencode(new_q),
            )
        )
    except Exception:
        return (url or "").strip()


def get_sent_links_file(date=None):
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")


def load_sent_links():
    """
    โหลดลิงก์ข่าวที่เคยส่ง 'ในวันนี้' เพื่อกันส่งซ้ำในวันเดียวกันเท่านั้น
    """
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
    """
    แปลงข้อความ impact_reason เป็น list bullet สะอาด ๆ
    """
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
    """
    คืนค่า False ถ้า impact_text เป็นเพียงข้อความแนว
    'ยังไม่พบผลกระทบโดยตรงต่อโครงการของ PTTEP'
    """
    if not impact_text:
        return False

    t = impact_text.lower().replace(" ", "")
    patterns = [
        "ยังไม่พบผลกระทบโดยตรงต่อโครงการของpttep",
        "ไม่พบผลกระทบโดยตรงต่อโครงการของpttep",
        "ยังไม่พบผลกระทบต่อโครงการของpttep",
    ]
    for p in patterns:
        if p in t:
            return False

    return True


# ============================================================================================================
# ดึงรูปจากหน้าเว็บข่าว (og:image / twitter:image / <img> แรก)
# ============================================================================================================
def fetch_article_image(url: str) -> str:
    if not url:
        return ""

    try:
        r = S.get(url, timeout=TIMEOUT)
        if r.status_code >= 400:
            return ""

        html = r.text

        m = re.search(
            r'<meta[^>]+property=[\'"]og:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]',
            html,
            re.I,
        )
        if m:
            return m.group(1)

        m = re.search(
            r'<meta[^>]+name=[\'"]twitter:image[\'"][^>]+content=[\'"]([^\'"]+)[\'"]',
            html,
            re.I,
        )
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
PTT_CONTEXT = r"""
[บริบทกลุ่มธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของ ปตท.]

ภาพรวมธุรกิจ
- กลุ่มธุรกิจนี้รับผิดชอบการจัดหาพลังงานขั้นต้นของประเทศ
- ดูแลห่วงโซ่อุปทานก๊าซธรรมชาติ ตั้งแต่การสำรวจ ผลิต นำเข้า แปรรูป ขนส่ง และจำหน่าย
- สินค้าหลัก: ก๊าซธรรมชาติ (NG), ก๊าซปิโตรเลียมเหลว (LPG), ก๊าซธรรมชาติสำหรับยานยนต์ (NGV)
- นำเข้าและพัฒนาโครงสร้างพื้นฐานสำหรับก๊าซธรรมชาติเหลว (LNG) เพื่อเสริมความมั่นคงด้านพลังงานของประเทศ
"""

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


# ============================================================================================================
# GEMINI CALL WRAPPER
# ============================================================================================================
GEMINI_CALLS = 0


def call_gemini(prompt):
    global GEMINI_CALLS

    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError("เกินโควต้า Gemini ประจำวัน")

    last_error = None
    for i in range(1, MAX_RETRIES + 1):
        try:
            r = model.generate_content(prompt)
            GEMINI_CALLS += 1
            return r
        except Exception as e:
            last_error = e
            if (
                any(x in str(e) for x in ["429", "unavailable", "deadline", "503", "500"])
                and i < MAX_RETRIES
            ):
                time.sleep(5 * i)
                continue
            raise e

    raise last_error


# ============================================================================================================
# GEMINI TAG + FILTER (รวมสองอย่างในทีเดียว)
# ============================================================================================================
def gemini_tag_and_filter(news):
    schema = {
        "type": "object",
        "properties": {
            "is_relevant": {"type": "boolean"},
            "summary": {"type": "string"},
            "topic_type": {
                "type": "string",
                "enum": [
                    "supply_disruption",
                    "price_move",
                    "policy",
                    "investment",
                    "geopolitics",
                    "other",
                ],
            },
            "region": {
                "type": "string",
                "enum": ["global", "asia", "europe", "middle_east", "us", "other"],
            },
            "impact_reason": {"type": "string"},
            "country": {"type": "string"},
            "projects": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["is_relevant"],
    }

    prompt = f"""
{PTT_CONTEXT}
{PTTEP_PROJECTS_CONTEXT}

[พันธมิตร / ผู้ร่วมทุนที่พบบ่อย]
- Chevron, ExxonMobil, TotalEnergies, Shell, BP, ENI, Sonatrach, Petrobras,
  ADNOC, Petronas และบริษัทพลังงานแห่งชาติอื่น ๆ

บทบาทของคุณ: Analyst + News Screener ของ PTTEP

ขั้นตอนที่ 1: ตัดสิน is_relevant
ให้ is_relevant = true ถ้าข่าวนี้มีความเป็นไปได้อย่างมีนัยสำคัญ
ที่จะกระทบ "โครงการสำรวจและผลิต/ก๊าซ" ของ PTTEP หรือโครงการร่วมทุน
ผ่านช่องทางต่อไปนี้ เช่น:
- ข่าวพลังงาน (oil/gas/LNG/pipeline/upstream) ในประเทศที่มีโครงการของ PTTEP
- ข่าวการเมือง นโยบาย ภาษี สัมปทาน มาตรการคว่ำบาตร ความมั่นคง สงคราม ประท้วงแรงงาน
  ในประเทศที่มีโครงการของ PTTEP หรือประเทศของผู้ร่วมทุนหลัก
- ข่าวที่กระทบ supply / cost / schedule ของโครงการเหล่านี้

ถ้าเป็น downstream, EV, lifestyle, PR ฯลฯ ที่ไม่โยงกับ upstream/นโยบายพลังงานเลย → is_relevant = false

ถ้าไม่แน่ใจ ให้เอนเอียงไปทาง is_relevant = true
(ดีกว่าคัดทิ้งข่าวสำคัญ)

ขั้นตอนที่ 2: ถ้า is_relevant = true ให้เติมข้อมูลต่อไปนี้
- summary: สรุปข่าวสั้น ๆ ภาษาไทย 2–4 ประโยค
- topic_type, region: แท็กประเภทข่าว/ภูมิภาค
- impact_reason:
  * เขียนเฉพาะ "ผลกระทบต่อโครงการของ PTTEP" เป็น bullet หรือหลายบรรทัด
  * พยายามอ้างอิงชื่อประเทศ/บล็อก/โครงการใน context
  * ถ้า "ยังไม่พบผลกระทบโดยตรง" ให้เขียนแบบนั้นได้
- country: ประเทศหลักที่เกี่ยวข้อง (เช่น Thailand, Myanmar, US, Mozambique, UAE ฯลฯ)
- projects: รายชื่อโครงการของ PTTEP ที่เกี่ยวข้อง (เช่น ["G1/61", "Mozambique Area 1"])

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุปจาก RSS: {news['summary']}
ข้อมูลเพิ่มเติม: {news.get('detail','')}

ให้ตอบกลับเป็น JSON เท่านั้น ตาม schema นี้:
{json.dumps(schema, ensure_ascii=False)}
"""

    try:
        r = call_gemini(prompt)
        raw = (r.text or "").strip()

        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()

        return json.loads(raw)
    except Exception:
        return {"is_relevant": False}


# ============================================================================================================
# FETCH NEWS
# ============================================================================================================
NEWS_FEEDS = [
    ("Oilprice", "Energy", "https://oilprice.com/rss/main"),
    ("CleanTechnica", "Energy", "https://cleantechnica.com/feed/"),
    ("HydrogenFuelNews", "Energy", "https://www.hydrogenfuelnews.com/feed/"),
    ("Economist", "Economy", "https://www.economist.com/latest/rss.xml"),
    ("YahooFinance", "Economy", "https://finance.yahoo.com/news/rssindex"),
]


def fetch_news_window():
    now_local = datetime.now(bangkok_tz)

    # ช่วงเวลา 21:00 ของเมื่อวาน ถึง 06:00 ของวันนี้
    start = (now_local - timedelta(days=1)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )
    end = now_local.replace(hour=6, minute=0, second=0, microsecond=0)

    out = []

    for site, cat, url in NEWS_FEEDS:
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
                    out.append(
                        {
                            "site": site,
                            "category": cat,
                            "title": e.title,
                            "summary": getattr(e, "summary", ""),
                            "link": e.link,
                            "published": dt,
                            "date": dt.strftime("%d/%m/%Y %H:%M"),
                        }
                    )
        except Exception:
            pass

    # dedupe ตาม link
    uniq = []
    seen = set()
    for n in out:
        k = _normalize_link(n["link"])
        if k not in seen:
            seen.add(k)
            uniq.append(n)

    # เรียงจากข่าวใหม่ไปเก่า
    uniq.sort(key=lambda x: x["published"], reverse=True)
    return uniq


# ============================================================================================================
# FLEX MESSAGE
# ============================================================================================================
def create_flex(news_items):
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    for n in news_items:
        bullets = _impact_to_bullets(n.get("impact_reason", "-"))

        link = n.get("link") or ""
        if not (isinstance(link, str) and link.startswith(("http://", "https://"))):
            link = "https://www.google.com/search?q=energy+gas+news"

        img = n.get("image") or DEFAULT_ICON_URL
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL

        country_txt = (n.get("country") or "ไม่ระบุ").strip()
        projects = n.get("projects") or []
        if isinstance(projects, list):
            proj_txt = ", ".join(projects[:3]) if projects else "ไม่ระบุ"
        else:
            proj_txt = str(projects)

        body_contents = [
            {
                "type": "text",
                "text": n["title"],
                "weight": "bold",
                "size": "lg",
                "wrap": True,
            },
            {
                "type": "text",
                "text": f"🗓 {n['date']}",
                "size": "xs",
                "color": "#888888",
                "margin": "sm",
            },
            {
                "type": "text",
                "text": f"🌍 {n['site']}",
                "size": "xs",
                "color": "#448AFF",
                "margin": "xs",
            },
            {
                "type": "text",
                "text": f"โครงการ: {proj_txt} | ประเทศ: {country_txt}",
                "size": "xs",
                "color": "#555555",
                "margin": "sm",
                "wrap": True,
            },
        ]

        impact_box = {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": "ผลกระทบต่อโครงการ",
                    "size": "lg",
                    "weight": "bold",
                    "color": "#000000",
                }
            ]
            + [
                {
                    "type": "text",
                    "text": f"• {b}",
                    "wrap": True,
                    "size": "md",
                    "color": "#000000",
                    "weight": "bold",
                    "margin": "xs",
                }
                for b in bullets
            ],
        }

        body_contents.append(impact_box)

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": img,
                "size": "full",
                "aspectRatio": "16:9",
                "aspectMode": "cover",
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": body_contents,
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {
                            "type": "uri",
                            "label": "อ่านต่อ",
                            "uri": link,
                        },
                    }
                ],
            },
        }

        bubbles.append(bubble)

    return [
        {
            "type": "flex",
            "altText": f"ข่าว PTTEP {now_txt}",
            "contents": {"type": "carousel", "contents": bubbles},
        }
    ]


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

    # pre-filter แบบไม่ใช้ LLM
    candidates = [n for n in all_news if keyword_prefilter(n)]
    print("หลัง keyword pre-filter:", len(candidates))

    # จำกัดจำนวนที่ส่งเข้า LLM
    if len(candidates) > MAX_LLM_ITEMS:
        candidates = candidates[:MAX_LLM_ITEMS]
        print(f"จำกัดข่าวที่ส่งเข้า LLM เหลือ: {len(candidates)} (MAX_LLM_ITEMS)")

    tagged = []
    for idx, n in enumerate(candidates, 1):
        print(f"[{idx}/{len(candidates)}] LLM tag+filter: {n['title'][:80]}...")
        n["detail"] = n["title"] if len(n["summary"]) < 50 else ""

        tag = gemini_tag_and_filter(n)

        if not tag.get("is_relevant"):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        n["topic_type"] = tag.get("topic_type", "other")
        n["region"] = tag.get("region", "other")
        n["impact_reason"] = tag.get(
            "impact_reason", "ยังไม่พบผลกระทบโดยตรงต่อโครงการของ PTTEP"
        )
        n["summary_llm"] = (
            tag.get("summary")
            or n.get("summary")
            or n["title"]
        )
        n["country"] = tag.get("country", "")
        n["projects"] = tag.get("projects", [])

        if not has_meaningful_impact(n["impact_reason"]):
            time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
            continue

        tagged.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    print("จำนวนข่าวที่มีผลกระทบต่อโครงการจริง ๆ:", len(tagged))
    if not tagged:
        print("ไม่มีข่าวที่มีผลกระทบต่อโครงการอย่างชัดเจน")
        return

    selected = tagged[:10]

    sent = load_sent_links()
    final = [n for n in selected if _normalize_link(n["link"]) not in sent]
    print("หลังตัดของเก่า:", len(final))

    if not final:
        print("ไม่มีข่าวใหม่")
        return

    for n in final:
        img = fetch_article_image(n.get("link", ""))
        if not (isinstance(img, str) and img.startswith(("http://", "https://"))):
            img = DEFAULT_ICON_URL
        n["image"] = img
        time.sleep(0.3)

    msgs = create_flex(final)
    send_to_line(msgs)
    save_sent_links([n["link"] for n in final])

    print("เสร็จสิ้น")


# ============================================================================================================
if __name__ == "__main__":
    main()
