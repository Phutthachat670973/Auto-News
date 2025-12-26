# news.py
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, unquote

import requests
import feedparser
import pytz
from dateutil import parser as dateutil_parser

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# =============================================================================
# ENV / CONFIG
# =============================================================================
TZ = pytz.timezone(os.getenv("TZ", "Asia/Bangkok"))

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN")

# LLM (Groq OpenAI-compatible)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions").strip()

# Optional Gemini fallback (ถ้าคุณยังใช้)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip()

USE_LLM = os.getenv("USE_LLM", "1").strip() == "1"

# News window
WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "48"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "100"))

# LLM batching / limits
MAX_LLM_ITEMS = int(os.getenv("MAX_LLM_ITEMS", "200"))  # จะตัดหลัง dedup
SLEEP_BETWEEN_CALLS = float(os.getenv("SLEEP_BETWEEN_CALLS", "0.8"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "450"))

# LINE limits
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))  # ปลอดภัย
MAX_MESSAGES_PER_RUN = int(os.getenv("MAX_MESSAGES_PER_RUN", "20"))   # กันสแปม

# sent links
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"


# =============================================================================
# PROJECT DB (ประเทศ/โครงการตามไฟล์ที่ให้มา)
# =============================================================================
# NOTE: ประเทศที่อนุญาต = key ใน PROJECTS_BY_COUNTRY เท่านั้น
PROJECTS_BY_COUNTRY = {
    "ประเทศไทย": {
        "โครงการจี 1/61": "PTTEP 60% (Operator), Mubadala Energy 40%",
        "โครงการจี 2/61": "PTTEP 100% (Operator)",
        "โครงการอาทิตย์ (Arthit)": "PTTEP 80% (Operator), Chevron 16%, MOECO 4%",
        "โครงการเอส 1 (S1)": "PTTEP 100% (Operator)",
        "โครงการสัมปทาน 4 (Contract 4)": "PTTEP 60% + (กลุ่ม) Chevron (Operator) — ผู้ร่วมทุนรายอื่นไม่ระบุครบ",
        "โครงการพีทีทีอีพี 1 (PTTEP 1)": "PTTEP 100% (Operator)",
        "โครงการบี 6/27": "PTTEP 100% (Operator)",
        "โครงการแอล 22/43": "PTTEP 100% (Operator)",
        "โครงการอี 5 (E5)": "PTTEP 20% + ExxonMobil (Operator) — ผู้ร่วมทุนรายอื่นไม่ระบุครบ",
        "โครงการจี 4/43": "PTTEP 21.375% + Chevron (Operator) — ผู้ร่วมทุนรายอื่นไม่ระบุครบ",
        "โครงการสินภูฮ่อม (Sinphuhorm)": "PTTEP 55%, APICO LLC 35%, ExxonMobil 10%",
        "โครงการบี 8/32 และ 9เอ (B8/32 & 9A)": "Chevron 51.660% (Operator), PTTEP 25.000%, MOECO 16.706%, KrisEnergy 4.634%, Palang Sophon 2.000%",
        "โครงการจี 4/48": "PTTEP 5% + Chevron (Operator) — ผู้ร่วมทุนรายอื่นไม่ระบุครบ",
        "โครงการจี 12/48": "PTTEP 66.67% (Operator), TotalEnergies EP Thailand 33.33%",
        "โครงการจี 1/65": "PTTEP 100% (Operator)",
        "โครงการจี 3/65": "PTTEP 100% (Operator)",
        "โครงการแอล 53/43 และแอล 54/43": "PTTEP 100% (Operator)",
    },
    "เมียนมา": {
        "โครงการซอติก้า (Zawtika)": "PTTEP 80% (Operator), MOGE 20%",
        "โครงการยาดานา (Yadana)": "PTTEP 62.96% (Operator), MOGE 37.04%",
        "โครงการเมียนมา เอ็ม 3 (Myanmar M3)": "PTTEP 100% (Operator)",
    },
    "มาเลเซีย": {
        "Malaysia SK309 and SK311": "ไม่ระบุครบ (มีเพียง PTTEP 42–59.5% และ Operator: PTTEP)",
        "Malaysia Block H": "Petronas Carigali, Pertamina Malaysia E&P + PTTEP (Operator) — ไม่ระบุ % ครบ",
        "Malaysia SK410B": "KUFPEC, Petronas Carigali + PTTEP (Operator) — ไม่ระบุ % ครบ",
        "Malaysia SK417": "ไม่ระบุครบ (มีเพียง PTTEP 80%, Operator: PTTEP)",
        "Malaysia SK405B": "ไม่ระบุครบ (มีเพียง PTTEP 49.5%, Operator: PTTEP)",
        "Malaysia SK438": "ไม่ระบุครบ (มีเพียง PTTEP 80%, Operator: PTTEP)",
        "Malaysia SK314A": "ไม่ระบุครบ (มีเพียง PTTEP 59.5%, Operator: PTTEP)",
        "Malaysia SK325": "ไม่ระบุครบ (มีเพียง PTTEP 32.5%, Operator: Petronas Carigali)",
        "Malaysia SB412": "ไม่ระบุครบ (มีเพียง PTTEP 60%, Operator: PTTEP)",
        "Malaysia Block K (เช่น Gumusut-Kakap)": "Shell (Operator), ConocoPhillips, Petronas, PTTEP, ฯลฯ — ไม่ระบุ % ครบ",
    },
    "เวียดนาม": {
        "โครงการเวียดนาม 16-1": "ไม่ระบุชื่อผู้ร่วมทุนครบ (Operator: HL JOC)",
        "โครงการเวียดนาม บี และ 48/95": "ไม่ระบุครบ (Operator: Vietnam Oil and Gas Group)",
        "โครงการเวียดนาม 52/97": "ไม่ระบุครบ (Operator: Vietnam Oil and Gas Group)",
        "โครงการเวียดนาม 9-2": "ไม่ระบุชื่อผู้ร่วมทุนครบ (Operator: HV JOC)",
    },
    "อินโดนีเซีย": {
        "โครงการนาทูน่า ซี เอ (Natuna Sea A)": "ไม่ระบุครบ (Operator: Harbour Energy)",
    },
    "คาซัคสถาน": {
        "โครงการดุงกา (Dunga)": "Dunga Operating GmbH 60% (Operator), Oman Oil Company Limited 20%, PTTEP (Kazakhstan) 20%",
    },
    "โอมาน": {
        "Oman Block 61": "bp 40% (Operator), OQ 30%, PTTEP 20%, PETRONAS 10%",
        "Oman Block 6 (PDO)": "รัฐบาลโอมาน 60%, Shell 34%, TotalEnergies 4%, PTTEP (Partex) 2%",
        "Oman Block 53": "Occidental 47% (Operator), OQEP 20%, Indian Oil 17%, Liwa 15%, PTTEP 1%",
        "Oman Onshore Block 12": "ไม่ระบุครบ (มีเพียง PTTEP 20%, Operator: TotalEnergies)",
        "Oman LNG Project": "รัฐบาลโอมาน, Shell, TotalEnergies, Mitsubishi ฯลฯ — ไม่ระบุ % ครบ",
    },
    "UAE": {
        "Abu Dhabi Offshore 1": "Eni 70% (Operator), PTTEP 30%",
        "Abu Dhabi Offshore 2": "Eni 70% (Operator), PTTEP 30%",
        "Abu Dhabi Offshore 3": "ไม่ระบุครบ (ระบุว่าโครงสร้าง 70/30 ตามชุดเดียวกัน)",
        "Ghasha Concession": "PTTEP 10% + ADNOC (ผู้ถือสิทธิหลัก/Operator) (+มีการกล่าวถึง Eni) — ไม่ระบุ % ครบ",
        "ADNOC Gas Processing (AGP)": "PTTEP (ผ่าน Partex) 2% + ADNOC (Operator) — ไม่ระบุ % ครบ",
    },
    "แอลจีเรีย": {
        "โครงการแอลจีเรีย 433a และ 416b": "ไม่ระบุชื่อผู้ร่วมทุนครบ (Operator: GBRS)",
        "โครงการแอลจีเรีย ฮาสซิ เบียร์ เรกาอิซ (Hassi Bir Rekaiz)": "ไม่ระบุชื่อผู้ร่วมทุนครบ (Operator: GHBR)",
    },
    "โมซัมบิก": {
        "Mozambique Area 1 (Mozambique LNG)": "TotalEnergies 26.5% (Operator), Mitsui 20%, ENH 15%, Bharat Petroleum 10%, Oil India 10%, ONGC Videsh 10%, PTTEP 8.5%",
    },
    "ออสเตรเลีย": {
        "PTTEP Australasia": "PTTEP 100% (Operator)",
    },
    "เม็กซิโก": {
        "Mexico Block 12 (2.4)": "Petronas (Operator), PTTEP, Ophir — ไม่ระบุ % ครบ",
        "Mexico Block 29 (2.4)": "ไม่ระบุครบ (มีเพียง PTTEP 16.67%, Operator: Repsol)",
    },
}

