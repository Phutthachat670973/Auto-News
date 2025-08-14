import os
import re
import json
import time
import random
from datetime import datetime, timedelta

import feedparser
from dateutil import parser as dateutil_parser
import pytz
from newspaper import Article
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

genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODEL_NAME = "gemini-1.5-flash"
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

GEMINI_DAILY_BUDGET = 10
MAX_RETRIES = 6
SLEEP_BETWEEN_CALLS = (4.2, 4.8)   # ปลอดภัยสำหรับ Gemini Free Tier (15 req/min)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)

# ========== SENT LINKS: กันส่งซ้ำ (วันนี้กับเมื่อวาน) ==========
SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

def get_sent_links_file(date=None):
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")

def load_sent_links_today_yesterday():
    sent_links = set()
    for i in range(2):  # วันนี้, เมื่อวาน
        date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        path = get_sent_links_file(date)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    url = line.strip()
                    if url:
                        sent_links.add(url)
    return sent_links

def save_sent_links(new_links, date=None):
    path = get_sent_links_file(date)
    with open(path, "a", encoding="utf-8") as f:
        for url in new_links:
            f.write(url.strip() + "\n")

news_sources = {
    "Oilprice": {"type": "rss", "url": "https://oilprice.com/rss/main", "category": "Energy", "site": "Oilprice"},
    "CleanTechnica": {"type": "rss", "url": "https://cleantechnica.com/feed/", "category": "Energy", "site": "CleanTechnica"},
    "HydrogenFuelNews": {"type": "rss", "url": "https://www.hydrogenfuelnews.com/feed/", "category": "Energy", "site": "Hydrogen Fuel News"},
    "Economist-Latest": {"type": "rss", "url": "https://www.economist.com/latest/rss.xml", "category": "Economy", "site": "Economist"},
}

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

GEMINI_CALLS = 0

# ---------- บล็อก Context สำหรับใส่ในพรอมพ์ ----------
PTT_CONTEXT = """
[Context: ความรู้พื้นฐานเพื่อช่วยตีความผลกระทบรายบริษัทในกลุ่ม PTT]

• PTTEP — สำรวจและผลิตปิโตรเลียม (E&P/Upstream)
  - ใช้ชี้เมื่อข่าวแตะ: ราคาน้ำมัน/ก๊าซ, ความเสี่ยงซัพพลาย/แหล่งผลิต, ความคืบหน้าสำรวจ-พัฒนาแหล่ง, กฎระเบียบ/สัมปทาน (PSC), เหตุขัดข้องในแหล่ง/ท่อ
  - คำพ้อง/สัญญาณในข่าว: upstream, E&P, แท่น/rig, field/reservoir, offshore/onshore, decommissioning

• PTTLNG — โครงสร้างพื้นฐาน/สถานีรับก๊าซ LNG และการรีก๊าซ (Map Ta Phut LNG Terminals)
  - ใช้ชี้เมื่อข่าวแตะ: นโยบาย/โควตานำเข้า LNG, แผนขยายกำลังรีก๊าซ, ความพร้อมท่าเทียบเรือ/คลัง, ค่าธรรมเนียม/มาตรฐานความปลอดภัย, ความแออัดของโครงข่าย
  - คำพ้อง/สัญญาณในข่าว: LNG terminal, receiving terminal, regas/รีก๊าซ, berthing jetty, storage tank, Map Ta Phut

• PTTGL — การลงทุน/พอร์ต/เทรดดิ้ง LNG ระดับโลก (JV ระหว่าง PTT และ PTTEP)
  - ใช้ชี้เมื่อข่าวแตะ: ดีลสัญญาซื้อขาย LNG (SPA/HOA), สัดส่วนถือหุ้นในโครงการ LNG, โครงสร้างพอร์ต/การบริหารสเปรดราคา hub (JKM/TTF/HH), กลยุทธ์จัดหา/ส่งมอบ
  - คำพ้อง/สัญญาณในข่าว: LNG portfolio, SPA, offtake, lifting, LNG JV, equity in liquefaction

• PTTNGD — กระจายก๊าซธรรมชาติภาคอุตสาหกรรม/เมือง (City/Industrial Gas Distribution)
  - ใช้ชี้เมื่อข่าวแตะ: ดีมานด์ก๊าซในนิคม/โรงงาน, ราคาก๊าซปลายทาง/โครงสร้างอัตรา, การเปลี่ยนเชื้อเพลิง (fuel switch), ขยายโครงข่ายท่อเมือง/ลูกค้าอุตสาหกรรม
  - คำพ้อง/สัญญาณในข่าว: city gas, industrial gas, distribution network, pipeline expansion, captive customer

[แนวทางให้โมเดลตัดสิน]
- ถ้าข่าวว่าด้วยราคาน้ำมัน/ซัพพลาย upstream → ให้พิจารณา PTTEP ก่อน
- ถ้าข่าวว่าด้วยโครงสร้างพื้นฐานรับ-รีก๊าซ/นโยบายนำเข้า LNG → ให้พิจารณา PTTLNG
- ถ้าข่าวว่าด้วยดีลสัญญา/พอร์ต LNG ระดับโลก → ให้พิจารณา PTTGL
- ถ้าข่าวว่าด้วยดีมานด์/ราคาก๊าซฝั่งลูกค้าอุตสาหกรรม/เมือง → ให้พิจารณา PTTNGD
"""

