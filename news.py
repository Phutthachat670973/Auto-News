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

# Groq (OpenAI-compatible) – optional (ใช้เพื่อสรุป “บริบท+ผลกระทบ”)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions").strip()
USE_LLM_SUMMARY = os.getenv("USE_LLM_SUMMARY", "1").strip().lower() in ["1", "true", "yes", "y"]

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "48"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "100"))

# ถ้า true จะส่งเฉพาะข่าวที่ match ประเทศในลิสต์ประเทศโครงการเท่านั้น (กันประเทศมั่ว)
REQUIRE_COUNTRY_WHITELIST = os.getenv("REQUIRE_COUNTRY_WHITELIST", "true").strip().lower() in ["1", "true", "yes", "y"]

# LINE limits
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))  # safe (<=12)
MAX_MESSAGES_PER_RUN = int(os.getenv("MAX_MESSAGES_PER_RUN", "20"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]

# LLM batching / retry
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1100"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "8"))
LLM_MAX_RETRIES_429 = int(os.getenv("LLM_MAX_RETRIES_429", "8"))
LLM_BASE_BACKOFF = float(os.getenv("LLM_BASE_BACKOFF", "1.5"))

# sent links
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)


# =============================================================================
# PROJECT DB (ประเทศ/โครงการ)
# - แสดงบนการ์ด: “โครงการในประเทศนี้: ...” เท่านั้น (ตามที่คุณต้องการ)
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
    "Australia": ["PTTEP Australasia", "Australasia"],
    "Mexico": ["Mexico Block 12", "Block 12", "Mexico Block 29", "Block 29"],
}

ALLOWED_COUNTRIES = set(PROJECTS_BY_COUNTRY.keys())

# =============================================================================
# FEEDS (เพิ่ม “ต่างประเทศ” แบบล็อกประเทศที่มีโครงการของคุณ)
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

# ใช้ประเทศที่มีโครงการเป็นตัว “ล็อก” ให้ได้ข่าวต่างประเทศจริง ๆ
COUNTRY_EN_QUERY = "(" + " OR ".join([
    "Thailand", "Myanmar", "Malaysia", "Vietnam", "Indonesia", "Kazakhstan",
    "Oman", "United Arab Emirates", "UAE", "Algeria", "Mozambique", "Australia", "Mexico"
]) + ")"

ENERGY_EN_QUERY = "(" + " OR ".join([
    "energy policy", "regulator", "power tariff", "electricity tariff", "direct PPA",
    "LNG", "natural gas", "gas", "oil", "crude", "offshore", "drilling",
    "small modular reactor", "SMR", "nuclear", "sanctions", "national security",
    "project financing", "FID", "export terminal", "pipeline"
]) + ")"