# Flatten helpers
PROJECT_TO_COUNTRY = {}
PROJECT_TO_PARTNERS = {}
ALL_PROJECT_NAMES = []
for c, d in PROJECTS_BY_COUNTRY.items():
    for p, partners in d.items():
        PROJECT_TO_COUNTRY[p] = c
        PROJECT_TO_PARTNERS[p] = partners
        ALL_PROJECT_NAMES.append(p)

ALLOWED_COUNTRIES = set(PROJECTS_BY_COUNTRY.keys())


# =============================================================================
# FEEDS (กว้าง ๆ แล้วค่อยให้ LLM จับเข้าโครงการ)
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

FEEDS = [
    # Thai domestic (broad energy policy / regulator / electricity)
    ("GoogleNewsTH", "domestic", gnews_rss('(พลังงาน OR PDP OR "กกพ" OR ค่าไฟ OR "Direct PPA" OR ก๊าซ OR LNG OR นิวเคลียร์ OR SMR OR โดรน OR แท่นขุดเจาะ)', hl="th", gl="TH", ceid="TH:th")),
    # International (broad)
    ("GoogleNewsEN", "international", gnews_rss('(energy policy OR regulator OR power tariff OR "direct PPA" OR LNG OR gas OR "small modular reactor" OR SMR OR sanctions OR "national security" OR drilling OR offshore OR "project financing")', hl="en", gl="US", ceid="US:en")),
    # Oilprice
    ("Oilprice", "international", "https://oilprice.com/rss/main"),
    # Yahoo Finance (optional general macro/energy)
    ("YahooFinance", "international", "https://finance.yahoo.com/news/rssindex"),
]


