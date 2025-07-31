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
MAX_RETRIES = 3
SLEEP_BETWEEN_CALLS = (1.2, 2.0)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)
THREE_DAYS_AGO = now - timedelta(days=3)

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
    "YahooFinance-News": {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "category": "Economy", "site": "Yahoo Finance"},
    "Politico-EU": {"type": "rss", "url": "https://www.politico.eu/feed/", "category": "Politics", "site": "Politico"},
    "Guardian-Politics": {"type": "rss", "url": "https://www.theguardian.com/politics/rss", "category": "Politics", "site": "Guardian"},
    "NPR-Politics": {"type": "rss", "url": "https://www.npr.org/rss/rss.php?id=1014", "category": "Politics", "site": "NPR"},
    "NYT-Politics": {"type": "rss", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml", "category": "Politics", "site": "NYT"},
    "TheHill-Politics": {"type": "rss", "url": "https://thehill.com/rss/syndicator/19109", "category": "Politics", "site": "The Hill"},
    "ABCNews-Politics": {"type": "rss", "url": "https://abcnews.go.com/abcnews/politicsheadlines", "category": "Politics", "site": "ABC News"},
}

PTT_ICON_URLS = {
    "PTTEP":  "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTEP.png",
    "PTTLNG": "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTLNG.jpg",
    "PTTGL":  "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTGL.jfif",
    "PTTNGD": "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTNGD.png",
}
DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

GEMINI_CALLS = 0

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
            last_error = e
            if attempt < max_retries:
                time.sleep(min(2**attempt + random.random(), 8))
            else:
                raise last_error

def fetch_news_3days():
    all_news = []
    for _, info in news_sources.items():
        try:
            feed = feedparser.parse(info["url"])
            for entry in feed.entries:
                if hasattr(entry, "published"):
                    pub_dt = dateutil_parser.parse(entry.published).astimezone(bangkok_tz)
                elif hasattr(entry, "updated"):
                    pub_dt = dateutil_parser.parse(entry.updated).astimezone(bangkok_tz)
                else:
                    continue
                if pub_dt < THREE_DAYS_AGO:
                    continue
                title = getattr(entry, "title", "-")
                summary = getattr(entry, "summary", "-")
                link = getattr(entry, "link", None)
                if not link:
                    continue
                all_news.append({
                    "site": info["site"], "category": info["category"],
                    "title": title, "summary": summary, "link": link,
                    "published": pub_dt, "date": pub_dt.strftime("%d/%m/%Y %H:%M")
                })
        except Exception as e:
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")
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

def gemini_summary_and_score(news):
    prompt = f"""
หัวข้อข่าว: {news['title']}
สรุปย่อ: {news['summary']}
เนื้อหาข่าว (ถ้ามี): {news.get('detail', '')}

กรุณาทำ 4 อย่างต่อไปนี้:

1. สรุปข่าวนี้เป็นภาษาไทยอย่างกระชับ (1-2 ประโยค)

2. ให้คะแนนความสำคัญของข่าวนี้ต่อกลุ่ม ปตท. (1-5 คะแนน)
   แจกแจงว่าทำไมจึงได้แต่ละคะแนน เช่น:
   - 2 คะแนน: ข่าวนี้กระทบต่อธุรกิจสำรวจและผลิตปิโตรเลียมของ PTTEP
   - 1 คะแนน: นโยบายภาครัฐที่อาจส่งผลต่อการดำเนินธุรกิจของ PTTLNG

3. วิเคราะห์ว่า ข่าวนี้มีผลกระทบต่อบริษัทใดในกลุ่ม PTT
   บริษัทในกลุ่ม PTT ได้แก่:
   - PTTEP – สำรวจและผลิตปิโตรเลียม
   - PTTLNG – บริหารสถานี LNG
   - PTTGL – การลงทุนใน LNG ระดับโลก
   - PTTNGD – ก๊าซธรรมชาติอุตสาหกรรม

4. แจกแจงคะแนนรวมที่ทำให้ข่าวนี้ได้รับคะแนนตามข้อ (2)
   เป็นรายการบูลเล็ตโดยใส่ "คะแนน:" นำหน้าแต่ละข้อ และให้ผลรวมเท่ากับคะแนนรวม
   ตัวอย่างรูปแบบ:
   - 2 คะแนน: มีประเด็นราคาน้ำมันดิบเพิ่มขึ้น ...
   - 1 คะแนน: มีนโยบายรัฐเกี่ยวกับ LNG ...
   - 1 คะแนน: ความเสี่ยงภูมิรัฐศาสตร์ ...

❗️ตอบกลับในรูปแบบนี้:
- สรุปข่าว: <ข้อความ>
- คะแนน: <คะแนน> (<คะแนนย่อย> จาก..., ...)
- ผลกระทบต่อ ปตท.: กระทบต่อ <ชื่อบริษัท> เพราะ <เหตุผล>
- เหตุผลคะแนนรวม:
  - <คะแนน> คะแนน: <เหตุผล>
  - <คะแนน> คะแนน: <เหตุผล>
"""
    try:
        resp = call_gemini(prompt)
        return resp.text
    except Exception as e:
        return f"ERROR: {e}"

