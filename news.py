# -*- coding: utf-8 -*-
"""
ดึงข่าวย้อนหลัง 3 วัน -> จัดอันดับหา 10 ข่าวตัวเต็ง (ไม่ใช้ LLM) -> ให้ Gemini วิเคราะห์เฉพาะ 10 ข่าว
-> สรุป + ให้คะแนน + แจกแจงคะแนนรวม (score breakdown) + ผลกระทบต่อบริษัทในกลุ่ม PTT
-> สร้าง Flex Message (มีไอคอนบริษัทที่ได้รับผล) และ (เลือกได้) ส่ง Broadcast ไป LINE OA

คุณสมบัติหลัก
- ใช้โควตา Gemini สูงสุด 10 calls/รอบ (GEMINI_DAILY_BUDGET = 10)
- ข้อ 4 เป็น "เหตุผลคะแนนรวม" (score breakdown)
- Flex: แสดง "ผลกระทบ / เหตุผลคะแนน" + "คะแนนรวม" + breakdown
- เพิ่มแถว "กระทบ:" + ไอคอนบริษัท (PTTEP, PTTLNG, PTTGL, PTTNGD) ใต้หมวดหมู่
"""

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

# (optional) โหลด .env เมื่อรันบนเครื่องนักพัฒนา
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ========================= CONFIG =========================
# --- API KEY: อ่านจาก Environment/Secrets ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

# --- ตั้งค่าโมเดล / โควตา ---
genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODEL_NAME = "gemini-1.5-flash"
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

GEMINI_DAILY_BUDGET = 10            # ใช้โมเดลไม่เกิน 10 ครั้ง/รอบ
MAX_RETRIES = 3
SLEEP_BETWEEN_CALLS = (1.2, 2.0)    # เว้นจังหวะช่วยลดโอกาสโดน rate limit
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"  # true=ไม่ยิง LINE, แค่พิมพ์ payload

# --- เวลา/โซน ---
import pytz
bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)
THREE_DAYS_AGO = now - timedelta(days=3)

# --- RSS แหล่งข่าว ---
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

# ===== โลโก้บริษัทในกลุ่ม PTT =====
PTT_ICON_URLS = {
    "PTTEP":  "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTEP.png",
    "PTTLNG": "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTLNG.jpg",
    "PTTGL":  "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTGL.jfif",
    "PTTNGD": "https://raw.githubusercontent.com/phutthachat1001/ptt-assets/refs/heads/main/PTTNGD.png",
}
DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ----- ตัวเลือก: boost คำสำคัญในการจัดอันดับก่อนส่งเข้า LLM -----
USE_KEYWORD_BOOST = False
KEYWORDS = [
    "PTT","PTTEP","PTTLNG","PTTGL","PTTNGD",
    "LNG","gas","natural gas","pipeline","regas",
    "oil","crude","OPEC","refinery","hydrogen","ammonia","CCS","carbon capture"
]

# ========================= Gemini wrapper =========================
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

# ========================= Fetch news =========================
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

# ========================= Rank 10 ตัวเต็ง (ไม่ใช้ LLM) =========================
def rank_candidates(news_list, use_keyword_boost=USE_KEYWORD_BOOST):
    ranked = []
    for n in news_list:
        # 1) ความสด (0..3)
        age_h = (now - n["published"]).total_seconds() / 3600.0
        recency = max(0.0, (72.0 - min(72.0, age_h))) / 72.0 * 3.0
        # 2) หมวดข่าว
        cat_w = {"Energy": 3.0, "Economy": 2.0, "Politics": 1.0}.get(n["category"], 1.0)
        # 3) ความยาวสรุป
        length = min(len(n.get("summary","")) / 500.0, 1.0)
        score = recency + cat_w + length
        # 4) (ตัวเลือก) keyword boost
        if use_keyword_boost:
            text = (n["title"] + " " + n.get("summary","")).lower()
            if any(k.lower() in text for k in KEYWORDS):
                score += 1.5
        ranked.append((score, n))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in ranked]

# ========================= Download top image =========================
def fetch_article_image(url):
    try:
        art = Article(url); art.download(); art.parse()
        return art.top_image or ""
    except Exception:
        return ""

# ========================= Helper: อ่านชื่อบริษัทจากผลวิเคราะห์ =========================
def extract_ptt_companies(text: str):
    if not text:
        return []
    companies = []
    for code in ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]:
        if code in text:
            companies.append(code)
    return companies