def call_gemini(prompt, max_retries=MAX_RETRIES):
    global GEMINI_CALLS
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError(f"ถึงงบ Gemini ประจำวันแล้ว ({GEMINI_CALLS}/{GEMINI_DAILY_BUDGET})")
    last_error = None
    for attempt in range(1, max_retries+1):
        try:
            resp = model.generate_content(prompt)
            GEMINI_CALLS += 1
            return resp
        except Exception as e:
            err_str = str(e)
            if "429" in err_str and "retry_delay" in err_str:
                import re
                m = re.search(r'retry_delay\s*{[^}]*seconds:\s*(\d+)', err_str)
                wait_sec = int(m.group(1)) if m else 60
                print(f"[Quota] โดน 429 รอ {wait_sec} วินาทีแล้วลองใหม่ (รอบที่ {attempt})")
                time.sleep(wait_sec)
            else:
                last_error = e
                if attempt < max_retries:
                    time.sleep(5 * attempt)
                else:
                    raise last_error
    raise last_error

def fetch_news_9pm_to_6am():
    now = datetime.now(bangkok_tz)
    start_time = (now - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=6, minute=0, second=0, microsecond=0)
    print("ช่วง fetch:", start_time, "ถึง", end_time)
    all_news = []
    for _, info in news_sources.items():
        try:
            feed = feedparser.parse(info["url"])
            for entry in feed.entries:
                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if not pub_str:
                    continue
                pub_dt = dateutil_parser.parse(pub_str).astimezone(bangkok_tz)
                if not (start_time <= pub_dt <= end_time):
                    continue
                all_news.append({
                    "site": info["site"], "category": info["category"],
                    "title": getattr(entry, "title", "-"),
                    "summary": getattr(entry, "summary", "-"),
                    "link": getattr(entry, "link", ""),
                    "published": pub_dt,
                    "date": pub_dt.strftime("%d/%m/%Y %H:%M")
                })
        except Exception as e:
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")
    print("ข่าวที่อยู่ในช่วง:", len(all_news))
    return all_news

def fetch_article_image(url):
    try:
        art = Article(url); art.download(); art.parse()
        return art.top_image or ""
    except Exception:
        return ""

def extract_ptt_companies(text: str):
    if not text:
        return []
    companies = []
    for code in ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]:
        if code in text:
            companies.append(code)
    return companies

