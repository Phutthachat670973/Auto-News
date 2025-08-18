# -*- coding: utf-8 -*-
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

# ใช้รุ่นเดียว: gemini-2.5-flash (แทน 1.5 ที่เลิกใช้งานแล้ว)
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# โควต้าต่อวัน (สอดคล้อง RPD ของ 2.5-flash) ปรับได้ผ่าน ENV
GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))
MAX_RETRIES = 6

# ช่วงพักต่อคำขอ: 2.5-flash ~ RPM ≈ 10 → เว้น 6–7 วินาที
SLEEP_BETWEEN_CALLS = (6.0, 7.0)
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
    "YahooFinance-News": {"url": "https://finance.yahoo.com/news/rssindex", "category": "Economy", "site": "Yahoo Finance"},
}

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

GEMINI_CALLS = 0

# ---------- Helpers ----------
def _normalize_colons(text: str) -> str:
    if not text:
        return text
    # แทนที่เครื่องหมายโคลอนทุกแบบให้เป็น ":" ตัวเดียว
    return re.sub(r"[：﹕꞉︓⦂⸿˸]", ":", text)


def _polish_impact_text(text: str) -> str:
    """ตัด (บวก/ลบ/ไม่ชัดเจน) และ (สั้น/กลาง/ยาว) + เกลาเว้นวรรค ให้สำนวนดูโปรฯ"""
    if not text:
        return text
    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)  # ตัดวงเล็บทิศทาง/ระยะเวลา
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*,\s*,", ", ", text)
    text = re.sub(r"\s*,\s*\.", ".", text)
    return text.strip()

# ---------- Context + Few-shot ----------
PTT_CONTEXT = """
[Context: ความรู้พื้นฐานเพื่อช่วยตีความผลกระทบรายบริษัทในกลุ่ม PTT]

• PTTEP — สำรวจและผลิตปิโตรเลียม (E&P/Upstream)
  - ใช้เมื่อข่าวแตะ: ราคาน้ำมัน/ก๊าซ, ความเสี่ยงซัพพลาย/แหล่งผลิต, ความคืบหน้าสำรวจ/พัฒนาแหล่ง, สัมปทาน/PSC, เหตุขัดข้องแหล่ง/ท่อ
  - คำพ้อง: upstream, E&P, rig/แท่น, field/reservoir, offshore/onshore
• PTTLNG — โครงสร้างพื้นฐาน/สถานีรับ LNG และการรีก๊าซ (Map Ta Phut)
  - ใช้เมื่อข่าวแตะ: นโยบายนำเข้า/โควตา, ขยายกำลังรีก๊าซ, ท่าเทียบ/คลัง, ค่าธรรมเนียม/ความปลอดภัย, ความแออัดโครงข่าย
  - คำพ้อง: LNG terminal, receiving terminal, regas/รีก๊าซ, storage tank
• PTTGL — การลงทุน/พอร์ต/เทรดดิ้ง LNG ระดับโลก (JV PTT & PTTEP)
  - ใช้เมื่อข่าวแตะ: ดีลสัญญา LNG (SPA/HOA), สัดส่วนถือหุ้นโครงการ LNG, สเปรดราคา hub (JKM/TTF/HH), กลยุทธ์จัดหา/ส่งมอบ
  - คำพ้อง: LNG portfolio, SPA, offtake, lifting, LNG JV, equity in liquefaction
• PTTNGD — กระจายก๊าซภาคอุตสาหกรรม/เมือง (City/Industrial Gas Distribution)
  - ใช้เมื่อข่าวแตะ: ดีมานด์ก๊าซอุตสาหกรรม/เมือง, ราคาก๊าซปลายทาง, fuel switch, ขยายโครงข่ายท่อ/ลูกค้า
  - คำพ้อง: city gas, industrial gas, distribution network, pipeline expansion

[แนวทางตัดสิน]
- ราคาน้ำมัน/ซัพพลาย upstream → พิจารณา PTTEP ก่อน
- โครงสร้างพื้นฐานรับ-รีก๊าซ/นโยบาย LNG → พิจารณา PTTLNG
- ดีล/พอร์ต LNG ระดับโลก → พิจารณา PTTGL
- ดีมานด์/ราคาก๊าซฝั่งลูกค้าอุตสาหกรรม/เมือง → พิจารณา PTTNGD
"""

