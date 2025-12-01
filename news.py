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
SLEEP_BETWEEN_CALLS = (6.0, 7.0)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)


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
    for i in range(2):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        p = get_sent_links_file(d)
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
        return ["ไม่ระบุผลกระทบ"]
    raw = text.strip()
    parts = []
    for line in raw.splitlines():
        if line.strip():
            parts.append(line.strip("•- —\t "))
    if len(parts) <= 1:
        tmp = re.split(r"[。．\.]\s*", raw)
        parts = [t.strip("•- ") for t in tmp if t.strip()]
    return parts or ["ไม่ระบุผลกระทบ"]


# ============================================================================================================
# CONTEXT
# ============================================================================================================
PTT_CONTEXT = """
โฟกัสเฉพาะ “กลุ่มธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของ ปตท.” เท่านั้น
ไม่ต้องแยกบริษัทลูก เช่น PTTEP / PTTLNG / PTTGL / PTTNGD / TTM

ขอบเขตธุรกิจหลัก:
- การจัดหาก๊าซธรรมชาติและ LNG
- การนำเข้า LNG
- ระบบท่อก๊าซธรรมชาติของประเทศ
- โรงแยกก๊าซธรรมชาติ (GSP)
- การส่งก๊าซให้โรงไฟฟ้า อุตสาหกรรม และ NGV

ถือว่าข่าว “เกี่ยวข้องอย่างมีนัยสำคัญ” หากเข้าอย่างน้อยหนึ่งข้อ:
1) ราคาก๊าซ / LNG / น้ำมันผันผวนรุนแรง
2) ซัพพลายก๊าซสะดุด เช่น ท่อก๊าซเสีย, โรงแยกหยุดผลิต, เหตุการณ์ geopolitical กระทบ supply
3) โครงสร้างพื้นฐานใหม่ด้านก๊าซ: LNG terminal, FSRU, Pipeline, Gas Processing
4) นโยบายรัฐด้านพลังงานที่กระทบความมั่นคงก๊าซหรือต้นทุนก๊าซของประเทศ

ไม่ต้องสนใจข่าว downstream, ปิโตรเคมี, EV, PR, การตลาด
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
            if any(x in str(e) for x in ["429", "unavailable", "deadline", "503", "500"]) and i < MAX_RETRIES:
                time.sleep(5 * i)
                continue
            raise e
    raise last_error


# ============================================================================================================
# FILTER → ข่าวเกี่ยวข้องหรือไม่
# ============================================================================================================
def llm_filter(news):
    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: News Screener ของ ปตท.
หน้าที่: ตอบว่า “ข่าวนี้เกี่ยวข้องกับกลุ่มธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของ ปตท. หรือไม่”

ข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เพิ่มเติม: {news.get('detail','')}

ตอบเพียงคำเดียว:
- ใช่
- ไม่ใช่
"""
    try:
        r = call_gemini(prompt)
        ans = (r.text or "").strip().replace("\n", "")
        return ans.startswith("ใช่")
    except Exception:
        return False