# ================= JSON version (ไม่เพิ่มโควต้า) =================
def gemini_summary_and_score_json(news):
    """
    ให้โมเดลตอบเป็น JSON เท่านั้น แล้วพาร์สอย่างทนทาน
    ฟอร์แมต JSON:
    {
      "summary_th": "ข้อความ",
      "score": 1..5,
      "score_breakdown": [{"points": n, "reason": "ข้อความ"}, ...],
      "impact": {"companies": ["PTTEP","PTTLNG","PTTGL","PTTNGD"], "reason": "ข้อความ"}
    }
    """
    allowed_companies = {"PTTEP","PTTLNG","PTTGL","PTTNGD"}
    prompt = f"""
{PTT_CONTEXT}

คุณเป็นนักวิเคราะห์ข่าวพลังงานสำหรับกลุ่ม ปตท. จงตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่นนอก JSON

อินพุตข่าว:
- หัวข้อ: {news['title']}
- สรุปย่อ: {news['summary']}
- เนื้อหา: {news.get('detail', '')}

สคีมา JSON:
{{
  "summary_th": "สรุปไทยแบบย่อ 1-2 ประโยค",
  "score": 1,
  "score_breakdown": [
    {{"points": 2, "reason": "เหตุผลย่อ"}},
    {{"points": 1, "reason": "เหตุผลย่อ"}}
  ],
  "impact": {{
    "companies": ["PTTEP","PTTLNG","PTTGL","PTTNGD"],
    "reason": "อธิบายสั้นว่าทำไมกระทบบริษัทใด"
  }}
}}

ข้อกำหนด:
- "score" เป็นจำนวนเต็ม 1..5
- ผลรวม points ใน "score_breakdown" ต้องเท่ากับ "score"
- "impact.companies" อนุญาตเฉพาะ PTTEP, PTTLNG, PTTGL, PTTNGD; ถ้าไม่เกี่ยวให้ []
- ถ้าไม่แน่ใจ ให้เลือกแบบระมัดระวังและให้เหตุผลสั้นๆ
"""
    try:
        resp = call_gemini(prompt)
        raw = (getattr(resp, "text", "") or "").strip()
        # เผื่อโมเดลห่อด้วย code fence
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
        data = json.loads(raw)

        # ตรวจ/ซ่อมค่า
        if not isinstance(data, dict):
            raise ValueError("LLM JSON is not an object")

        summary = str(data.get("summary_th", "ไม่พบสรุปข่าว")).strip() or "ไม่พบสรุปข่าว"

        try:
            score = int(data.get("score", 3))
        except Exception:
            score = 3
        score = max(1, min(5, score))

        breakdown = data.get("score_breakdown") or []
        if not isinstance(breakdown, list):
            breakdown = []
        # คุมผลรวมแต้มให้เท่าคะแนน
        total = 0
        fixed = []
        for item in breakdown:
            try:
                pts = int(item.get("points", 0))
                rsn = str(item.get("reason", "-")).strip() or "-"
            except Exception:
                pts, rsn = 0, "-"
            fixed.append({"points": pts, "reason": rsn})
            total += pts
        if total != score:
            # ปรับรายการสุดท้ายให้ผลรวมตรง
            if fixed:
                diff = score - total
                fixed[-1]["points"] += diff
            else:
                fixed = [{"points": score, "reason": "คะแนนรวมปรับอัตโนมัติให้ตรงสเปค"}]

        impact = data.get("impact") or {}
        if not isinstance(impact, dict):
            impact = {}
        companies = impact.get("companies") or []
        if not isinstance(companies, list):
            companies = []
        # กรองให้เหลือเฉพาะ allowed
        companies = [c for c in companies if str(c).upper() in allowed_companies]
        reason = str(impact.get("reason", "-")).strip() or "-"

        return {
            "summary_th": summary,
            "score": score,
            "score_breakdown": fixed,
            "impact": {"companies": companies, "reason": reason}
        }

    except Exception as e:
        # Fallback ที่ไม่ทำให้ล้ม
        return {
            "summary_th": "ไม่สามารถพาร์ส JSON จากโมเดลได้",
            "score": 3,
            "score_breakdown": [{"points": 3, "reason": "Fallback: พาร์สไม่สำเร็จ"}],
            "impact": {"companies": [], "reason": "-"},
            "error": str(e)
        }

