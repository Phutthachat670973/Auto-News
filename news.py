# news.py
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import random
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

# Groq (OpenAI-compatible) – optional, for "impact" only
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions").strip()
USE_LLM_IMPACT = os.getenv("USE_LLM_IMPACT", "1").strip().lower() in ["1", "true", "yes", "y"]

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "48"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "100"))

# If true: ต้อง match โครงการเท่านั้น ไม่ใช้ fallback ประเทศ
REQUIRE_PROJECT_MATCH = os.getenv("REQUIRE_PROJECT_MATCH", "false").strip().lower() in ["1", "true", "yes", "y"]

# LINE limits
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))  # safe (<=12)
MAX_MESSAGES_PER_RUN = int(os.getenv("MAX_MESSAGES_PER_RUN", "20"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]

# LLM batching / retry
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "900"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "8"))
LLM_MAX_RETRIES_429 = int(os.getenv("LLM_MAX_RETRIES_429", "8"))
LLM_BASE_BACKOFF = float(os.getenv("LLM_BASE_BACKOFF", "1.5"))

# sent links
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)


# =============================================================================
# PROJECT DB (ประเทศ/โครงการตามลิสต์ของคุณ)  ✅ ประเทศจะมีได้เฉพาะ key ใน dict นี้เท่านั้น
# =============================================================================
PROJECTS_BY_COUNTRY = {
    "Thailand": [
        "โครงการจี 1/61", "โครงการจี 2/61", "โครงการอาทิตย์", "Arthit",
        "โครงการเอส 1", "S1",
        "โครงการสัมปทาน 4", "Contract 4",
        "โครงการพีทีทีอีพี 1", "PTTEP 1",
        "โครงการบี 6/27",
        "โครงการแอล 22/43",
        "โครงการอี 5", "E5",
        "โครงการจี 4/43",
        "โครงการสินภูฮ่อม", "Sinphuhorm",
        "โครงการบี 8/32", "B8/32", "9A", "9เอ", "โครงการบี 8/32 และ 9เอ",
        "โครงการจี 4/48",
        "โครงการจี 12/48",
        "โครงการจี 1/65",
        "โครงการจี 3/65",
        "โครงการแอล 53/43", "โครงการแอล 54/43", "แอล 53/43", "แอล 54/43",
    ],
    "Myanmar": ["โครงการซอติก้า", "Zawtika", "โครงการยาดานา", "Yadana", "โครงการเมียนมา เอ็ม 3", "Myanmar M3"],
    "Malaysia": [
        "Malaysia SK309", "SK309", "Malaysia SK311", "SK311",
        "Malaysia Block H", "Block H",
        "Malaysia SK410B", "SK410B",
        "Malaysia SK417", "SK417",
        "Malaysia SK405B", "SK405B",
        "Malaysia SK438", "SK438",
        "Malaysia SK314A", "SK314A",
        "Malaysia SK325", "SK325",
        "Malaysia SB412", "SB412",
        "Malaysia Block K", "Block K", "Gumusut-Kakap",
    ],
    "Vietnam": ["โครงการเวียดนาม 16-1", "Vietnam 16-1", "16-1", "Block B", "48/95", "52/97", "9-2"],
    "Indonesia": ["โครงการนาทูน่า ซี เอ", "Natuna Sea A"],
    "Kazakhstan": ["โครงการดุงกา", "Dunga"],
    "Oman": ["Oman Block 61", "Block 61", "Oman Block 6", "PDO", "Oman Block 53", "Block 53", "Onshore Block 12", "Oman LNG", "Oman LNG Project"],
    "UAE": ["Abu Dhabi Offshore 1", "Abu Dhabi Offshore 2", "Abu Dhabi Offshore 3", "Ghasha", "Ghasha Concession", "ADNOC Gas Processing", "AGP"],
    "Algeria": ["433a", "416b", "Hassi Bir Rekaiz"],
    "Mozambique": ["Mozambique Area 1", "Mozambique LNG", "Area 1"],
    "Australia": ["PTTEP Australasia"],
    "Mexico": ["Mexico Block 12", "Block 12", "Mexico Block 29", "Block 29"],
}

