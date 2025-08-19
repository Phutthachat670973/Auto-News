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

# ========================= CONFIG =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# โควต้า/รีทไร
GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))
MAX_RETRIES = 6
SLEEP_BETWEEN_CALLS = (6.0, 7.0)  # ~10 RPM
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)

# HTTP session (เบา เร็ว)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

# ========== SENT LINKS: กันส่งซ้ำ (วันนี้กับเมื่อวาน) ==========
SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

def _normalize_link(url: str) -> str:
    try:
        p = urlparse(url)
        netloc = p.netloc.lower()
        scheme = (p.scheme or "https").lower()
        # ล้างพารามิเตอร์ติดตามทั่วไป
        bad_keys = {"fbclid","gclid","ref","ref_","mc_cid","mc_eid"}
        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            if k.startswith("utm_") or k in bad_keys:
                continue
            q.append((k, v))
        return urlunparse(p._replace(scheme=scheme, netloc=netloc, query=urlencode(q)))
    except Exception:
        return (url or "").strip()

def get_sent_links_file(date=None):
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")

def load_sent_links_today_yesterday():
    sent_links = set()
    for i in range(2):  # วันนี้ & เมื่อวาน
        date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        path = get_sent_links_file(date)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    url = _normalize_link(line.strip())
                    if url:
                        sent_links.add(url)
    return sent_links

def save_sent_links(new_links, date=None):
    path = get_sent_links_file(date)
    with open(path, "a", encoding="utf-8") as f:
        for url in new_links:
            f.write(_normalize_link(url) + "\n")

# ========================= FEEDS =========================
news_sources = {
    "Oilprice": {"url": "https://oilprice.com/rss/main", "category": "Energy", "site": "Oilprice"},
    "CleanTechnica": {"url": "https://cleantechnica.com/feed/", "category": "Energy", "site": "CleanTechnica"},
    "HydrogenFuelNews": {"url": "https://www.hydrogenfuelnews.com/feed/", "category": "Energy", "site": "Hydrogen Fuel News"},
    "Economist": {"url": "https://www.economist.com/latest/rss.xml", "category": "Economy", "site": "Economist"},
    "YahooFinance": {"url": "https://finance.yahoo.com/news/rssindex", "category": "Economy", "site": "Yahoo Finance"},
}

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"
GEMINI_CALLS = 0

# ========================= Helpers =========================
COLON_RX = re.compile(r"[：﹕꞉︓⦂⸿˸]")

def _normalize_colons(text: str) -> str:
    return COLON_RX.sub(":", text or "")

def _polish_impact_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*,\s*,", ", ", text)
    text = re.sub(r"\s*,\s*\.", ".", text)
    return text.strip()

def fetch_article_image(url: str) -> str:
    """ดึง og:image / twitter:image อย่างเบา ๆ ด้วย requests"""
    try:
        r = S.get(url, timeout=TIMEOUT)
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

# ========================= Upstream Context =========================
PTT_CONTEXT = """
[Department Context — Upstream Business Group Subsidiary Management Department]

เป้าหมาย: เฝ้าระวังและประเมินข่าวที่กระทบ upstream (PTTEP ก่อน) และบริษัทย่อยอื่นเมื่อมีผลสะท้อนกลับชัดเจน

เกณฑ์ข่าวสำคัญ:
• Brent/WTI/JKM/TTF ผันผวนโดดเด่น
• เหตุขัดข้องการผลิต/ท่อ/แหล่งสำคัญ
• นโยบาย/สัมปทาน/PSC/ภาษี upstream
• ดีล M&A/FID/ฟาร์มอิน-ฟาร์มเอาท์/ค้นพบเชิงพาณิชย์
• OPEC+/สงคราม/ภัยธรรมชาติ

สิ่งที่ไม่นับ: ข่าว downstream/PR/EV ที่ไม่โยงกลไกสู่ upstream
"""

# ========================= Gemini Wrapper =========================
def call_gemini(prompt, max_retries=MAX_RETRIES):
    global GEMINI_CALLS
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError(f"ถึงงบ Gemini ประจำวันแล้ว ({GEMINI_CALLS}/{GEMINI_DAILY_BUDGET})")
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = model.generate_content(prompt)
            GEMINI_CALLS += 1
            return resp
        except Exception as e:
            err_str = str(e)
            # ถ้าเจอ rate limit/ชั่วคราว ลอง backoff
            if attempt < max_retries and any(x in err_str for x in ["429","exhausted","temporarily","unavailable","deadline","500","503"]):
                time.sleep(min(60, 5 * attempt))
                continue
            last_error = e
            if attempt < max_retries:
                time.sleep(3 * attempt)
            else:
                raise last_error
    raise last_error