# Few-shot (คงของเดิมที่ปรับแล้ว)
FEW_SHOT = """
[ตัวอย่าง 1]
อินพุต:
หัวข้อข่าว: Ukraine Hits Key Russian Pipeline Hub as Moscow Braces for Trump-Putin Talks
สรุปย่อ: ยูเครนโจมตีสถานีสูบน้ำมันของรัสเซีย ทำให้เกิดความไม่แน่นอนด้านซัพพลาย
เนื้อหาข่าว: น้ำมันดิบในยุโรปและเอเชียส่งผลต่อราคาน้ำมัน

เอาต์พุต:
- สรุปข่าว: ยูเครนโจมตีโครงสร้างพื้นฐานน้ำมันรัสเซีย เพิ่มความเสี่ยงซัพพลาย กดดันราคาน้ำมันปรับขึ้น
- คะแนน: 5 (3 จากความเสี่ยง supply disruption upstream, 2 จากความตึงเครียดภูมิรัฐศาสตร์)
- ผลกระทบต่อ ปตท.: กระทบต่อ PTTEP เพราะเป็นธุรกิจ upstream ที่อ่อนไหวต่อราคาน้ำมัน
- เหตุผลคะแนนรวม:
  - 3 คะแนน: ความเสี่ยง supply disruption กระทบต่อรายได้ upstream ของ PTTEP
  - 2 คะแนน: ความตึงเครียดภูมิรัฐศาสตร์เพิ่มความไม่แน่นอนด้านพลังงาน

[ตัวอย่าง 2]
อินพุต:
หัวข้อข่าว: US Natural Gas Prices Slump On High Inventories, Record Production
สรุปย่อ: ราคาแก๊สธรรมชาติในสหรัฐลดลงจากปริมาณสำรองสูงและการผลิตเพิ่มขึ้น
เนื้อหาข่าว: อาจส่งผลต่อตลาดแก๊สทั่วโลก

เอาต์พุต:
- สรุปข่าว: ราคาแก๊สสหรัฐร่วงจากสำรองสูงและการผลิตเพิ่ม อาจกดดันราคา LNG ในตลาดโลก
- คะแนน: 4 (2 จากราคาก๊าซโลกมีแนวโน้มลด, 2 จากผลกระทบต่อ margin ของ LNG import)
- ผลกระทบต่อ ปตท.: กระทบต่อ PTTGL เพราะพอร์ต LNG อาจทำกำไรลดลง และกระทบ PTTNGD จากต้นทุนก๊าซปลายทางที่เปลี่ยนแปลง
- เหตุผลคะแนนรวม:
  - 2 คะแนน: ราคาก๊าซลดลงกระทบรายได้จากพอร์ต LNG ของ PTTGL
  - 2 คะแนน: ราคาก๊าซที่ลดลงเปลี่ยนโครงสร้างต้นทุน PTTNGD

[ตัวอย่าง 3]
อินพุต:
หัวข้อข่าว: KRG, Baghdad Strike Another Oil Deal
สรุปย่อ: อิรักและเคอร์ดิสถานตกลงส่งออกน้ำมันร่วมกัน แต่ยังรอการอนุมัติจากตุรกี
เนื้อหาข่าว: -

เอาต์พุต:
- สรุปข่าว: ดีลน้ำมันระหว่างอิรัก–เคอร์ดิสถานอาจเพิ่มซัพพลาย แต่ยังไม่แน่นอน อาจกดดันราคาน้ำมัน
- คะแนน: 4 (2 จากความเป็นไปได้ของ supply เพิ่ม, 2 จากความไม่แน่นอนด้านภูมิรัฐศาสตร์)
- ผลกระทบต่อ ปตท.: กระทบต่อ PTTEP เพราะซัพพลายเพิ่มอาจกดราคาน้ำมัน กระทบรายได้ upstream
- เหตุผลคะแนนรวม:
  - 2 คะแนน: ซัพพลายเพิ่มอาจกดราคาน้ำมัน
  - 2 คะแนน: ความไม่แน่นอนทางภูมิรัฐศาสตร์ยังสูง

[ตัวอย่าง 4]
อินพุต:
หัวข้อข่าว: Russia’s Fuel Exports Plummeted in July
สรุปย่อ: การส่งออกน้ำมันกลั่นของรัสเซียลดลง 6.6% จากดีมานด์ในประเทศเพิ่ม
เนื้อหาข่าว: ส่งผลต่อตลาดโลกเล็กน้อย

เอาต์พุต:
- สรุปข่าว: รัสเซียลดส่งออกน้ำมันกลั่นเพราะใช้ในประเทศมากขึ้น ผลต่อตลาดโลกจำกัด
- คะแนน: 2 (1 จาก supply น้ำมันกลั่นโลกหดเล็กน้อย, 1 จากโอกาสหนุนราคาน้ำมันแบบจำกัด)
- ผลกระทบต่อ ปตท.: กระทบ PTTEP เล็กน้อยผ่านราคาน้ำมัน และอาจกระทบ PTTGL ทางอ้อมต่อโครงสร้างราคา LNG
- เหตุผลคะแนนรวม:
  - 1 คะแนน: supply น้ำมันกลั่นลดอาจหนุนราคาเล็กน้อย
  - 1 คะแนน: ผลต่อราคาเชื้อเพลิงอื่นมีจำกัด

[ตัวอย่าง 5]
อินพุต:
หัวข้อข่าว: Green Hydrogen Revolution: How the Global South Powers the Energy Transition
สรุปย่อ: รายงาน IRENA ชี้บทบาทประเทศกำลังพัฒนาในไฮโดรเจนสีเขียว ส่งผลต่อพลังงานในระยะยาว
เนื้อหาข่าว: -

เอาต์พุต:
- สรุปข่าว: เทรนด์ไฮโดรเจนสีเขียวในประเทศกำลังพัฒนามีแนวโน้มเติบโต อาจสร้างตลาดพลังงานใหม่ระยะยาว
- คะแนน: 4 (2 จากโอกาสขยายตลาดพลังงานสะอาด, 2 จากแรงกดดันให้ปรับพอร์ตลงทุนของ PTT)
- ผลกระทบต่อ ปตท.: กระทบต่อ PTTEP, PTTLNG, PTTGL, PTTNGD ในเชิงกลยุทธ์การเปลี่ยนผ่านพลังงาน
- เหตุผลคะแนนรวม:
  - 2 คะแนน: โอกาสเติบโตของตลาดไฮโดรเจนสีเขียว
  - 2 คะแนน: ความจำเป็นปรับกลยุทธ์/พอร์ตลงทุนของ PTT
"""