# ผู้ร่วมทุน (ใส่เท่าที่คุณมีข้อมูล — ไม่มีไม่เป็นไร)
PARTNERS_BY_PROJECT = {
    "Zawtika": "PTTEP 80% (Operator), MOGE 20%",
    "Yadana": "PTTEP 62.96% (Operator), MOGE 37.04%",
    "Oman Block 61": "bp (Operator), OQ, PTTEP, PETRONAS",
    "Oman Block 6": "รัฐบาลโอมาน, Shell, TotalEnergies, PTTEP (Partex)",
    "Mozambique Area 1": "TotalEnergies (Operator), Mitsui, ENH, Bharat Petroleum, Oil India, ONGC Videsh, PTTEP",
    "Abu Dhabi Offshore 1": "Eni (Operator), PTTEP",
    "Abu Dhabi Offshore 2": "Eni (Operator), PTTEP",
    "Ghasha Concession": "ADNOC (+พันธมิตร), PTTEP",
    "Mexico Block 12": "Petronas (Operator), PTTEP (+พันธมิตร)",
    "Mexico Block 29": "Repsol (Operator), PTTEP",
}

ALLOWED_COUNTRIES = set(PROJECTS_BY_COUNTRY.keys())


# =============================================================================
# FEEDS
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

FEEDS = [
    ("GoogleNewsTH", "domestic", gnews_rss('(พลังงาน OR PDP OR "กกพ" OR ค่าไฟ OR "Direct PPA" OR ก๊าซ OR LNG OR นิวเคลียร์ OR SMR OR โดรน OR แท่นขุดเจาะ)', hl="th", gl="TH", ceid="TH:th")),
    ("GoogleNewsEN", "international", gnews_rss('(energy policy OR regulator OR power tariff OR "direct PPA" OR LNG OR gas OR "small modular reactor" OR SMR OR sanctions OR "national security" OR drilling OR offshore OR "project financing")', hl="en", gl="US", ceid="US:en")),
    ("Oilprice", "international", "https://oilprice.com/rss/main"),
    ("YahooFinance", "international", "https://finance.yahoo.com/news/rssindex"),
]


# =============================================================================
# UTIL
# =============================================================================
def now_tz() -> datetime:
    return datetime.now(TZ)

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    try:
        u = urlparse(url)
        return u._replace(fragment="").geturl()
    except Exception:
        return url

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""