# ========================= LLM Prompts =========================
def llm_ptt_subsidiary_impact_filter(news):
    """
    **ต้องตอบกลับ "ใช่" หรือ "ไม่ใช่" เท่านั้น**
    - ใช่: ข่าวกระทบ upstream โดยตรง หรือโยงกลไกสู่ PTTEP (หรือ PTTLNG/PTTGL/PTTNGD เมื่อสะท้อนกลับสู่ upstream)
    - ไม่ใช่: ข่าว downstream/PR ที่ไม่โยงกลไก
    """
    prompt = f'''
{PTT_CONTEXT}
บทบาท: News Screener for Upstream Business Group Subsidiary Management Department
ตอบเพียงคำเดียว: "ใช่" หรือ "ไม่ใช่"
- ใช่: ข่าวกระทบ upstream โดยตรง หรือโยงกลไกสู่ PTTEP (หรือ PTTLNG/PTTGL/PTTNGD เมื่อสะท้อนกลับสู่ upstream)
- ไม่ใช่: ข่าว downstream/PR ที่ไม่โยงกลไก

ข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เนื้อหา: {news.get('detail','')}
'''
    try:
        resp = call_gemini(prompt)
        ans = (resp.text or "").strip().replace("\n", "")
        return ans.startswith("ใช่")
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        return False

def gemini_summary_and_score(news):
    """
    คืนค่า JSON ตาม schema:
      {
        "summary": str,
        "score": int (1..5),
        "score_breakdown": [{"points": int, "reason": str}, ...]  # ผลรวม points ต้อง = score
        "impact_companies": ["PTTEP","PTTLNG","PTTGL","PTTNGD"] (<= 2 ราย หรือ [] ถ้าไม่เกี่ยว)
        "impact_reason": str  # อธิบายกลไกเฉพาะ เช่น supply↓ → Brent↑ → margin upstream PTTEP↑
      }
    """
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "score": {"type": "integer"},
            "score_breakdown": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"points": {"type": "integer"}, "reason": {"type": "string"}},
                    "required": ["points", "reason"]
                }
            },
            "impact_companies": {
                "type": "array",
                "items": {"type": "string", "enum": ["PTTEP","PTTLNG","PTTGL","PTTNGD"]}
            },
            "impact_reason": {"type": "string"}
        },
        "required": ["summary","score","score_breakdown","impact_companies","impact_reason"]
    }

    prompt = f"""
{PTT_CONTEXT}

บทบาท: Analyst for Upstream Business Group Subsidiary Management Department

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เนื้อหา: {news.get('detail','')}

จงตอบกลับ **เฉพาะ JSON** ตาม schema:
{json.dumps(schema, ensure_ascii=False)}

ข้อกำหนด:
- summary: กระชับ ระบุเหตุการณ์+กลไก (เช่น supply↓ → Brent↑ → margin PTTEP↑)
- score: 1–5, materiality upstream-first
- score_breakdown: รวม points = score
- impact_companies: ไม่เกิน 2, PTTEP ก่อนเสมอถ้าเกี่ยว supply/price/PSC/production
- impact_reason: อธิบายกลไกเฉพาะเจาะจง ห้ามใช้ถ้อยคำกว้าง ๆ
"""
    try:
        resp = call_gemini(prompt)
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        print("[WARN] JSON parse fail:", e)
        return {
            "summary": "ไม่สามารถแปลง JSON ได้",
            "score": 3,
            "score_breakdown": [{"points": 2, "reason": "default"}, {"points": 1, "reason": "fallback"}],
            "impact_companies": [],
            "impact_reason": "-",
        }

# ========================= Logic =========================
def is_ptt_related_from_output(impact_companies) -> bool:
    return bool(impact_companies)