def is_ptt_related_from_output(out_text: str) -> bool:
    # ยังเก็บไว้เผื่อใช้งานที่อื่น แต่ในเวอร์ชัน JSON เราใช้ length ของ companies เป็นหลัก
    if not out_text or out_text.startswith("ERROR"):
        return False
    m = re.search(r"ผลกระทบต่อ\s*ปตท\.[：:]\s*(.*)", out_text)
    if not m: return False
    val = m.group(1).strip()
    return any(x in val for x in ["PTTEP","PTTLNG","PTTGL","PTTNGD"])

def llm_ptt_subsidiary_impact_filter(news, llm_model):
    # ฟิลเตอร์เดิม (ยัง 1 call/ข่าวเหมือนเดิม) — ไม่เพิ่มโควต้า
    prompt = f'''
{PTT_CONTEXT}

คุณคือผู้เชี่ยวชาญด้านการคัดกรองข่าวสำหรับบริษัทในเครือ ปตท. กรุณาวิเคราะห์ข่าวด้านล่างนี้ แล้วตอบเพียง "ใช่" หรือ "ไม่ใช่" เท่านั้น

ให้ตอบ "ใช่" ถ้าเนื้อหาข่าวนี้
- มีผลกระทบโดยตรงหรือโดยอ้อมต่อบริษัทเหล่านี้: PTTEP, PTTLNG, PTTGL, PTTNGD
- แม้ในข่าวจะไม่ได้กล่าวถึงชื่อบริษัทเหล่านี้โดยตรง แต่มีประเด็นที่เกี่ยวข้องกับอุตสาหกรรม/ธุรกิจที่บริษัทเหล่านี้ดำเนินการ (เช่น ราคาน้ำมัน/ก๊าซ, LNG, โครงสร้างพื้นฐาน, ดีลสัญญา, ดีมานด์ภาคอุตสาหกรรม)

หากข่าวไม่มีผลกระทบที่เกี่ยวข้องกับธุรกิจหลักของบริษัทเหล่านี้เลย ให้ตอบ "ไม่ใช่"
ตอบเพียง "ใช่" หรือ "ไม่ใช่" เท่านั้น  
---
ข่าว:
{news['title']}
{news['summary']}
{news.get('detail', '')}
'''
    try:
        resp = llm_model.generate_content(prompt)
        ans = resp.text.strip().replace("\n", "")
        return ans.startswith("ใช่")
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        return False

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

def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def create_flex_message(news_items):
    import re
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def join_companies(codes):
        codes = codes or []
        if not codes:
            return "ไม่มีระบุ"
        return ", ".join(codes)

    bubbles = []
    for item in news_items:
        bd_text = (item.get("score_breakdown") or "-")
        bd_clean = re.sub(r"^- ", "", bd_text, flags=re.MULTILINE)

        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm"
        }

        body_contents = [
            {
                "type": "text",
                "text": item.get("title", "-"),
                "weight": "bold",
                "size": "lg",
                "wrap": True,
                "color": "#111111",
            },
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "sm",
                "contents": [
                    {"type": "text", "text": f"🗓 {item.get('date','-')}", "size": "xs", "color": "#aaaaaa", "flex": 5},
                    {"type": "text", "text": f"📌 {item.get('category','')}", "size": "xs", "color": "#888888", "align": "end", "flex": 5}
                ]
            },
            {"type": "text", "text": f"🌍 {item.get('site','')}", "size": "xs", "color": "#448AFF", "margin": "sm"},
            impact_line,
            {
                "type": "text",
                "text": item.get("gemini_summary") or "ไม่พบสรุปข่าว",
                "size": "md",
                "wrap": True,
                "margin": "md",
                "color": "#1A237E",
                "weight": "bold"
            },
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "contents": [
                    {
                        "type": "text",
                        "text": "ผลกระทบ / เหตุผลคะแนน",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#D32F2F"
                    },
                    {
                        "type": "text",
                        "text": (item.get("gemini_reason") or "-"),
                        "size": "md",
                        "wrap": True,
                        "color": "#C62828",
                        "weight": "bold",
                    },
                    {
                        "type": "text",
                        "text": f"คะแนนรวม: {item.get('gemini_score','-')} คะแนน",
                        "size": "lg",
                        "wrap": True,
                        "color": "#000000",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": bd_clean,
                        "size": "sm",
                        "wrap": True,
                        "color": "#8E0000",
                        "weight": "bold"
                    }
                ]
            }
        ]

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": item.get("image") or "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png",
                "size": "full",
                "aspectRatio": "16:9",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": body_contents
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": "หมายเหตุ: การวิเคราะห์ทั้งหมดอยู่ในช่วงทดสอบ ขออภัยในความไม่สะดวก",
                        "size": "xs",
                        "color": "#FF0000",
                        "wrap": True,
                        "margin": "md",
                        "weight": "regular"
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {"type": "uri", "label": "อ่านต่อ", "uri": item.get("link", "#")}
                    }
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
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"Broadcast #{idx} status:", resp.status_code, getattr(resp, "text", ""))
        if resp.status_code >= 300:
            break