def shorten_google_news_url(url: str) -> str:
    """
    ลดโอกาส LINE error: uri ต้อง <= 1000 chars
    - ถ้ามี query url= ดึงปลายทาง
    - ถ้าเป็น news.google.com/articles/... ลอง follow redirect 1 ครั้ง
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

            try:
                r = requests.get(url, allow_redirects=True, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                final = normalize_url(r.url)
                if final and len(final) < len(url):
                    return final
            except Exception:
                pass
    except Exception:
        pass
    return url

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

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

def cut(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"

def norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


# =============================================================================
# RSS PARSE
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
    seen = set()
    out = []
    for it in items:
        key = sha1(((it.get("title", "") + "||" + it.get("url", "")).lower()))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# =============================================================================
# MATCHING (DETERMINISTIC)
# =============================================================================
# Build alias regex per country/project
PROJECT_CANON = []  # list of (canon_project, country, [regex...])
for country, plist in PROJECTS_BY_COUNTRY.items():
    # พยายามทำ "ชื่อโครงการหลัก" เป็นชื่อที่ยาวกว่า ถ้ามี "โครงการ..." ให้ใช้เป็น canon
    # ที่เหลือถือเป็น alias
    # จัดกลุ่มง่าย ๆ: เอา alias ทั้งหมดไปใส่ matcher ของตัวเอง (canon = alias เอง)
    for p in plist:
        aliases = set()
        aliases.add(p)

        # ถ้ามีวงเล็บ (Zawtika) -> add Zawtika
        for m in re.findall(r"\(([^)]+)\)", p):
            m = m.strip()
            if m:
                aliases.add(m)

        # pattern แบบ จี 1/61, บี 8/32, แอล 53/43
        for m in re.findall(r"(จี|บี|แอล|อี)\s*([0-9]+)\s*/\s*([0-9]+)", p):
            t, a, b = m
            aliases.add(f"{t} {a}/{b}")
            aliases.add(f"{t}{a}/{b}")

        # Block / SK / Area
        low = p.lower()
        for m in re.findall(r"\b(block|sk|area)\s*([0-9]+[a-z]?)\b", low):
            aliases.add(f"{m[0]} {m[1]}")
            aliases.add(f"{m[0]}{m[1]}")

        # สร้าง regex list (ยาวก่อน)
        alias_list = sorted({norm(a) for a in aliases if len(norm(a)) >= 3}, key=len, reverse=True)
        regs = []
        for a in alias_list:
            esc = re.escape(a).replace(r"\ ", r"\s+")
            regs.append(re.compile(esc, re.IGNORECASE))
        PROJECT_CANON.append((p, country, regs))

def detect_project_and_country(title: str, summary: str):
    text = norm(f"{title} {summary}")
    for canon, country, regs in PROJECT_CANON:
        for rgx in regs:
            if rgx.search(text):
                return canon, country
    return "", ""

# Fallback country keywords (เฉพาะประเทศใน whitelist)
COUNTRY_KEYWORDS = {
    "Thailand": r"(thailand|ไทย|ประเทศไทย|กกพ|กฟผ|pdp|ค่าไฟ|direct ppa)",
    "Myanmar": r"(myanmar|เมียนมา|moge|yadana|zawtika)",
    "Malaysia": r"(malaysia|มาเลเซีย|\bsk\s*3\d{2}[a-z]?\b|\bsk\s*4\d{2}[a-z]?\b|petronas|gumusut|kakap)",
    "Vietnam": r"(vietnam|เวียดนาม|petrovietnam|block b|48/95|52/97|9-2|16-1)",
    "Indonesia": r"(indonesia|อินโดนีเซีย|natuna)",
    "Kazakhstan": r"(kazakhstan|คาซัคสถาน|dunga)",
    "Oman": r"(oman|โอมาน|muscat|\bpdo\b|\boq\b)",
    "UAE": r"(uae|united arab emirates|abu dhabi|dubai|adnoc|ghasha)",
    "Algeria": r"(algeria|แอลจีเรีย|hassi|bir rekaiz|433a|416b)",
    "Mozambique": r"(mozambique|โมซัมบิก|rovuma|area 1)",
    "Australia": r"(australia|ออสเตรเลีย|australasia)",
    "Mexico": r"(mexico|เม็กซิโก|pemex|block 12|block 29)",
}
COUNTRY_REGEX = {k: re.compile(v, re.IGNORECASE) for k, v in COUNTRY_KEYWORDS.items() if k in ALLOWED_COUNTRIES}

def detect_country(title: str, summary: str) -> str:
    text = norm(f"{title} {summary}")
    for c, rgx in COUNTRY_REGEX.items():
        if rgx.search(text):
            return c
    return ""


# =============================================================================
# LLM IMPACT (batch) – optional
# =============================================================================
def call_groq_with_retry(messages, temperature=0.25, max_tokens=900):
    if not GROQ_API_KEY:
        raise RuntimeError("Missing GROQ_API_KEY")
    payload = {"model": GROQ_MODEL, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}

    for attempt in range(LLM_MAX_RETRIES_429 + 1):
        r = requests.post(
            GROQ_ENDPOINT,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if r.status_code == 429:
            wait = (LLM_BASE_BACKOFF ** min(attempt + 1, 8)) + random.uniform(0.0, 0.8)
            print(f"[LLM] 429 Too Many Requests -> backoff {wait:.1f}s (attempt {attempt+1})")
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()

    raise RuntimeError("LLM rate-limited too long (429)")

def llm_impacts_batch(batch_items):
    """
    batch_items: [{id,title,summary,country,project,mode}]
    mode: "project" or "country"
    """
    # no LLM -> fallback
    if (not USE_LLM_IMPACT) or (not GROQ_API_KEY):
        out = {}
        for x in batch_items:
            if x.get("mode") == "project":
                out[x["id"]] = "• อาจกระทบแผนงาน/ต้นทุน/ความเสี่ยงของโครงการ ควรติดตามรายละเอียดจากหน่วยงานที่เกี่ยวข้องอย่างใกล้ชิด"
            else:
                out[x["id"]] = "• อาจกระทบภาพรวมสภาพแวดล้อมการลงทุน/กฎระเบียบในประเทศนี้ ซึ่งอาจส่งผลต่อโครงการในประเทศดังกล่าว"
        return out

    sys = (
        "คุณเป็นผู้ช่วยสรุปผลกระทบข่าวต่อโครงการ/ประเทศแบบสุภาพ กระชับ "
        "ห้ามเดาประเทศใหม่ และห้ามอ้างโครงการที่ไม่ได้ให้มา"
    )
    user_payload = {"items": batch_items}
    user = f"""