def fetch_news_9pm_to_6am():
    now_local = datetime.now(bangkok_tz)
    start_time = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end_time = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    print("ช่วง fetch:", start_time, "ถึง", end_time)
    all_news = []
    for _, info in news_sources.items():
        try:
            feed = feedparser.parse(info["url"])
            for entry in feed.entries:
                # เวลาเผยแพร่
                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if not pub_str and getattr(entry, "published_parsed", None):
                    t = entry.published_parsed
                    pub_dt = datetime(*t[:6], tzinfo=pytz.UTC).astimezone(bangkok_tz)
                else:
                    if not pub_str:
                        continue
                    pub_dt = dateutil_parser.parse(pub_str)
                    if pub_dt.tzinfo is None:
                        pub_dt = pytz.UTC.localize(pub_dt)
                    pub_dt = pub_dt.astimezone(bangkok_tz)

                if not (start_time <= pub_dt <= end_time):
                    continue

                # เนื้อหาเบื้องต้น
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                link = getattr(entry, "link", "")
                title = getattr(entry, "title", "-")

                all_news.append({
                    "site": info["site"],
                    "category": info["category"],
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": pub_dt,
                    "date": pub_dt.strftime("%d/%m/%Y %H:%M"),
                })
        except Exception as e:
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")

    # de-dup link
    seen = set()
    uniq = []
    for n in all_news:
        key = _normalize_link(n.get("link", ""))
        if key and key not in seen:
            seen.add(key)
            uniq.append(n)

    print("ข่าวที่อยู่ในช่วง (unique):", len(uniq))
    return uniq

def rank_candidates(news_list, use_keyword_boost=False):
    ranked = []
    for n in news_list:
        age_h = (now - n["published"]).total_seconds() / 3600.0
        recency = max(0.0, (72.0 - min(72.0, age_h))) / 72.0 * 3.0
        cat_w = {"Energy": 3.0, "Economy": 2.0, "Politics": 1.0}.get(n["category"], 1.0)
        length = min(len(n.get("summary","")) / 500.0, 1.0)
        score = recency + cat_w + length
        ranked.append((score, n))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in ranked]

def create_flex_message(news_items):
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def join_companies(codes):
        codes = codes or []
        return ", ".join(codes) if codes else "ไม่มีระบุ"

    bubbles = []
    for item in news_items:
        bd = item.get("score_breakdown", [])
        bd_lines = [f"- {x.get('points', 0)} คะแนน: {x.get('reason', '-')}\n" for x in bd]
        bd_clean = "".join(bd_lines).strip() or "-"

        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm",
        }

        img = item.get("image") or DEFAULT_ICON_URL
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL

        body_contents = [
            {"type": "text","text": item.get("title", "-"),"weight": "bold","size": "lg","wrap": True,"color": "#111111"},
            {
                "type": "box","layout": "horizontal","margin": "sm",
                "contents": [
                    {"type": "text","text": f"🗓 {item.get('date','-')}", "size": "xs","color": "#aaaaaa","flex": 5},
                    {"type": "text","text": f"📌 {item.get('category','')}", "size": "xs","color": "#888888","align": "end","flex": 5}
                ]
            },
            {"type": "text","text": f"🌍 {item.get('site','')}", "size": "xs","color": "#448AFF","margin": "sm"},
            impact_line,
            {"type": "text","text": item.get("gemini_summary") or "ไม่พบสรุปข่าว","size": "md","wrap": True,"margin": "md","color": "#1A237E","weight": "bold"},
            {
                "type": "box","layout": "vertical","margin": "lg",
                "contents": [
                    {"type": "text","text": "ผลกระทบ / เหตุผลคะแนน","weight": "bold","size": "lg","color": "#D32F2F"},
                    {"type": "text","text": (item.get("gemini_reason") or "-"),"size": "md","wrap": True,"color": "#C62828","weight": "bold"},
                    {"type": "text","text": f"คะแนนรวม: {item.get('gemini_score','-')} คะแนน","size": "lg","wrap": True,"color": "#000000","weight": "bold"},
                    {"type": "text","text": bd_clean,"size": "sm","wrap": True,"color": "#8E0000","weight": "bold"}
                ]
            }
        ]

        bubble = {
            "type": "bubble","size": "mega",
            "hero": {"type": "image","url": img,"size": "full","aspectRatio": "16:9","aspectMode": "cover"},
            "body": {"type": "box","layout": "vertical","spacing": "md","contents": body_contents},
            "footer": {
                "type": "box","layout": "vertical","spacing": "sm",
                "contents": [
                    {"type": "text","text": "หมายเหตุ: การวิเคราะห์ทั้งหมดอยู่ในช่วงทดสอบ ขออภัยในความไม่สะดวก","size": "xs","color": "#FF0000","wrap": True,"margin": "md","weight": "regular"},
                    {"type": "button","style": "primary","color": "#1DB446","action": {"type": "uri","label": "อ่านต่อ","uri": item.get("link", "#")}}
                ]
            }
        }
        bubbles.append(bubble)

    carousels = []
    for i in range(0, len(bubbles), 10):
        carousels.append({
            "type": "flex",
            "altText": f"ข่าวเกี่ยวข้องกับ ปตท. {now_thai}",
            "contents": {"type": "carousel", "contents": bubbles[i:i+10]}
        })
    return carousels