def is_ptt_related_from_output(out_text: str) -> bool:
    if not out_text or out_text.startswith("ERROR"):
        return False
    m = re.search(r"ผลกระทบต่อ\s*ปตท\.[：:]\s*(.*)", out_text)
    if not m: return False
    val = m.group(1).strip()
    return any(x in val for x in ["PTTEP","PTTLNG","PTTGL","PTTNGD"])

# ====== ฟังก์ชัน LLM filter สำหรับบริษัทลูก PTT ======
def llm_ptt_subsidiary_impact_filter(news, llm_model):
    prompt = f'''
คุณคือผู้เชี่ยวชาญด้านการคัดกรองข่าวสำหรับบริษัทในเครือ ปตท. กรุณาวิเคราะห์ข่าวด้านล่างนี้ แล้วตอบเพียง "ใช่" หรือ "ไม่ใช่" เท่านั้น

ให้ตอบ "ใช่" ถ้าเนื้อหาข่าวนี้
- มีผลกระทบโดยตรงหรือโดยอ้อมต่อบริษัทเหล่านี้: PTTEP, PTTLNG, PTTGL, PTTNGD
- แม้ในข่าวจะไม่ได้กล่าวถึงชื่อบริษัทเหล่านี้โดยตรง แต่มีประเด็นที่เกี่ยวข้องกับอุตสาหกรรม/ธุรกิจที่บริษัทเหล่านี้ดำเนินการ เช่น ราคาน้ำมัน, ราคาก๊าซธรรมชาติ, นโยบายหรือกฎระเบียบที่เกี่ยวกับ LNG หรือก๊าซฯ, ทิศทางตลาดพลังงาน ที่บริษัทเหล่านี้จะได้รับผลกระทบหรือมีโอกาสทางธุรกิจ

เช่น
- ราคาน้ำมันหรือก๊าซปรับขึ้น/ลง → อาจมีผลกับ PTTEP หรือ PTTNGD
- นโยบาย LNG หรือโครงการใหม่ที่เกี่ยวกับการนำเข้าหรือขนส่ง LNG → อาจมีผลกับ PTTLNG หรือ PTTGL
- ความเปลี่ยนแปลงในอุตสาหกรรมพลังงานที่เชื่อมโยงกับธุรกิจหลักของบริษัทเหล่านี้

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


def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def create_flex_message(news_items):
    import re
    ICON_SIZE = "md"
    ICONS_PER_ROW = 2
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def _chunk(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    bubbles = []
    for item in news_items:
        bd_text = (item.get("score_breakdown") or "-")
        bd_clean = re.sub(r"^- ", "", bd_text, flags=re.MULTILINE)

        icon_imgs = []
        for code in (item.get("ptt_companies") or []):
            url = PTT_ICON_URLS.get(code, DEFAULT_ICON_URL)
            icon_imgs.append({
                "type": "image",
                "url": url,
                "size": ICON_SIZE,
                "aspectRatio": "1:1",
                "aspectMode": "fit"
            })

        icons_rows = []
        for row_imgs in _chunk(icon_imgs, ICONS_PER_ROW):
            icons_rows.append({
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": row_imgs
            })

        icons_grid = None
        if icons_rows:
            icons_grid = {
                "type": "box",
                "layout": "vertical",
                "margin": "sm",
                "spacing": "xs",
                "contents": (
                    [{
                        "type": "text",
                        "text": "กระทบ:",
                        "size": "xs",
                        "color": "#000000",
                        "weight": "bold"
                    }]
                    + icons_rows
                )
            }

        body_contents = [
            {
                "type": "text",
                "text": item.get("title", "-"),
                "weight": "bold",
                "size": "lg",
                "wrap": True,
                "color": "#111111",
                "maxLines": 3
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
        ]
        if icons_grid:
            body_contents.append(icons_grid)

        body_contents += [
            {
                "type": "text",
                "text": item.get("gemini_summary") or "ไม่พบสรุปข่าว",
                "size": "md",
                "wrap": True,
                "margin": "md",
                "maxLines": 6,
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
                        "maxLines": 8
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
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {"type": "uri", "label": "อ่านต่อ", "uri": item.get("link", "#")}
                    }
                ]
            }
        }
        bubbles.append(bubble)

    # ปิดฟังก์ชันตรงนี้
    carousels = []
    for i in range(0, len(bubbles), 10):
        carousels.append({
            "type": "flex",
            "altText": f"Top ข่าวเกี่ยวข้อง ปตท. {now_thai}",
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
    # 1) ดึงข่าว 3 วันทั้งหมด
    all_news = fetch_news_3days()
    print(f"ดึงข่าวทั้งหมดภายใน 3 วัน: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว")
        return

    # 2) กรองข่าวด้วย LLM (เฉพาะที่เกี่ยวข้องบริษัทลูก PTT 4 บริษัท)
    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS
    filtered_news = []
    for news in all_news:
        # ดึง detail เพิ่ม ถ้า summary สั้นมาก
        if len(news.get('summary','')) < 50:
            try:
                art = Article(news['link']); art.download(); art.parse()
                news['detail'] = (art.text or "").strip() or news['title']
            except Exception:
                news['detail'] = news['title']
        else:
            news['detail'] = ""
        # เรียก LLM filter
        if llm_ptt_subsidiary_impact_filter(news, model):
    filtered_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ข่าวที่เกี่ยวข้องกับบริษัทลูก PTT: {len(filtered_news)} ข่าว")

    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้องบริษัทลูก PTT")
        return

    # 3) เลือกตัวเต็ง 10 ข่าว (ไม่ใช้ LLM)
    ranked = rank_candidates(filtered_news, use_keyword_boost=False)
    top_candidates = ranked[:min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์เพียง {len(top_candidates)} ข่าว (จำกัด 10)")

    # 4) วิเคราะห์ด้วย Gemini LLM
    ptt_related_news = []
    for news in top_candidates:
        out = gemini_summary_and_score(news)
        news['gemini_output'] = out

        m_score = re.search(r"คะแนน[:：]\s*(\d+)", out or "")
        news['gemini_score'] = int(m_score.group(1)) if m_score else 3

        m_sum = re.search(r"สรุปข่าว[:：]\s*(.*)", out or "")
        news['gemini_summary'] = m_sum.group(1).strip() if m_sum else "ไม่พบสรุปข่าว"

        m_reason = re.search(r"ผลกระทบต่อ\s*ปตท\.[：:]\s*(.*)", out or "")
        news['gemini_reason'] = m_reason.group(1).strip() if m_reason else "-"

        news['ptt_companies'] = extract_ptt_companies(news.get('gemini_reason', ''))

        m_bd = re.search(r"เหตุผลคะแนนรวม[:：]\s*(.*)", out or "", flags=re.DOTALL)
        if m_bd:
            score_bd_raw = m_bd.group(1).strip()
            lines = [ln.strip() for ln in score_bd_raw.splitlines() if "คะแนน" in ln]
            news['score_breakdown'] = "\n".join(lines) if lines else score_bd_raw
        else:
            news['score_breakdown'] = "-"

        # เช็คซ้ำเพื่อความชัวร์
        if is_ptt_related_from_output(out):
            ptt_related_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not ptt_related_news:
        print("ไม่พบข่าวที่โมเดลระบุว่ากระทบต่อกลุ่ม PTT จากตัวเต็ง 10 ข่าว")
        return

    # 5) คัด Top 10 ตามคะแนน
    ptt_related_news.sort(key=lambda n: (n.get('gemini_score',0), n.get('published', datetime.min)), reverse=True)
    top_news = ptt_related_news[:10]

    # --- กันข่าวซ้ำ (วันนี้กับเมื่อวาน) ---
    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if n["link"] not in sent_links]

    if not top_news_to_send:
        print("ข่าววันนี้กับเมื่อวานส่งครบหมดแล้ว ไม่มีข่าวใหม่")
        return

    # 6) ดึงรูปภาพและสร้าง Flex
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