# ========================= MAIN =========================
def main():
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว")
        return
    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS
    filtered_news = []
    for news in all_news:
        if len(news.get('summary','')) < 50:
            try:
                art = Article(news['link']); art.download(); art.parse()
                news['detail'] = (art.text or "").strip() or news['title']
            except Exception:
                news['detail'] = news['title']
        else:
            news['detail'] = ""
        if llm_ptt_subsidiary_impact_filter(news, model):
            filtered_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))   # Sleep นานขึ้น

    print(f"ข่าวที่เกี่ยวข้องกับบริษัทลูก PTT: {len(filtered_news)} ข่าว")

    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้องบริษัทลูก PTT")
        return

    ranked = rank_candidates(filtered_news, use_keyword_boost=False)
    top_candidates = ranked[:min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์เพียง {len(top_candidates)} ข่าว (จำกัด 10)")

    ptt_related_news = []
    for news in top_candidates:
        # ===== เปลี่ยนมาใช้ JSON เวอร์ชัน (ไม่เพิ่มจำนวน call) =====
        data = gemini_summary_and_score_json(news)
        news['gemini_output'] = data

        news['gemini_score'] = int(data.get('score', 3))
        news['gemini_summary'] = data.get('summary_th', 'ไม่พบสรุปข่าว')

        impact = data.get('impact', {}) or {}
        news['gemini_reason'] = impact.get('reason', '-') or '-'
        companies = impact.get('companies', []) or []
        # ถ้ายังอยากเผื่อ LLM เขียนชื่อบริษัทในเหตุผล ให้ดึงเพิ่มจากข้อความด้วย
        if not companies:
            companies = extract_ptt_companies(news['gemini_reason'])
        news['ptt_companies'] = companies

        # ทำ breakdown เป็นข้อความสวยๆ
        bd_lines = []
        for item in data.get('score_breakdown', []):
            try:
                bd_lines.append(f"{int(item.get('points',0))} คะแนน: {str(item.get('reason','-')).strip()}")
            except:
                pass
        news['score_breakdown'] = "\n".join(bd_lines) if bd_lines else "-"

        # เก็บข่าวที่มีรายชื่อบริษัทกระทบอย่างน้อย 1
        if news['ptt_companies']:
            ptt_related_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))   # Sleep นานขึ้น

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not ptt_related_news:
        print("ไม่พบข่าวที่โมเดลระบุว่ากระทบต่อกลุ่ม PTT จากตัวเต็ง 10 ข่าว")
        return

    ptt_related_news.sort(key=lambda n: (n.get('gemini_score',0), n.get('published', datetime.min)), reverse=True)
    top_news = ptt_related_news[:10]

    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if n["link"] not in sent_links]

    if not top_news_to_send:
        print("ข่าววันนี้กับเมื่อวานส่งครบหมดแล้ว ไม่มีข่าวใหม่")
        return

    for item in top_news_to_send:
        item["image"] = fetch_article_image(item["link"]) or ""

    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)
    save_sent_links([n["link"] for n in top_news_to_send])
    print("เสร็จสิ้น.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