def broadcast_flex_message(access_token, flex_carousels):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    for idx, carousel in enumerate(flex_carousels, 1):
        payload = {"messages": [carousel]}
        if DRY_RUN:
            print(f"[DRY_RUN] จะส่ง Carousel #{idx}: {json.dumps(payload)[:500]}...")
            continue
        try:
            resp = S.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            print(f"Broadcast #{idx} status:", resp.status_code, getattr(resp, "text", ""))
            if resp.status_code >= 300:
                break
            time.sleep(1.2)
        except Exception as e:
            print("[LINE ERROR]", e)
            break

# ========================= MAIN =========================
def main():
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว")
        return
    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS

    # -------- Filter stage --------
    filtered_news = []
    for news in all_news:
        # ถ้า summary สั้นเกิน ให้ยัด title เป็น detail เพื่อเพิ่มบริบทให้ LLM
        if len((news.get('summary') or '')) < 50:
            news['detail'] = news['title']
        else:
            news['detail'] = ''
        if llm_ptt_subsidiary_impact_filter(news):
            filtered_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ข่าวที่เกี่ยวข้องกับบริษัทลูก PTT (ผ่านฟิลเตอร์): {len(filtered_news)} ข่าว")
    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้องบริษัทลูก PTT")
        return

    # -------- Analyze stage --------
    ranked = rank_candidates(filtered_news, use_keyword_boost=False)
    top_candidates = ranked[: min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์เพียง {len(top_candidates)} ข่าว (จำกัด 10)")

    ptt_related_news = []
    for news in top_candidates:
        data = gemini_summary_and_score(news)
        # unify fields
        news['gemini_summary'] = _normalize_colons(data.get('summary', '')).strip() or 'ไม่พบสรุปข่าว'
        score = int(data.get('score', 3))
        news['gemini_score'] = max(1, min(5, score))
        bd = data.get('score_breakdown', [])
        # บังคับให้ผลรวม points = score
        total_points = sum(int(x.get('points', 0)) for x in bd)
        if bd and total_points != news['gemini_score']:
            diff = news['gemini_score'] - total_points
            bd[-1]['points'] = int(bd[-1].get('points', 0)) + diff
        news['score_breakdown'] = bd

        reason = _polish_impact_text(data.get('impact_reason', '').strip())
        news['gemini_reason'] = reason or '-'

        companies = [c for c in data.get('impact_companies', []) if c in {"PTTEP","PTTLNG","PTTGL","PTTNGD"}]
        news['ptt_companies'] = list(dict.fromkeys(companies))  # unique, keep order

        if is_ptt_related_from_output(companies):
            ptt_related_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not ptt_related_news:
        print("ไม่พบข่าวที่โมเดลระบุว่ากระทบต่อกลุ่ม PTT จากตัวเต็ง 10 ข่าว")
        return

    # เรียงตามคะแนน > เวลาเผยแพร่
    ptt_related_news.sort(key=lambda n: (n.get('gemini_score', 0), n.get('published', datetime.min)), reverse=True)
    top_news = ptt_related_news[:10]

    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if _normalize_link(n.get('link','')) not in sent_links]
    if not top_news_to_send:
        print("ข่าววันนี้กับเมื่อวานส่งครบหมดแล้ว ไม่มีข่าวใหม่")
        return

    # เติมภาพ
    for item in top_news_to_send:
        img = fetch_article_image(item.get("link", "")) or ""
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    # ส่ง LINE
    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)
    save_sent_links([n.get("link", "") for n in top_news_to_send])
    print("เสร็จสิ้น.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
