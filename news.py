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

# Groq (OpenAI-compatible)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions").strip()
USE_LLM_IMPACT = os.getenv("USE_LLM_IMPACT", "1").strip() == "1"

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "48"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "100"))

# LINE limits
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))  # max 12 แต่เผื่อปลอดภัย
MAX_MESSAGES_PER_RUN = int(os.getenv("MAX_MESSAGES_PER_RUN", "20"))

# sent links
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

DRY_RUN = os.getenv("DRY_RUN", "0").strip() == "1"

# LLM limits / retry
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "900"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "8"))  # 1 call ต่อ 8 ข่าว
LLM_MAX_RETRIES_429 = int(os.getenv("LLM_MAX_RETRIES_429", "8"))
LLM_BASE_BACKOFF = float(os.getenv("LLM_BASE_BACKOFF", "1.5"))


# =============================================================================
# PROJECT DB (ประเทศ/โครงการตามที่คุณให้ — ประเทศจะ “มีได้เท่านี้เท่านั้น”)
# =============================================================================
# หมายเหตุ: คุณสามารถเติม/แก้รายการนี้ให้ตรง 100% ตามไฟล์คุณได้
PROJECTS_BY_COUNTRY = {
    "ประเทศไทย": {
        "โครงการจี 1/61": "PTTEP 60% (Operator), Mubadala Energy 40%",
        "โครงการจี 2/61": "PTTEP 100% (Operator)",
        "โครงการอาทิตย์ (Arthit)": "PTTEP 80% (Operator), Chevron 16%, MOECO 4%",
        "โครงการเอส 1 (S1)": "PTTEP 100% (Operator)",
        "โครงการสินภูฮ่อม (Sinphuhorm)": "PTTEP 55%, APICO LLC 35%, ExxonMobil 10%",
        "โครงการบี 8/32 และ 9เอ (B8/32 & 9A)": "Chevron 51.660% (Operator), PTTEP 25.000%, MOECO 16.706%, KrisEnergy 4.634%, Palang Sophon 2.000%",
        "โครงการจี 12/48": "PTTEP 66.67% (Operator), TotalEnergies EP Thailand 33.33%",
        "โครงการแอล 53/43 และแอล 54/43": "PTTEP 100% (Operator)",
    },
    "เมียนมา": {
        "โครงการซอติก้า (Zawtika)": "PTTEP 80% (Operator), MOGE 20%",
        "โครงการยาดานา (Yadana)": "PTTEP 62.96% (Operator), MOGE 37.04%",
    },
    "โอมาน": {
        "Oman Block 61": "bp 40% (Operator), OQ 30%, PTTEP 20%, PETRONAS 10%",
        "Oman Block 6 (PDO)": "รัฐบาลโอมาน 60%, Shell 34%, TotalEnergies 4%, PTTEP (Partex) 2%",
        "Oman Block 53": "Occidental 47% (Operator), OQEP 20%, Indian Oil 17%, Liwa 15%, PTTEP 1%",
        "Oman Onshore Block 12": "PTTEP 20% (ไม่ระบุรายอื่นครบ)",
        "Oman LNG Project": "รัฐบาลโอมาน, Shell, TotalEnergies, Mitsubishi ฯลฯ (ไม่ระบุ % ครบ)",
    },
    "UAE": {
        "Abu Dhabi Offshore 1": "Eni 70% (Operator), PTTEP 30%",
        "Abu Dhabi Offshore 2": "Eni 70% (Operator), PTTEP 30%",
        "Ghasha Concession": "PTTEP + ADNOC (ไม่ระบุ % ครบ)",
        "ADNOC Gas Processing (AGP)": "PTTEP (ผ่าน Partex) + ADNOC (ไม่ระบุ % ครบ)",
    },
    "โมซัมบิก": {
        "Mozambique Area 1 (Mozambique LNG)": "TotalEnergies (Operator), Mitsui, ENH, Bharat Petroleum, Oil India, ONGC Videsh, PTTEP ฯลฯ",
    },
    "เม็กซิโก": {
        "Mexico Block 12 (2.4)": "Petronas (Operator), PTTEP, Ophir (ไม่ระบุ % ครบ)",
        "Mexico Block 29 (2.4)": "Repsol (Operator), PTTEP 16.67% (ไม่ระบุครบ)",
    },
}

PROJECT_TO_COUNTRY = {}
PROJECT_TO_PARTNERS = {}
ALL_PROJECTS = []
for c, d in PROJECTS_BY_COUNTRY.items():
    for p, partners in d.items():
        PROJECT_TO_COUNTRY[p] = c
        PROJECT_TO_PARTNERS[p] = partners
        ALL_PROJECTS.append(p)

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
    ลดปัญหา LINE: uri ต้อง <= 1000 ตัวอักษร
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

            # follow redirect 1 ครั้ง (ถ้าสั้นลง)
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