# =============================================================================
# UTIL
# =============================================================================
def now_tz() -> datetime:
    return datetime.now(TZ)

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    # strip fragments
    try:
        u = urlparse(url)
        clean = u._replace(fragment="").geturl()
        return clean
    except Exception:
        return url

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def shorten_google_news_url(url: str) -> str:
    """
    พยายามทำให้ url สั้นลง (ลดโอกาสเกิน 1000 chars)
    - ถ้าเป็น Google News RSS redirect ที่มีพารามิเตอร์ url= ให้ดึงปลายทาง
    - ถ้าเป็น news.google.com/articles/... จะลอง follow redirect 1 ครั้ง
    """
    url = normalize_url(url)
    if not url:
        return url

    try:
        u = urlparse(url)
        if "news.google.com" in u.netloc:
            qs = parse_qs(u.query)
            if "url" in qs and qs["url"]:
                return normalize_url(unquote(qs["url"][0]))
            # Some google news links embed original in "article" params, try follow
            try:
                r = requests.get(url, allow_redirects=True, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                final = normalize_url(r.url)
                # keep if shorter and not empty
                if final and len(final) < len(url):
                    return final
            except Exception:
                pass
    except Exception:
        pass

    return url

def read_sent_links() -> set:
    sent = set()
    for fn in os.listdir(SENT_DIR):
        if not fn.endswith(".txt"):
            continue
        fp = os.path.join(SENT_DIR, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sent.add(line)
        except Exception:
            continue
    return sent

def append_sent_link(url: str):
    url = normalize_url(url)
    if not url:
        return
    fn = os.path.join(SENT_DIR, now_tz().strftime("%Y-%m-%d") + ".txt")
    with open(fn, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def in_time_window(published_dt: datetime, hours: int) -> bool:
    if not published_dt:
        return False
    return published_dt >= (now_tz() - timedelta(hours=hours))


# =============================================================================
# FETCH + PARSE RSS
# =============================================================================
def fetch_feed(name: str, section: str, url: str):
    d = feedparser.parse(url)
    entries = d.entries or []
    print(f"[FEED] {name}/{section} entries={len(entries)} url={url[:120]}...")
    return entries

def parse_entry(e, feed_name: str, section: str):
    title = (getattr(e, "title", "") or "").strip()
    link = (getattr(e, "link", "") or "").strip()
    summary = (getattr(e, "summary", "") or "").strip()
    published = getattr(e, "published", None) or getattr(e, "updated", None)

    try:
        published_dt = dateutil_parser.parse(published) if published else None
        if published_dt and published_dt.tzinfo is None:
            published_dt = TZ.localize(published_dt)
        if published_dt:
            published_dt = published_dt.astimezone(TZ)
    except Exception:
        published_dt = None

    return {
        "title": title,
        "url": normalize_url(link),
        "summary": summary,
        "published_dt": published_dt,
        "feed": feed_name,
        "section": section,
        "source_domain": domain_of(link),
    }

def dedup_items(items):
    """
    Dedup by normalized url + title hash (ง่าย ๆ แต่ได้ผล)
    """
    seen = set()
    out = []
    for it in items:
        key = sha1((it.get("title", "") + "||" + it.get("url", "")).lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# =============================================================================
# LLM (Groq / fallback)
# =============================================================================
def call_groq(messages, temperature=0.2, max_tokens=300):
    if not GROQ_API_KEY:
        raise RuntimeError("Missing GROQ_API_KEY")
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = requests.post(
        GROQ_ENDPOINT,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip()

def llm_classify_to_projects(item):
    """
    คืนค่า:
    {
      "projects":[{"name":"...", "evidence":"..."}],
      "impact":"...",
      "relevance":0-100
    }
    หรือ None ถ้าไม่เกี่ยว
    """
    title = item.get("title", "")[:300]
    summary = re.sub(r"\s+", " ", (item.get("summary", "") or ""))[:600]
    text = f"TITLE: {title}\nSUMMARY: {summary}"

    # ลด token: ส่งรายชื่อโครงการแบบบรรทัดเดียว
    project_list = "\n".join([f"- {p} | {PROJECT_TO_COUNTRY[p]}" for p in ALL_PROJECT_NAMES])

    sys = (
        "คุณเป็นผู้ช่วยคัดกรองข่าวให้เกี่ยวข้องกับ 'โครงการ' ในรายการเท่านั้น "
        "ห้ามเดาประเทศนอกลิสต์ และห้ามเลือกโครงการถ้าไม่มีคำยืนยันใน TITLE/SUMMARY"
    )

    user = f"""
ให้เลือกเฉพาะโครงการจากรายการนี้เท่านั้น:
{project_list}

กติกา:
1) เลือกโครงการได้ 0-3 โครงการที่ 'มีหลักฐานชัดเจน' ว่าเกี่ยวข้องใน TITLE/SUMMARY
2) field evidence ต้องเป็นคำ/วลีที่พบจริงใน TITLE/SUMMARY (ยกมาแบบสั้น)
3) ถ้าไม่เกี่ยวกับโครงการใดเลย ให้ projects เป็น [] และ relevance ต่ำ
4) impact ให้เป็น bullet เดียว (ประโยคเดียว) อธิบายผลกระทบต่อโครงการแบบสุภาพ กระชับ

คืนค่าเป็น JSON เท่านั้น ตามรูป:
{{
  "projects":[{{"name":"<project name>","evidence":"<phrase from text>"}}],
  "impact":"• ...",
  "relevance":0
}}

TEXT:
{text}
""".strip()

    if not USE_LLM:
        return None

    content = call_groq(
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.25,
        max_tokens=min(LLM_MAX_TOKENS, max(200, LLM_MAX_TOKENS)),
    )

    # Parse JSON (เผื่อมี ```json)
    content = content.strip()
    content = re.sub(r"^```json\s*", "", content, flags=re.I).strip()
    content = re.sub(r"^```\s*", "", content, flags=re.I).strip()
    content = re.sub(r"\s*```$", "", content).strip()

    try:
        data = json.loads(content)
    except Exception:
        return None

    projects = data.get("projects") or []
    relevance = int(data.get("relevance") or 0)
    impact = (data.get("impact") or "").strip()

    # ต้องเป็น list และ project ต้องอยู่ในลิสต์จริง
    valid = []
    hay = (title + "\n" + summary).lower()

    for p in projects[:3]:
        name = (p.get("name") or "").strip()
        evidence = (p.get("evidence") or "").strip()
        if not name or name not in PROJECT_TO_COUNTRY:
            continue
        if not evidence:
            continue
        # evidence ต้องพบจริงในข้อความ (กัน LLM เดา)
        if evidence.lower() not in hay:
            continue
        valid.append({"name": name, "evidence": evidence})

    # ถ้าไม่ match โครงการจริง -> ทิ้ง
    if not valid or relevance < 15:
        return None

    # impact ต้องเป็น bullet เดียว และไม่ยาวมาก
    if not impact.startswith("•"):
        impact = "• " + impact.lstrip("-• ").strip()
    if len(impact) > 220:
        impact = impact[:217].rstrip() + "..."

    return {
        "projects": valid,
        "impact": impact,
        "relevance": max(0, min(100, relevance)),
    }


# =============================================================================
# LINE FLEX
# =============================================================================
def line_headers():
    return {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

def send_line_message(message_obj):
    if DRY_RUN:
        print("=== DRY_RUN LINE PAYLOAD ===")
        print(json.dumps({"messages": [message_obj]}, ensure_ascii=False)[:2000])
        return True

    url = "https://api.line.me/v2/bot/message/broadcast"
    payload = {"messages": [message_obj]}
    r = requests.post(url, headers=line_headers(), json=payload, timeout=30)
    print(f"LINE status: {r.status_code}")
    if r.status_code >= 400:
        print("Response:", r.text[:2000])
        return False
    return True

def flex_bubble(item, llm):
    title = (item.get("title") or "").strip()
    published_dt = item.get("published_dt")
    dt_str = published_dt.strftime("%d/%m/%Y %H:%M") if published_dt else ""
    src = item.get("source_domain") or item.get("feed") or ""

    # projects + derive country strictly from project list
    projects = [p["name"] for p in llm["projects"]]
    # ถ้ามีหลายโครงการ ให้แสดงประเทศจากโครงการแรก (และเพิ่มหมายเหตุถ้าต่างประเทศ)
    country = PROJECT_TO_COUNTRY.get(projects[0], "")
    partners = PROJECT_TO_PARTNERS.get(projects[0], "")
    if len(projects) > 1:
        # รวมชื่อโครงการแบบสั้น
        proj_text = ", ".join(projects[:3])
    else:
        proj_text = projects[0]

    # URL shorten (แก้ปัญหา uri > 1000)
    url = shorten_google_news_url(item.get("url") or "")
    if len(url) > 1000:
        # fallback: ถ้ายาวเกินจริง ๆ ให้ทิ้งปุ่ม (กัน 400)
        url = ""

    # จำกัดความยาวข้อความบางส่วน
    def cut(s, n):
        s = (s or "").strip()
        return s if len(s) <= n else s[: n - 1].rstrip() + "…"

    title = cut(title, 120)
    proj_text = cut(proj_text, 120)
    partners = cut(partners, 170)
    impact = cut(llm.get("impact") or "", 220)

    body_contents = [
        {"type": "text", "text": title, "weight": "bold", "wrap": True, "size": "md"},
        {"type": "text", "text": f"{dt_str}  |  {src}", "wrap": True, "size": "xs", "color": "#888888"},
        {"type": "text", "text": f"ประเทศ: {country}", "wrap": True, "size": "sm"},
        {"type": "text", "text": f"โครงการ: {proj_text}", "wrap": True, "size": "sm"},
    ]

    # ใส่ผู้ร่วมทุนถ้ามี
    if partners:
        body_contents.append({"type": "text", "text": f"ผู้ร่วมทุน: {partners}", "wrap": True, "size": "sm"})

    # ใส่ผลกระทบ (bullet เดียว)
    if impact:
        body_contents.append({"type": "text", "text": impact, "wrap": True, "size": "sm"})

    bubble = {
        "type": "bubble",
        "size": "mega",
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body_contents},
    }

    # footer button ถ้า url สั้นพอ
    if url:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {"type": "uri", "label": "อ่านข่าว", "uri": url},
                }
            ],
        }

    return bubble

def flex_messages_from_bubbles(bubbles, alt_prefix="สรุปข่าวพลังงาน"):
    """
    แบ่งเป็นหลาย carousel เพื่อไม่ให้เกินลิมิต
    """
    msgs = []
    chunks = [bubbles[i:i+BUBBLES_PER_CAROUSEL] for i in range(0, len(bubbles), BUBBLES_PER_CAROUSEL)]
    date_tag = now_tz().strftime("%d/%m/%Y")
    for idx, ch in enumerate(chunks[:MAX_MESSAGES_PER_RUN], start=1):
        msgs.append({
            "type": "flex",
            "altText": f"{alt_prefix} {date_tag} ({idx}/{len(chunks)})",
            "contents": {"type": "carousel", "contents": ch},
        })
    return msgs


# =============================================================================
# MAIN
# =============================================================================
def main():
    sent = read_sent_links()

    # 1) fetch + parse + time window filter
    raw_items = []
    for name, section, url in FEEDS:
        entries = fetch_feed(name, section, url)
        kept = 0
        for e in entries[:MAX_PER_FEED]:
            it = parse_entry(e, name, section)
            if not it["title"] or not it["url"]:
                continue
            if it["url"] in sent:
                continue
            if it["published_dt"] and not in_time_window(it["published_dt"], WINDOW_HOURS):
                continue
            raw_items.append(it)
            kept += 1
        print(f"[FEED] kept_in_window={kept}")

    print(f"จำนวนข่าวดิบทั้งหมด: {len(raw_items)}")

    # 2) dedup
    items = dedup_items(raw_items)
    print(f"จำนวนข่าวหลังตัดซ้ำ: {len(items)}")

    # 3) cap
    items = items[:MAX_LLM_ITEMS]
    print(f"จำนวนข่าวที่จะประเมินด้วย LLM: {len(items)}")

    # 4) LLM filter -> only projects in list (ประเทศจะไม่หลุดลิสต์)
    passed = []
    groq_calls = 0

    for i, it in enumerate(items, start=1):
        llm = None
        try:
            llm = llm_classify_to_projects(it)
            groq_calls += 1
            time.sleep(SLEEP_BETWEEN_CALLS)
        except Exception as ex:
            # ถ้า LLM มีปัญหา ข้ามข่าวนั้นไป
            print(f"[LLM ERROR] {i}/{len(items)} {ex}")
            continue

        if not llm:
            continue

        # Safety: ประเทศต้องอยู่ใน allowed list (ได้จาก project)
        first_proj = llm["projects"][0]["name"]
        country = PROJECT_TO_COUNTRY.get(first_proj, "")
        if country not in ALLOWED_COUNTRIES:
            continue

        passed.append((it, llm))

    print(f"จำนวนข่าวผ่านเงื่อนไข (match โครงการเท่านั้น): {len(passed)}")
    print(f"เสร็จสิ้น (Groq calls: {groq_calls})")

    if not passed:
        print("ไม่มีข่าวที่ match โครงการในลิสต์วันนี้")
        return

    # 5) Build bubbles
    bubbles = []
    for it, llm in passed:
        bubbles.append(flex_bubble(it, llm))

    # 6) Send LINE (split carousels)
    messages = flex_messages_from_bubbles(bubbles, alt_prefix="สรุปข่าวตามโครงการ")
    ok_any = False
    for idx, msg in enumerate(messages, start=1):
        print("=== LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type": "flex", "altText": msg["altText"]}]}, ensure_ascii=False))
        ok = send_line_message(msg)
        if ok:
            ok_any = True
        else:
            # ถ้าตีกลับ ให้หยุดก่อนเพื่อไม่ยิงซ้ำเยอะ
            break

    # 7) Mark sent links (เฉพาะถ้าส่งสำเร็จอย่างน้อย 1 ข้อความ)
    if ok_any:
        for it, _ in passed:
            append_sent_link(it["url"])

if __name__ == "__main__":
    main()