เขียนผลกระทบ (impact) เป็น bullet เดียว เริ่มด้วย • ความยาวไม่เกิน 1 ประโยค
ส่งกลับเป็น JSON เท่านั้น:
{{"impacts":[{{"id":"1","impact":"• ..."}}]}}

ข้อมูล:
{json.dumps(user_payload, ensure_ascii=False)}
""".strip()

    content = call_groq_with_retry(
        [{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.25,
        max_tokens=LLM_MAX_TOKENS,
    )

    content = re.sub(r"^```json\s*", "", content, flags=re.I).strip()
    content = re.sub(r"^```\s*", "", content, flags=re.I).strip()
    content = re.sub(r"\s*```$", "", content).strip()

    out = {}
    try:
        data = json.loads(content)
        arr = data.get("impacts") or []
        for x in arr:
            _id = str(x.get("id", "")).strip()
            imp = (x.get("impact") or "").strip()
            if _id:
                if not imp.startswith("•"):
                    imp = "• " + imp.lstrip("-• ").strip()
                out[_id] = cut(imp, 220)
    except Exception:
        pass

    # fill missing
    for x in batch_items:
        if x["id"] not in out:
            out[x["id"]] = "• อาจมีผลกระทบต่อแผนงาน/ความเสี่ยง ควรติดตามความคืบหน้าอย่างใกล้ชิด"
    return out


# =============================================================================
# LINE FLEX
# =============================================================================
def line_headers():
    return {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

def send_line_message(message_obj):
    if DRY_RUN:
        print("=== DRY_RUN LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type": "flex", "altText": message_obj.get("altText", "")}]}, ensure_ascii=False))
        return True

    url = "https://api.line.me/v2/bot/message/broadcast"
    payload = {"messages": [message_obj]}
    r = requests.post(url, headers=line_headers(), json=payload, timeout=30)
    print(f"Send -> LINE status: {r.status_code}")
    if r.status_code >= 400:
        print("Response:", r.text[:2000])
        return False
    return True

def flex_bubble(item):
    title = cut(item.get("title") or "", 120)
    published_dt = item.get("published_dt")
    dt_str = published_dt.strftime("%d/%m/%Y %H:%M") if published_dt else ""
    src = item.get("source_domain") or item.get("feed") or ""

    country = item.get("country") or ""
    project = item.get("project") or ""
    impact = item.get("impact") or ""
    partners = item.get("partners") or ""
    hints = item.get("project_hints") or []

    url = shorten_google_news_url(item.get("url") or "")
    if len(url) > 1000:
        url = ""  # กัน LINE 400

    body = [
        {"type": "text", "text": title, "weight": "bold", "wrap": True, "size": "md"},
        {"type": "text", "text": f"{dt_str}  |  {src}", "wrap": True, "size": "xs", "color": "#888888"},
        {"type": "text", "text": f"ประเทศ: {country}", "wrap": True, "size": "sm"},
    ]

    if project:
        body.append({"type": "text", "text": f"โครงการ: {cut(project, 160)}", "wrap": True, "size": "sm"})
    else:
        body.append({"type": "text", "text": "โครงการ: ไม่พบชื่อโครงการในหัวข้อ/สรุป", "wrap": True, "size": "sm"})

    if partners:
        body.append({"type": "text", "text": f"ผู้ร่วมทุน: {cut(partners, 220)}", "wrap": True, "size": "sm"})

    if hints:
        body.append({"type": "text", "text": f"โครงการในประเทศนี้: {cut(', '.join(hints), 220)}", "wrap": True, "size": "sm"})

    if impact:
        body.append({"type": "text", "text": cut(impact, 220), "wrap": True, "size": "sm"})

    bubble = {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}}

    if url:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [{"type": "button", "style": "primary", "height": "sm", "action": {"type": "uri", "label": "อ่านข่าว", "uri": url}}],
        }

    return bubble

def flex_messages_from_bubbles(bubbles, alt_prefix="สรุปข่าวตามโครงการ"):
    msgs = []
    chunks = [bubbles[i:i + BUBBLES_PER_CAROUSEL] for i in range(0, len(bubbles), BUBBLES_PER_CAROUSEL)]
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

    # 1) fetch + parse + time window
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

    items = dedup_items(raw_items)
    print(f"จำนวนข่าวหลังตัดซ้ำ: {len(items)}")

    # 2) match project deterministically
    matched = []
    for it in items:
        proj, c = detect_project_and_country(it.get("title", ""), it.get("summary", ""))
        if proj and c in ALLOWED_COUNTRIES:
            it2 = dict(it)
            it2["country"] = c
            it2["project"] = proj
            it2["mode"] = "project"
            # partner info (best-effort)
            it2["partners"] = PARTNERS_BY_PROJECT.get(proj, "")
            matched.append(it2)

    print(f"จำนวนข่าวที่ match โครงการ (deterministic): {len(matched)}")

    # 3) fallback by country (only whitelist) if no project match
    if not matched:
        if REQUIRE_PROJECT_MATCH:
            print("ไม่มีข่าวที่ match โครงการในลิสต์วันนี้ (REQUIRE_PROJECT_MATCH=true)")
            return

        fallback = []
        for it in items:
            c = detect_country(it.get("title", ""), it.get("summary", ""))
            if not c or c not in ALLOWED_COUNTRIES:
                continue

            sample_projects = PROJECTS_BY_COUNTRY.get(c, [])[:3]

            it2 = dict(it)
            it2["country"] = c
            it2["project"] = ""  # แสดงว่าไม่เจอชื่อโครงการ
            it2["mode"] = "country"
            it2["project_hints"] = sample_projects
            fallback.append(it2)

        matched = fallback
        print(f"จำนวนข่าวที่ fallback ด้วยประเทศ: {len(matched)}")

        if not matched:
            print("ไม่มีข่าวที่เข้า whitelist ประเทศของโครงการในลิสต์วันนี้")
            return

    # 4) generate impacts (batch)
    batch = []
    for idx, it in enumerate(matched, start=1):
        batch.append({
            "id": str(idx),
            "title": cut(it.get("title", ""), 180),
            "summary": cut(re.sub(r"\s+", " ", it.get("summary", "") or ""), 500),
            "country": it.get("country", ""),
            "project": it.get("project", "") or "N/A",
            "mode": it.get("mode", "project"),
        })

    impacts = {}
    for i in range(0, len(batch), LLM_BATCH_SIZE):
        sub = batch[i:i + LLM_BATCH_SIZE]
        out = llm_impacts_batch(sub)
        impacts.update(out)

    for idx, it in enumerate(matched, start=1):
        it["impact"] = impacts.get(str(idx), "• ควรติดตามความคืบหน้าอย่างใกล้ชิด")

    # 5) build bubbles + send (split carousel)
    bubbles = [flex_bubble(it) for it in matched]
    messages = flex_messages_from_bubbles(bubbles, alt_prefix="สรุปข่าวตามโครงการ")

    ok_any = False
    for msg in messages:
        print("=== LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type": "flex", "altText": msg["altText"]}]}, ensure_ascii=False))
        ok = send_line_message(msg)
        if ok:
            ok_any = True
        else:
            break

    # 6) mark sent links only if sent
    if ok_any:
        for it in matched:
            append_sent_link(it["url"])

if __name__ == "__main__":
    main()