FEEDS = [
    # Thai
    ("GoogleNewsTH", "domestic", gnews_rss(
        '(พลังงาน OR PDP OR "กกพ" OR ค่าไฟ OR "Direct PPA" OR ก๊าซ OR LNG OR นิวเคลียร์ OR SMR OR โดรน OR แท่นขุดเจาะ)',
        hl="th", gl="TH", ceid="TH:th"
    )),
    # International (ล็อกประเทศที่คุณมีโครงการ)
    ("GoogleNewsEN", "international", gnews_rss(
        f"{COUNTRY_EN_QUERY} AND {ENERGY_EN_QUERY}",
        hl="en", gl="US", ceid="US:en"
    )),
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
# COUNTRY DETECTION (whitelist only)
# =============================================================================
COUNTRY_KEYWORDS = {
    "Thailand": r"(thailand|ไทย|ประเทศไทย|กกพ|กฟผ|pdp|ค่าไฟ|direct ppa)",
    "Myanmar": r"(myanmar|เมียนมา|moge|yangon|naypyidaw)",
    "Malaysia": r"(malaysia|มาเลเซีย|petronas|sabah|sarawak|\bsk\s*3\d{2}[a-z]?\b|\bsk\s*4\d{2}[a-z]?\b)",
    "Vietnam": r"(vietnam|เวียดนาม|petrovietnam|hanoi|ho chi minh|block b|48/95|52/97|9-2|16-1)",
    "Indonesia": r"(indonesia|อินโดนีเซีย|jakarta|natuna)",
    "Kazakhstan": r"(kazakhstan|คาซัคสถาน|astana|dunga)",
    "Oman": r"(oman|โอมาน|muscat|\bpdo\b|\boq\b)",
    "UAE": r"(uae|united arab emirates|abu dhabi|dubai|adnoc|ghasha)",
    "Algeria": r"(algeria|แอลจีเรีย|hassi|bir rekaiz|433a|416b)",
    "Mozambique": r"(mozambique|โมซัมบิก|maputo|rovuma|area 1)",
    "Australia": r"(australia|ออสเตรเลีย|perth|australasia)",
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
# LLM SUMMARY (บริบท + ผลกระทบ) – batch + retry 429
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

def llm_context_impact_batch(batch_items):
    """
    batch_items: [{id,title,summary,country,projects_hint}]
    return id -> (bullet1, bullet2)
    """
    # fallback without LLM
    if (not USE_LLM_SUMMARY) or (not GROQ_API_KEY):
        out = {}
        for x in batch_items:
            out[x["id"]] = (
                "• บริบท: ข่าวพลังงาน/นโยบาย/โครงสร้างพื้นฐานมีการอัปเดตที่อาจเปลี่ยนทิศทางตลาดหรือกฎระเบียบ",
                "• ผลกระทบ: อาจกระทบต้นทุน/ความเสี่ยง/แผนงานของโครงการในประเทศนี้ ควรติดตามความคืบหน้าอย่างใกล้ชิด",
            )
        return out

    sys = (
        "คุณเป็นผู้ช่วยสรุปข่าวพลังงานให้เข้าใจบริบทแบบสุภาพ กระชับ "
        "ห้ามเดาประเทศอื่นนอกเหนือจากที่ระบุใน field country "
        "และห้ามอ้างชื่อโครงการนอกเหนือจาก projects_hint"
    )

    user = f"""
ทำสรุปข่าวแต่ละรายการเป็น 2 bullet:
1) '• บริบท: ...' อธิบายว่าเกิดอะไรขึ้น/ใครทำอะไร/เรื่องนี้เกี่ยวกับอะไร (1 ประโยค)
2) '• ผลกระทบ: ...' อธิบายผลต่อโครงการในประเทศนี้ (1 ประโยค) อ้างอิง projects_hint ได้

ส่งกลับเป็น JSON เท่านั้น:
{{"items":[{{"id":"1","context":"• บริบท: ...","impact":"• ผลกระทบ: ..."}}]}}

ข้อมูล:
{json.dumps({"items": batch_items}, ensure_ascii=False)}
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
        arr = data.get("items") or []
        for x in arr:
            _id = str(x.get("id", "")).strip()
            c = (x.get("context") or "").strip()
            imp = (x.get("impact") or "").strip()
            if _id:
                if not c.startswith("•"):
                    c = "• " + c.lstrip("-• ").strip()
                if not imp.startswith("•"):
                    imp = "• " + imp.lstrip("-• ").strip()
                out[_id] = (cut(c, 220), cut(imp, 240))
    except Exception:
        pass

    for x in batch_items:
        if x["id"] not in out:
            out[x["id"]] = (
                "• บริบท: ข่าวพลังงาน/นโยบาย/ตลาดมีการอัปเดตที่สำคัญ",
                "• ผลกระทบ: อาจกระทบต้นทุน/ความเสี่ยง/แผนงานของโครงการในประเทศนี้ ควรติดตามอย่างใกล้ชิด",
            )
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
    hints = item.get("project_hints") or []
    context = item.get("context") or ""
    impact = item.get("impact") or ""

    url = shorten_google_news_url(item.get("url") or "")
    if len(url) > 1000:
        url = ""  # กัน LINE 400

    body = [
        {"type": "text", "text": title, "weight": "bold", "wrap": True, "size": "md"},
        {"type": "text", "text": f"{dt_str}  |  {src}", "wrap": True, "size": "xs", "color": "#888888"},
        {"type": "text", "text": f"ประเทศ: {country}", "wrap": True, "size": "sm"},
    ]

    # ✅ ตามที่คุณต้องการ: แสดง “เฉพาะโครงการในประเทศนี้”
    if hints:
        body.append({"type": "text", "text": f"โครงการในประเทศนี้: {cut(', '.join(hints), 240)}", "wrap": True, "size": "sm"})

    if context:
        body.append({"type": "text", "text": cut(context, 220), "wrap": True, "size": "sm"})
    if impact:
        body.append({"type": "text", "text": cut(impact, 240), "wrap": True, "size": "sm"})

    bubble = {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}}

    if url:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [{"type": "button", "style": "primary", "height": "sm", "action": {"type": "uri", "label": "อ่านข่าว", "uri": url}}],
        }

    return bubble

def flex_messages_from_bubbles(bubbles, alt_prefix="สรุปข่าวตามประเทศ/โครงการ"):
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

    # 2) classify to country (whitelist only)
    matched = []
    for it in items:
        c = detect_country(it.get("title", ""), it.get("summary", ""))
        if REQUIRE_COUNTRY_WHITELIST and (not c or c not in ALLOWED_COUNTRIES):
            continue
        if not c:
            continue

        # project hints: แสดงเฉพาะ “โครงการในประเทศนี้” (เอา 3-5 โครงการแรก)
        hints = PROJECTS_BY_COUNTRY.get(c, [])[:5]

        it2 = dict(it)
        it2["country"] = c
        it2["project_hints"] = hints
        matched.append(it2)

    print(f"จำนวนข่าวที่เข้า whitelist ประเทศของโครงการ: {len(matched)}")
    if not matched:
        print("ไม่มีข่าวที่เข้า whitelist ประเทศของโครงการในลิสต์วันนี้")
        return

    # 3) summary (บริบท + ผลกระทบ) batch
    batch = []
    for idx, it in enumerate(matched, start=1):
        batch.append({
            "id": str(idx),
            "title": cut(it.get("title", ""), 180),
            "summary": cut(re.sub(r"\s+", " ", it.get("summary", "") or ""), 650),
            "country": it.get("country", ""),
            "projects_hint": it.get("project_hints", [])[:5],
        })

    summaries = {}
    for i in range(0, len(batch), LLM_BATCH_SIZE):
        sub = batch[i:i + LLM_BATCH_SIZE]
        out = llm_context_impact_batch(sub)
        summaries.update(out)

    for idx, it in enumerate(matched, start=1):
        c, imp = summaries.get(str(idx), ("", ""))
        it["context"] = c
        it["impact"] = imp

    # 4) Build & send LINE
    bubbles = [flex_bubble(it) for it in matched]
    messages = flex_messages_from_bubbles(bubbles)

    ok_any = False
    for msg in messages:
        print("=== LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type": "flex", "altText": msg["altText"]}]}, ensure_ascii=False))
        ok = send_line_message(msg)
        if ok:
            ok_any = True
        else:
            break

    # 5) mark sent links only if sent
    if ok_any:
        for it in matched:
            append_sent_link(it["url"])


if __name__ == "__main__":
    main()