# ========================= LLM prompt =========================
def gemini_summary_and_score(news):
    prompt = f"""
หัวข้อข่าว: {news['title']}
สรุปย่อ: {news['summary']}
เนื้อหาข่าว (ถ้ามี): {news.get('detail', '')}

กรุณาทำ 4 อย่างต่อไปนี้:

1. สรุปข่าวนี้เป็นภาษาไทยอย่างกระชับ (1-2 ประโยค)

2. ให้คะแนนความสำคัญของข่าวระดับโลก (1-5 คะแนน)
   แจกแจงว่าแต่ละคะแนนมาจากปัจจัยอะไร เช่น:
   - 2 คะแนนจากราคาน้ำมันที่เปลี่ยนแปลง
   - 1 คะแนนจากนโยบายรัฐที่เกี่ยวกับ LNG

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

# ========================= LINE Flex =========================
def _chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def create_flex_message(news_items):
    ICON_SIZE = "md"  # ขยายไอคอนเป็น md (หรือ "lg" ได้ถ้าอยากให้ใหญ่ขึ้น)
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    bubbles = []
    for item in news_items:
        # จำกัดบรรทัด breakdown เพื่อไม่ให้ยาวเกิน
        bd_text = (item.get("score_breakdown") or "-")
        bd_lines = bd_text.splitlines()
        if len(bd_lines) > 6:
            bd_text = "\n".join(bd_lines[:6]) + "\n... (ตัดทอน)"

        # ====== แถวไอคอนบริษัทที่ได้รับผล (horizontal + image OK) ======
        icon_imgs = []
        for code in (item.get("ptt_companies") or []):
            url = PTT_ICON_URLS.get(code, DEFAULT_ICON_URL)
            icon_imgs.append({
                "type": "image",
                "url": url,
                "size": ICON_SIZE,        # ขยายขนาดเป็น md หรือ lg
                "aspectRatio": "1:1",
                "aspectMode": "fit"
                # ถ้ายังไม่ใหญ่พอ ลองเพิ่ม "width": "40px"
            })

        icons_row = None
        if icon_imgs:
            icons_row = {
                "type": "box",
                "layout": "horizontal",
                "margin": "sm",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "กระทบ:", "size": "xs", "color": "#888888", "align": "start"}
                ] + icon_imgs,
                "alignItems": "flex-start",   # ทำให้ภาพและข้อความเรียงชิดบน
            }

        # ---- สร้าง body ----
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
        if icons_row:
            body_contents.append(icons_row)   # ใส่ไอคอนต่อท้าย

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
                    {"type": "text", "text": "ผลกระทบ / เหตุผลคะแนน", "weight": "bold", "size": "sm", "color": "#D32F2F"},
                    {
                        "type": "text",
                        "text": f"คะแนนรวม: {item.get('gemini_score','-')} คะแนน",
                        "size": "sm",
                        "wrap": True,
                        "color": "#C62828",
                        "weight": "bold"
                    },
                    {
                        "type": "text",
                        "text": (item.get("gemini_reason") or "-"),
                        "size": "sm",
                        "wrap": True,
                        "color": "#C62828",
                        "maxLines": 6
                    },
                    {
                        "type": "text",
                        "text": bd_text,
                        "size": "xs",
                        "wrap": True,
                        "color": "#8E0000"
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

    # แบ่ง Carousel ละไม่เกิน 10 bubbles
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

    # 2) เลือกตัวเต็ง 10 ข่าว (ไม่ใช้ LLM)
    ranked = rank_candidates(all_news, use_keyword_boost=USE_KEYWORD_BOOST)
    top_candidates = ranked[:min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์เพียง {len(top_candidates)} ข่าว (จำกัด 10)")

    # 3) วิเคราะห์ด้วย LLM
    ptt_related_news = []
    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS
    for news in top_candidates:
        if len(news.get('summary','')) < 50:
            try:
                art = Article(news['link']); art.download(); art.parse()
                news['detail'] = (art.text or "").strip() or news['title']
            except Exception:
                news['detail'] = news['title']
        else:
            news['detail'] = ""

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

        if is_ptt_related_from_output(out):
            ptt_related_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not ptt_related_news:
        print("ไม่พบข่าวที่โมเดลระบุว่ากระทบต่อกลุ่ม PTT จากตัวเต็ง 10 ข่าว")
        return

    # 4) คัด Top 10 ตามคะแนน
    ptt_related_news.sort(key=lambda n: (n.get('gemini_score',0), n.get('published', datetime.min)), reverse=True)
    top_news = ptt_related_news[:10]

    # 5) ดึงรูปภาพและสร้าง Flex
    for item in top_news:
        item["image"] = fetch_article_image(item["link"]) or ""

    carousels = create_flex_message(top_news)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)
    print("เสร็จสิ้น.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