# ============================================================================================================
# TAG ข่าว (ไม่มีบริษัทลูก)
# ============================================================================================================
def gemini_tag(news):
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "topic_type": {
                "type": "string",
                "enum": [
                    "supply_disruption", "price_move", "policy",
                    "investment", "geopolitics", "other"
                ]
            },
            "region": {
                "type": "string",
                "enum": ["global", "asia", "europe", "middle_east", "us", "other"]
            },
            "impact_reason": {"type": "string"}
        },
        "required": ["summary", "topic_type", "region", "impact_reason"]
    }

    prompt = f"""
{PTT_CONTEXT}

วิเคราะห์ข่าวต่อไปนี้และตอบ JSON เท่านั้น:

หัวข้อ: {news['title']}
สรุป RSS: {news['summary']}
ข้อมูลเพิ่ม: {news.get('detail','')}

เงื่อนไข:
- summary = เขียนสรุปข่าวสั้น ๆ ภาษาไทย
- topic_type = เลือกจาก enum
- region = เลือกจาก enum
- impact_reason = เขียนผลกระทบต่อ “กลุ่มธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของ ปตท.”
  *เขียนเป็น bullet point หรือหลายบรรทัด*

ตอบเป็น JSON ตาม schema นี้:
{json.dumps(schema, ensure_ascii=False)}
"""

    try:
        r = call_gemini(prompt)
        raw = (r.text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$","", raw).strip()
        return json.loads(raw)
    except Exception:
        return {
            "summary": news['summary'],
            "topic_type": "other",
            "region": "other",
            "impact_reason": "-"
        }


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
                    out.append({
                        "site": site,
                        "category": cat,
                        "title": e.title,
                        "summary": getattr(e, "summary", ""),
                        "link": e.link,
                        "published": dt,
                        "date": dt.strftime("%d/%m/%Y %H:%M")
                    })
        except Exception:
            pass

    uniq = []
    seen = set()
    for n in out:
        k = _normalize_link(n['link'])
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    return uniq


# ============================================================================================================
# GROUP NEWS
# ============================================================================================================
def group_news(news_list, min_size=3):
    buckets = {}
    for n in news_list:
        key = (n.get("topic_type"), n.get("region"))
        buckets.setdefault(key, []).append(n)

    out = []
    for (topic, region), items in buckets.items():
        if len(items) >= min_size:
            items_sorted = sorted(items, key=lambda x: x['published'], reverse=True)
            anchor = items_sorted[0]
            out.append({
                "is_group": True,
                "topic_type": topic,
                "region": region,
                "news_items": items_sorted,
                "title": anchor['title'],
                "site": "หลายแหล่งข่าว",
                "category": anchor['category'],
                "date": anchor['date'],
                "published": anchor['published'],
                "link": anchor['link']
            })
        else:
            out.extend(items)
    return out


# ============================================================================================================
# SUMMARIZE GROUP
# ============================================================================================================
def gemini_group_summary(group):
    block = "\n".join([f"- {n['title']}: {n['summary']}" for n in group['news_items']])

    prompt = f"""
{PTT_CONTEXT}

สรุปภาพรวมของกลุ่มข่าวต่อไปนี้:
{block}

ตอบ JSON:
{{
 "summary": "<สรุป>",
 "impact_reason": "<ผลกระทบเป็น bullet>"
}}
"""

    try:
        r = call_gemini(prompt)
        raw = (r.text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?","", raw).strip()
            raw = re.sub(r"```$","", raw).strip()
        return json.loads(raw)
    except Exception:
        return {"summary": "สรุปไม่ได้", "impact_reason": "-"}


# ============================================================================================================
# FLEX MESSAGE
# ============================================================================================================
def create_flex(news_items):
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    for n in news_items:
        bullets = _impact_to_bullets(n.get("impact_reason", "-"))

        # ตรวจลิงก์ ถ้าไม่ใช่ http(s) ให้ใช้ fallback
        link = n.get("link") or ""
        if not (isinstance(link, str) and link.startswith(("http://", "https://"))):
            link = "https://www.google.com/search?q=energy+gas+news"

        impact_box = {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": "ผลกระทบต่อกลุ่ม ปตท.",
                    "size": "lg",
                    "weight": "bold",
                    "color": "#D32F2F"
                }
            ] + [
                {
                    "type": "text",
                    "text": f"• {b}",
                    "wrap": True,
                    "size": "md",
                    "color": "#C62828",
                    "weight": "bold",
                    "margin": "xs"
                }
                for b in bullets
            ]
        }

        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": n['title'], "weight": "bold", "size": "lg", "wrap": True},
                    {"type": "text", "text": f"🗓 {n['date']}", "size": "xs", "color": "#888888", "margin": "sm"},
                    {"type": "text", "text": f"🌍 {n['site']}", "size": "xs", "color": "#448AFF", "margin": "xs"},
                    {
                        "type": "text",
                        "text": f"ประเภท: {n['topic_type']} | ภูมิภาค: {n['region']}",
                        "size": "xs",
                        "color": "#555555",
                        "margin": "sm"
                    },
                    impact_box
                ]
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
                            "uri": link
                        }
                    }
                ]
            }
        }
        bubbles.append(bubble)

    return [{
        "type": "flex",
        "altText": f"ข่าว ปตท. {now_txt}",
        "contents": {
            "type": "carousel",
            "contents": bubbles
        }
    }]


# ============================================================================================================
# BROADCAST LINE (เพิ่ม debug payload + response body)
# ============================================================================================================
def send_to_line(messages):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    for i, msg in enumerate(messages, 1):
        payload = {"messages": [msg]}

        # DEBUG: แสดง payload ที่จะส่งให้ LINE
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

    filtered = []
    for n in all_news:
        n['detail'] = n['title'] if len(n['summary']) < 50 else ''
        if llm_filter(n):
            filtered.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    if not filtered:
        print("ไม่มีข่าวเกี่ยวข้อง")
        return

    print("วิเคราะห์ข่าวด้วย LLM...")
    tagged = []
    for n in filtered:
        tag = gemini_tag(n)
        n['topic_type'] = tag['topic_type']
        n['region'] = tag['region']
        n['impact_reason'] = tag['impact_reason']
        tagged.append(n)
        time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))

    grouped = group_news(tagged)

    for g in grouped:
        if g.get("is_group"):
            meta = gemini_group_summary(g)
            g['impact_reason'] = meta['impact_reason']

    selected = grouped[:10]

    sent = load_sent_links()
    final = [n for n in selected if _normalize_link(n['link']) not in sent]

    if not final:
        print("ไม่มีข่าวใหม่")
        return

    msgs = create_flex(final)
    send_to_line(msgs)
    save_sent_links([n['link'] for n in final])

    print("เสร็จสิ้น")


# ============================================================================================================
if __name__ == "__main__":
    main()