# =============================================================================
# PARSE RSS
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
# PROJECT MATCHING (DETERMINISTIC) — ประเทศจะไม่มั่ว เพราะมาจากโครงการเท่านั้น
# =============================================================================
def _norm_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s)
    return s

def _aliases_from_project_name(p: str):
    aliases = set()
    raw = p.strip()

    # 1) ชื่อเต็ม
    aliases.add(raw)

    # 2) เอาข้างในวงเล็บ เช่น (Zawtika), (Arthit), (B8/32 & 9A)
    for m in re.findall(r"\(([^)]+)\)", raw):
        m = m.strip()
        if m:
            aliases.add(m)

    # 3) ดึง pattern แบบ จี 1/61, บี 8/32, แอล 22/43, อี 5
    for m in re.findall(r"(จี|บี|แอล|อี)\s*([0-9]+)\s*/\s*([0-9]+)", raw):
        t, a, b = m
        aliases.add(f"{t} {a}/{b}")
        aliases.add(f"{t}{a}/{b}")
    # 9A / 9เอ
    if "9เอ" in raw or "9a" in raw.lower():
        aliases.add("9เอ")
        aliases.add("9a")

    # 4) Block / SK / Offshore / Area
    lowered = raw.lower()
    for m in re.findall(r"\b(block|sk|area)\s*([0-9]+[a-z]?)\b", lowered):
        aliases.add(f"{m[0]} {m[1]}")
        aliases.add(f"{m[0]}{m[1]}")

    # 5) คีย์เวิร์ดสำคัญ
    for kw in ["zawtika", "yadana", "sinphuhorm", "ghasha", "mozambique", "australasia", "pdo", "natuna", "dunga", "adnoc", "abu dhabi"]:
        if kw in lowered:
            aliases.add(kw)

    # ทำให้เป็นรูป normalize
    out = []
    for a in aliases:
        a2 = _norm_text(a)
        a2 = a2.replace("โครงการ", "").strip()
        if a2 and len(a2) >= 3:
            out.append(a2)
    # เอา alias สั้นมากออกกันมั่ว
    out = [x for x in out if len(x) >= 3]
    return sorted(set(out), key=len, reverse=True)

PROJECT_MATCHERS = {}  # project -> list[regex]
for proj in ALL_PROJECTS:
    aliases = _aliases_from_project_name(proj)
    patterns = []
    for a in aliases:
        # escape แล้วทำให้ space ยืดหยุ่น
        esc = re.escape(a)
        esc = esc.replace(r"\ ", r"\s+")
        patterns.append(re.compile(esc, re.IGNORECASE))
    PROJECT_MATCHERS[proj] = patterns

def detect_projects(title: str, summary: str, max_projects: int = 3):
    text = _norm_text(f"{title} {summary}")
    hits = []
    for proj, pats in PROJECT_MATCHERS.items():
        for pat in pats:
            if pat.search(text):
                hits.append(proj)
                break
        if len(hits) >= max_projects:
            break
    return hits


# =============================================================================
# LLM (Impact only) — batch + retry 429
# =============================================================================
def call_groq_with_retry(messages, temperature=0.25, max_tokens=900):
    if not GROQ_API_KEY:
        raise RuntimeError("Missing GROQ_API_KEY")

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    for attempt in range(LLM_MAX_RETRIES_429 + 1):
        r = requests.post(
            GROQ_ENDPOINT,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )

        if r.status_code == 429:
            # exponential backoff + jitter
            wait = (LLM_BASE_BACKOFF ** min(attempt + 1, 8)) + random.uniform(0.0, 0.8)
            print(f"[LLM] 429 Too Many Requests -> backoff {wait:.1f}s (attempt {attempt+1})")
            time.sleep(wait)
            continue

        r.raise_for_status()
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()

    raise RuntimeError("LLM rate-limited too long (429)")