# ---------- Gemini Call Wrapper ----------
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
                import re as _re
                m = _re.search(r'retry_delay\s*{[^}]*seconds:\s*(\d+)', err_str)
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

# ---------- News fetchers ----------
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

# ---------- LLM prompts ----------
def gemini_summary_and_score(news):
    prompt = f"""
{PTT_CONTEXT}
{FEW_SHOT}

หัวข้อข่าว: {news['title']}
สรุปย่อ: {news['summary']}
เนื้อหาข่าว (ถ้ามี): {news.get('detail', '')}

กรุณาทำ 4 อย่างต่อไปนี้ (ยึดรูปแบบคำตอบด้านล่างอย่างเคร่งครัด และเขียนแบบมืออาชีพ กระชับ ชัดเจน):

1. สรุปข่าวนี้เป็นภาษาไทยอย่างกระชับ (1-2 ประโยค)
   - อธิบาย "เหตุการณ์หลัก" และ "กลไก" ต่อราคา/ซัพพลาย/ดีมานด์/ต้นทุน

2. ให้คะแนนความสำคัญของข่าวนี้ต่อกลุ่ม ปตท. (1-5 คะแนน)
   - แจกแจงเหตุผลเป็นรายการสั้น ๆ (ปัจจัย + กลไกที่เชื่อมโยงธุรกิจ)

3. วิเคราะห์ผลกระทบต่อบริษัทในกลุ่ม PTT (อิง Context)
   - ใช้เฉพาะชื่อ: PTTEP, PTTLNG, PTTGL, PTTNGD
   - เลือกบริษัทที่ "เกี่ยวข้องจริง" ไม่เกิน 2 ราย พร้อมเหตุผลเฉพาะเจาะจงเชิงกลไก
   - ห้ามใส่วงเล็บระบุ (+/−/ไม่ชัดเจน) หรือ (สั้น/กลาง/ยาว)

4. แสดงผลลัพธ์ในรูปแบบ **ด้านล่างนี้เท่านั้น**:
- สรุปข่าว: <ข้อความ>
- คะแนน: <คะแนนรวมตัวเลข> (<คะแนนย่อย> จาก..., ...)
- ผลกระทบต่อ ปตท.: กระทบต่อ <ชื่อบริษัท 1, ชื่อบริษัท 2> เพราะ <เหตุผลสั้นแบบมืออาชีพ>
- เหตุผลคะแนนรวม:
  - <คะแนน> คะแนน: <เหตุผล>
  - <คะแนน> คะแนน: <เหตุผล>

เงื่อนไขเพิ่มเติม:
- ห้ามใช้คำกว้าง ๆ เช่น "กระทบต่อราคา" โดยไม่บอกกลไก (เช่น ซัพพลายลด → ราคาเพิ่ม → margin upstream ดีขึ้น)
- คะแนนย่อยต้องรวมกันเท่ากับคะแนนรวม
- รายงาน/เมกะเทรนด์ให้ถือเป็นผลทางอ้อม เลือกเฉพาะบริษัทที่มีเหตุผลเฉพาะ
- ใช้เฉพาะชื่อบริษัท: PTTEP, PTTLNG, PTTGL, PTTNGD
"""
    try:
        resp = call_gemini(prompt)
        return resp.text
    except Exception as e:
        return f"ERROR: {e}"