def llm_generate_impacts_batch(batch_items):
    """
    batch_items: list of dict {id,title,summary,project,country,partners}
    return dict id -> impact_bullet
    """
    if not USE_LLM_IMPACT:
        return {x["id"]: "• มีความเป็นไปได้ที่จะส่งผลต่อความเสี่ยง/ต้นทุน/แผนงานของโครงการ ควรติดตามความคืบหน้าอย่างใกล้ชิด" for x in batch_items}

    sys = (
        "คุณเป็นผู้ช่วยสรุปผลกระทบข่าวต่อโครงการพลังงานแบบสุภาพและกระชับ "
        "ห้ามเดาโครงการหรือประเทศเพิ่ม และห้ามใส่ประเทศอื่นนอกเหนือจากที่ให้มา"
    )

    user_payload = {
        "items": batch_items,
        "rules": {
            "format": "JSON only",
            "impact": "bullet เดียว เริ่มด้วย • ความยาวไม่เกิน 1 ประโยค",
        }
    }

    user = f"""
จงเขียน impact ต่อ 'โครงการ' ตามข้อมูลที่ให้มาเท่านั้น
ส่งกลับเป็น JSON รูปแบบ:
{{"impacts": [{{"id":"1","impact":"• ..."}}]}}

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

    # fallback for missing ids
    for x in batch_items:
        if x["id"] not in out:
            out[x["id"]] = "• มีความเป็นไปได้ที่จะส่งผลต่อความเสี่ยง/ต้นทุน/แผนงานของโครงการ ควรติดตามความคืบหน้าอย่างใกล้ชิด"
    return out


# =============================================================================
# LINE FLEX
# =============================================================================
def line_headers():
    return {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}

def send_line_message(message_obj):
    if DRY_RUN:
        print("=== DRY_RUN LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type": "flex", "altText": message_obj.get("altText","")}]} , ensure_ascii=False))
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

    project = item["project"]
    country = item["country"]
    partners = PROJECT_TO_PARTNERS.get(project, "")

    url = shorten_google_news_url(item.get("url") or "")
    if len(url) > 1000:
        url = ""  # กัน LINE 400

    body = [
        {"type": "text", "text": title, "weight": "bold", "wrap": True, "size": "md"},
        {"type": "text", "text": f"{dt_str}  |  {src}", "wrap": True, "size": "xs", "color": "#888888"},
        {"type": "text", "text": f"ประเทศ: {country}", "wrap": True, "size": "sm"},
        {"type": "text", "text": f"โครงการ: {cut(project, 150)}", "wrap": True, "size": "sm"},
    ]

    if partners:
        body.append({"type": "text", "text": f"ผู้ร่วมทุน: {cut(partners, 200)}", "wrap": True, "size": "sm"})

    impact = item.get("impact") or ""
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

    # 2) detect project deterministically (ประเทศ = ประเทศของโครงการเท่านั้น)
    matched = []
    for it in items:
        projs = detect_projects(it.get("title",""), it.get("summary",""), max_projects=1)
        if not projs:
            continue
        proj = projs[0]
        country = PROJECT_TO_COUNTRY.get(proj, "")
        if country not in ALLOWED_COUNTRIES:
            continue

        it2 = dict(it)
        it2["project"] = proj
        it2["country"] = country
        matched.append(it2)

    print(f"จำนวนข่าวที่ match โครงการ (deterministic): {len(matched)}")
    if not matched:
        print("ไม่มีข่าวที่ match โครงการในลิสต์วันนี้")
        return

    # 3) LLM impacts (batch)
    impacts = {}
    if USE_LLM_IMPACT and GROQ_API_KEY:
        batch = []
        for idx, it in enumerate(matched, start=1):
            batch.append({
                "id": str(idx),
                "title": cut(it.get("title",""), 180),
                "summary": cut(re.sub(r"\s+"," ", it.get("summary","") or ""), 500),
                "project": it["project"],
                "country": it["country"],
                "partners": cut(PROJECT_TO_PARTNERS.get(it["project"], ""), 220),
            })
        # split batch
        for i in range(0, len(batch), LLM_BATCH_SIZE):
            sub = batch[i:i+LLM_BATCH_SIZE]
            out = llm_generate_impacts_batch(sub)
            impacts.update(out)

    # attach impacts
    for idx, it in enumerate(matched, start=1):
        it["impact"] = impacts.get(str(idx), "• มีความเป็นไปได้ที่จะส่งผลต่อความเสี่ยง/ต้นทุน/แผนงานของโครงการ ควรติดตามความคืบหน้าอย่างใกล้ชิด")

    # 4) Build bubbles + send
    bubbles = [flex_bubble(it) for it in matched]
    messages = flex_messages_from_bubbles(bubbles)

    ok_any = False
    for msg in messages:
        print("=== LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type":"flex","altText": msg["altText"]}]}, ensure_ascii=False))
        ok = send_line_message(msg)
        if ok:
            ok_any = True
        else:
            break

    # 5) Mark sent links only if sent
    if ok_any:
        for it in matched:
            append_sent_link(it["url"])

if __name__ == "__main__":
    main()