def is_ptt_related_from_output(out_text: str) -> bool:
    if not out_text or out_text.startswith("ERROR"):
        return False
    out_text = _normalize_colons(out_text)
    m = re.search(r"ผลกระทบต่อ\s*ปตท\.\s*:\s*(.*)", out_text)
    if not m: return False
    val = m.group(1).strip()
    return any(x in val for x in ["PTTEP","PTTLNG","PTTGL","PTTNGD"])


def llm_ptt_subsidiary_impact_filter(news):
    """ฟิลเตอร์ข่าว (นับโควต้าผ่าน call_gemini เสมอ)"""
    prompt = f'''
{PTT_CONTEXT}

คุณคือผู้เชี่ยวชาญด้านการคัดกรองข่าวสำหรับบริษัทในเครือ ปตท.
ตอบเพียง "ใช่" หรือ "ไม่ใช่" เท่านั้น ตามเกณฑ์:
- "ใช่" ถ้าข่าวมีผลโดยตรง/อ้อมต่อ PTTEP, PTTLNG, PTTGL, หรือ PTTNGD
- แม้ไม่มีชื่อบริษัทในข่าว แต่มีประเด็นที่กระทบธุรกิจตาม Context ข้างต้น ก็ให้ "ใช่"
- ถ้าไม่เกี่ยวข้องกับธุรกิจหลักของบริษัทเหล่านี้ ให้ "ไม่ใช่"

ข่าว:
{news['title']}
{news['summary']}
{news.get('detail', '')}
'''
    try:
        resp = call_gemini(prompt)
        ans = (resp.text or "").strip().replace("\n", "")
        return ans.startswith("ใช่")
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        return False

# ---------- Ranking/Output ----------
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
    import re as _re
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def join_companies(codes):
        codes = codes or []
        if not codes:
            return "ไม่มีระบุ"
        return ", ".join(codes)

    bubbles = []
    for item in news_items:
        bd_text = (item.get("score_breakdown") or "-")
        bd_clean = _re.sub(r"^[-•]\s*", "", bd_text, flags=_re.MULTILINE)

        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm"
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

    # -------- Filter stage --------
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
        if llm_ptt_subsidiary_impact_filter(news):   # นับโควต้าทุกครั้ง
            filtered_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ข่าวที่เกี่ยวข้องกับบริษัทลูก PTT: {len(filtered_news)} ข่าว")
    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้องบริษัทลูก PTT")
        return

    # -------- Analyze stage --------
    ranked = rank_candidates(filtered_news, use_keyword_boost=False)
    top_candidates = ranked[:min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์เพียง {len(top_candidates)} ข่าว (จำกัด 10)")

    ptt_related_news = []
    for news in top_candidates:
        out = gemini_summary_and_score(news)
        out = _normalize_colons(out)
        news['gemini_output'] = out

        m_score  = re.search(r"คะแนน:\s*(\d+)", out or "")
        news['gemini_score'] = int(m_score.group(1)) if m_score else 3

        m_sum    = re.search(r"สรุปข่าว:\s*(.*)", out or "")
        news['gemini_summary'] = m_sum.group(1).strip() if m_sum else "ไม่พบสรุปข่าว"

        m_reason = re.search(r"ผลกระทบต่อ\s*ปตท\.\s*:\s*(.*)", out or "")
        news['gemini_reason'] = _polish_impact_text(m_reason.group(1).strip() if m_reason else "-")

        news['ptt_companies'] = extract_ptt_companies(news.get('gemini_reason', ''))

        m_bd     = re.search(r"เหตุผลคะแนนรวม\s*:\s*(.*)", out or "", flags=re.DOTALL)
        if m_bd:
            score_bd_raw = m_bd.group(1).strip()
            lines = []
            for ln in score_bd_raw.splitlines():
                ln = ln.strip()
                ln = re.sub(r"^[-•]\s*", "", ln)  # ตัด bullet
                if "คะแนน" in ln:
                    lines.append(ln)
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

    ptt_related_news.sort(key=lambda n: (n.get('gemini_score',0), n.get('published', datetime.min)), reverse=True)
    top_news = ptt_related_news[:10]

    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if n["link"] not in sent_links]
    if not top_news_to_send:
        print("ข่าววันนี้กับเมื่อวานส่งครบหมดแล้ว ไม่มีข่าวใหม่")
        return

    for item in top_news_to_send:
        img = fetch_article_image(item["link"]) or ""
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)
    save_sent_links([n["link"] for n in top_news_to_send])
    print("เสร็จสิ้น.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
