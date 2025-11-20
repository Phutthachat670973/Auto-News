import os          # โมดูลมาตรฐาน ใช้จัดการตัวแปรสภาพแวดล้อม (env) และไฟล์/โฟลเดอร์ในเครื่อง
import re          # โมดูล regex ใช้ค้นหา/แทนที่ข้อความตาม pattern
import json        # แปลงข้อมูลไป-กลับระหว่าง Python object <-> JSON string
import time        # ใช้ฟังก์ชันเกี่ยวกับเวลา เช่น sleep หน่วงเวลา
import random      # ใช้สุ่มตัวเลข (เช่น สุ่มเวลาหน่วงระหว่างเรียก API)
from datetime import datetime, timedelta   # ใช้คลาส datetime และ timedelta สำหรับจัดการวันเวลา
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode  # แยก/ประกอบ URL และ query string

import feedparser   # ไลบรารีอ่าน RSS feed ข่าว → แปลง RSS เป็น object ที่วน loop ได้
from dateutil import parser as dateutil_parser  # ช่วย parse string วันที่/เวลา ให้กลายเป็น datetime
import pytz         # ไลบรารี timezone (เช่น Asia/Bangkok)
import requests     # ใช้ยิง HTTP request ไปยังเว็บหรือ API ต่าง ๆ
import google.generativeai as genai  # ไลบรารีสำหรับเรียกใช้โมเดล Gemini ของ Google

# ===== โหลดค่าจาก .env (ถ้ามี) =====
# พยายามโหลดไฟล์ .env (ถ้าใช้ตอนรันใน local)
#   - .env มักเก็บพวก secret หรือ config เช่น GEMINI_API_KEY, LINE_ACCESS_TOKEN
#   - ถ้าโหลดไม่ได้ก็ไม่เป็นไร (เช่น ใน environment ที่ไม่มีไฟล์ .env)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ========================= CONFIG =========================
# ส่วนนี้คือ “ตั้งค่าพื้นฐาน” สำหรับทั้งโปรแกรม เช่น API key, timeout, limit ต่าง ๆ

# ดึงค่า GEMINI_API_KEY จาก Environment Variable
#   - ถ้าไม่เจอให้ใช้ "" (string ว่าง) แล้ว strip() เพื่อตัดช่องว่างหัวท้าย
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# ดึงค่า LINE_CHANNEL_ACCESS_TOKEN สำหรับยิง Broadcast ไป LINE
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

# ถ้าไม่มีคีย์ของ GEMINI → โปรแกรมทำงานต่อไม่ได้ → ให้ raise error ทันที
if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")

# ถ้าไม่มี LINE_CHANNEL_ACCESS_TOKEN → ก็ทำงานต่อไม่ได้เช่นกัน เพราะส่ง LINE ไม่ได้
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

# ตั้งชื่อโมเดล Gemini ที่จะใช้
#   - ถ้าใน env มีตัวแปร GEMINI_MODEL_NAME ให้ใช้ตามนั้น
#   - ถ้าไม่มีก็ใช้ค่า default คือ "gemini-2.5-flash"
#   - .strip() ตัดช่องว่าง เผื่อมีเว้นวรรคเกินมา
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

# ตั้งค่า API KEY ให้ไลบรารี Gemini
#   - ทำให้ genai รู้ว่าจะใช้ key ไหนทุกครั้งที่เรียกโมเดล
genai.configure(api_key=GEMINI_API_KEY)

# สร้าง object โมเดล Gemini ล่วงหน้า
#   - เวลาเรียกใช้ จะได้ไม่ต้องสร้างใหม่ทุกครั้ง
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# จำกัดจำนวนครั้งเรียก Gemini ต่อวัน (กันการใช้เกินโควต้า / ป้องกันค่าใช้จ่ายบานปลาย)
#   - อ่านจาก env ชื่อ GEMINI_DAILY_BUDGET ถ้าไม่มีใช้ default = 250
#   - แปลงเป็น int เพื่อไว้เปรียบเทียบ/นับ
GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))

# จำนวนครั้งสูงสุดที่ยอมให้ retry เวลาเรียก Gemini แล้วเจอ error ชั่วคราว
#   - ป้องกันไม่ให้ loop retry ไปเรื่อย ๆ แบบไม่มีที่สิ้นสุด
MAX_RETRIES = 6

# ช่วงเวลา delay แบบสุ่มระหว่างการเรียก Gemini แต่ละครั้ง
#   - ช่วยลดโอกาสโดน rate limit จากฝั่ง API
SLEEP_BETWEEN_CALLS = (6.0, 7.0)

# ถ้าตั้ง DRY_RUN = "true" ใน env:
#   - โปรแกรมจะ “ไม่ยิง” LINE Broadcast จริง
#   - แต่จะแค่ print payload ออก console → ใช้เทสโครงสร้างของข้อความอย่างเดียว
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ตั้ง timezone เป็น Asia/Bangkok (เวลาไทย)
bangkok_tz = pytz.timezone("Asia/Bangkok")

# now = เวลาปัจจุบัน ณ กรุงเทพฯ
#   - ใช้ในการตั้งชื่อไฟล์, log, หรือเวลาที่อ้างอิงในระบบ
now = datetime.now(bangkok_tz)

# ใช้ requests.Session แทนการใช้ requests.get/post ตรง ๆ
#   - Session จะ reuse การเชื่อมต่อ HTTP เดิม ทำให้เรียกเว็บหลาย ๆ ครั้งได้เร็วและประหยัดกว่า
S = requests.Session()

# ตั้ง header User-Agent ให้เหมือน browser ทั่วไป
#   - บางเว็บจะ block ถ้า User-Agent แปลกหรือหายไป
S.headers.update({"User-Agent": "Mozilla/5.0"})

# ตั้ง timeout สูงสุดตอนเรียกเว็บ (วินาที)
#   - กันไม่ให้โปรแกรมค้างถ้าเว็บไม่ตอบ
TIMEOUT = 15

# โฟลเดอร์เก็บ “ลิงก์ข่าวที่ส่งไปแล้ว” แยกตามวัน
#   - ใช้กันส่งลิงก์ข่าวซ้ำ (วันนี้ + เมื่อวาน)
SENT_LINKS_DIR = "sent_links"

# ถ้าโฟลเดอร์ยังไม่มี ให้สร้างขึ้นมาเลย
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

# ========================= Helpers =========================
def _normalize_link(url: str) -> str:
    """
    ฟังก์ชันนี้ทำหน้าที่ “ทำความสะอาด URL” ให้เป็นรูปแบบมาตรฐาน
    เป้าหมาย:
      - ลบ query ที่เป็นพวก tracking (utm_*, fbclid, gclid ฯลฯ)
      - normalize scheme (เช่น http/https) และ netloc (โดเมน) ให้เป็นตัวพิมพ์เล็ก
      - เพื่อตรวจว่าลิงก์ “ซ้ำหรือไม่” ได้ง่ายขึ้น
    ตัวอย่าง:
      https://example.com/news?id=1&utm_source=fb
      → https://example.com/news?id=1
    """
    try:
        # แยก URL ออกเป็นส่วน ๆ (scheme, netloc, path, query ฯลฯ)
        p = urlparse(url)
        netloc = p.netloc.lower()              # ทำโดเมนให้เป็นตัวพิมพ์เล็ก
        scheme = (p.scheme or "https").lower() # ถ้าไม่มี scheme ให้ใช้ "https" เป็นค่า default

        # รายชื่อ query param ที่ถือว่าเป็น "ขยะ tracking" ไม่จำเป็นสำหรับเนื้อหาข่าว
        bad_keys = {"fbclid", "gclid", "ref", "ref_", "mc_cid", "mc_eid"}

        # สร้าง list ใหม่ของ query ที่ต้องการเก็บไว้
        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            # ถ้าเป็นพวก utm_* หรือ key ใน bad_keys → ข้ามทิ้ง
            if k.startswith("utm_") or k in bad_keys:
                continue
            # นอกนั้นเก็บไว้
            q.append((k, v))

        # ประกอบ URL กลับ โดยใช้ scheme/netloc ที่ normalize แล้ว และ query ใหม่ที่ถูกกรอง
        return urlunparse(p._replace(scheme=scheme, netloc=netloc, query=urlencode(q)))
    except Exception:
        # ถ้ามี error ใด ๆ (เช่น url แปลกมาก) → คืน string เดิมแต่ตัดช่องว่างหัวท้าย
        return (url or "").strip()


def get_sent_links_file(date=None):
    """
    คืนชื่อไฟล์ที่ใช้เก็บ “ลิงก์ข่าวที่ส่งแล้ว” ของวันนั้น ๆ
    รูปแบบชื่อไฟล์: sent_links/YYYY-MM-DD.txt

    - ถ้าไม่ส่ง date เข้ามา → ใช้วันที่ปัจจุบัน (เวลาไทย)
    - 1 วัน = 1 ไฟล์
    """
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")


def load_sent_links_today_yesterday():
    """
    โหลดลิงก์ข่าวที่เคยส่งไปแล้วใน:
      - วันนี้
      - เมื่อวาน

    เหตุผล:
      - เพื่อกันไม่ให้ส่งข่าวซ้ำภายในช่วง 2 วันล่าสุด

    วิธีทำ:
      - วน i = 0,1 → วันนี้และเมื่อวาน
      - ถ้าไฟล์ของวันนั้นมีอยู่ → อ่านทุกบรรทัด
      - normalize URL แต่ละบรรทัด → เก็บลง set
    """
    sent_links = set()
    for i in range(2):
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
    """
    เพิ่มลิงก์ข่าวที่เพิ่งส่งไปแล้ว ลงไฟล์ของวันนั้น
    - ใช้ร่วมกับ load_sent_links_today_yesterday เพื่อกันการส่งซ้ำในอนาคต
    """
    path = get_sent_links_file(date)
    with open(path, "a", encoding="utf-8") as f:
        for url in new_links:
            f.write(_normalize_link(url) + "\n")


def _polish_impact_text(text: str) -> str:
    """
    ทำความสะอาดข้อความส่วน impact_reason ก่อนนำไปแสดง:
      - ลบวงเล็บที่มีคำว่า (บวก/ลบ/ไม่ชัดเจน/สั้น/กลาง/ยาว) ออก
        (กันกรณี LLM ใส่ meta เพิ่มมาในวงเล็บ)
      - ลดช่องว่างซ้ำ ๆ ให้เหลืออันเดียว
      - แก้รูปแบบเครื่องหมายจุลภาคและจุด (", ," หรือ ", .") ให้เรียบร้อย
    """
    if not text:
        return text

    # ลบวงเล็บที่มีคำบอกแนวโน้มความยาว/ทิศทางผลกระทบ
    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)
    # แปลงช่องว่างมากกว่า 1 ตัว → เหลือช่องว่างเดียว
    text = re.sub(r"\s{2,}", " ", text)
    # แก้เคส , , → , 
    text = re.sub(r"\s*,\s*,", ", ", text)
    # แก้เคส , . → .
    text = re.sub(r"\s*,\s*\.", ".", text)
    return text.strip()

# ========================= FEEDS =========================
# รายชื่อ RSS feed ที่ใช้เป็นแหล่งข่าว
# - แต่ละ key คือชื่อ source ที่เราเรียกเอง
# - เก็บ URL, หมวดหมู่ใหญ่ ๆ, และชื่อ site ไว้แสดงใน LINE
news_sources = {
    "Oilprice": {
        "url": "https://oilprice.com/rss/main",
        "category": "Energy",
        "site": "Oilprice"
    },
    "CleanTechnica": {
        "url": "https://cleantechnica.com/feed/",
        "category": "Energy",
        "site": "CleanTechnica"
    },
    "HydrogenFuelNews": {
        "url": "https://www.hydrogenfuelnews.com/feed/",
        "category": "Energy",
        "site": "Hydrogen Fuel News"
    },
    "Economist": {
        "url": "https://www.economist.com/latest/rss.xml",
        "category": "Economy",
        "site": "Economist"
    },
    "YahooFinance": {
        "url": "https://finance.yahoo.com/news/rssindex",
        "category": "Economy",
        "site": "Yahoo Finance"
    },
}

# URL รูป default (สำรอง) ถ้าหารูปจากเว็บข่าวไม่ได้
DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ตัวนับจำนวนครั้งที่เรียก Gemini ใน "วันนี้"
#   - ใช้คู่กับ GEMINI_DAILY_BUDGET เพื่อคุม limit
GEMINI_CALLS = 0

# regex สำหรับแปลงเครื่องหมายโคลอนแบบแปลก ๆ ให้กลายเป็น ":" ปกติ
#   - ป้องกันเวลาที่ LLM ตอบมาแล้วใช้ Unicode แปลก ๆ
COLON_RX = re.compile(r"[：﹕꞉︓⦂⸿˸]")


def _normalize_colons(text: str) -> str:
    """
    แทนที่เครื่องหมาย ":" เวอร์ชันแปลก ๆ (fullwidth/ภาษาจีน ฯลฯ)
    ให้เป็น ":" ASCII ปกติทั้งหมด
    """
    return COLON_RX.sub(":", text or "")


def fetch_article_image(url: str) -> str:
    """
    พยายาม "ดึง URL รูปประกอบข่าว" จากหน้าเว็บจริง
    ลำดับการหา:
      1) meta property="og:image"
      2) meta name="twitter:image"
      3) src ของ <img> ตัวแรกในหน้าดังกล่าว
    ถ้าหาอะไรไม่ได้เลย → คืน string ว่าง ""
    """
    try:
        # ยิง HTTP GET ไปดึง HTML หน้าเว็บ
        r = S.get(url, timeout=TIMEOUT)
        if r.status_code >= 400:
            # ถ้าสถานะผิดปกติ (4xx/5xx) ไม่ต้องไป parse ต่อ
            return ""
        html = r.text

        # 1) หา meta og:image
        m = re.search(
            r'<meta[^>]+property=[\'\"]og:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]',
            html,
            re.I
        )
        if m:
            return m.group(1)

        # 2) หา meta twitter:image
        m = re.search(
            r'<meta[^>]+name=[\'\"]twitter:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]',
            html,
            re.I
        )
        if m:
            return m.group(1)

        # 3) หา <img> ตัวแรกจาก HTML
        m = re.search(r'<img[^>]+src=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            src = m.group(1)
            # ถ้าขึ้นต้นด้วย // → ใส่ scheme เดิมจากหน้าเว็บให้ครบ (เช่น https:)
            if src.startswith("//"):
                parsed = urlparse(url)
                return f"{parsed.scheme}:{src}"
            # ถ้าเป็น path แบบ /xxx → เติมโดเมนตาม URL ต้นทาง
            if src.startswith("/"):
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}{src}"
            # กรณีอื่น return ตาม src ตรง ๆ
            return src

        # ถ้าไม่เจออะไรเลย
        return ""
    except Exception:
        # ถ้ามี error (เช่น timeout, parse error) → คืน "" แล้วไปใช้ DEFAULT_ICON_URL ภายหลัง
        return ""

# ========================= Upstream & Gas Context =========================
# ข้อความบริบท PTT_CONTEXT:
#   - ใช้ฝังใน prompt ของ LLM ทุกครั้งที่ให้ช่วยตัดสิน/สรุปข่าว
#   - อธิบาย value chain ของกลุ่ม ปตท. คร่าว ๆ
#   - อธิบายเกณฑ์ 4 ข้อ ว่าข่าวแบบไหนถือว่า "เกี่ยวข้องอย่างมีนัยสำคัญ"
PTT_CONTEXT = """
[บริบทธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของกลุ่ม ปตท. — ฉบับย่อ]

ภาพรวม value chain จากแผนภาพ:
- ปลายต้นน้ำ: การสำรวจและผลิตปิโตรเลียม (ส่วนใหญ่โดย PTTEP) ทั้งในและต่างประเทศ
- การนำเข้า LNG และก๊าซจากต่างประเทศ → ระบบท่อก๊าซธรรมชาติ → โรงแยกก๊าซ → อุปกรณ์/โครงสร้างพื้นฐานก๊าซ
- ปลายน้ำของธุรกิจก๊าซ: โรงไฟฟ้าที่ใช้ก๊าซเป็นเชื้อเพลิง, โรงงานอุตสาหกรรม, ปิโตรเคมี, NGV ฯลฯ
- บริษัทหลักที่เกี่ยวข้อง: PTTEP (Upstream), PTTLNG (นำเข้า/จัดเก็บ LNG), PTTGL/ระบบท่อก๊าซ, PTTNGD (จัดจำหน่ายก๊าซ)

ให้ถือว่าข่าว "เกี่ยวข้องอย่างมีนัยสำคัญกับธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของกลุ่ม ปตท."
ถ้ามีอย่างน้อยหนึ่งข้อจากนี้:

1) ราคาพลังงานเปลี่ยนแรง
   - ราคาน้ำมันดิบ หรือราคาก๊าซ/LNG เปลี่ยนขึ้นหรือลงผิดปกติ
   - มีผลต่อรายได้ของ PTTEP หรือ ต้นทุนนำเข้า LNG/ก๊าซของกลุ่ม ปตท.

2) ซัพพลายก๊าซ/น้ำมันสะดุด
   - การหยุดผลิต/ลดกำลังผลิต, ท่อก๊าซเสีย, โรงแยกก๊าซหรือท่าเรือ/FSRU ใช้งานไม่ได้
   - เหตุฉุกเฉิน/สงคราม/ภัยพิบัติที่ทำให้ปริมาณก๊าซ/น้ำมันในตลาดลดลงหรือไม่แน่นอน

3) โครงการหรือดีลใหญ่ที่เปลี่ยนโครงสร้างตลาด
   - โครงการลงทุนใหม่, FID, M&A, ท่อก๊าซ/โรงแยก/คลัง LNG/โรงไฟฟ้าใหม่
   - ส่งผลให้กำลังการผลิตเพิ่ม/ลด หรือบทบาทของผู้เล่นรายใหญ่เปลี่ยนไป

4) นโยบาย/มาตรการรัฐที่กระทบต้นทุนและความมั่นคงด้านพลังงาน
   - ภาษี, กฎหมาย, มาตรการกำกับ หรือโควตา ที่เกี่ยวกับการสำรวจ ผลิต นำเข้า หรือขายก๊าซ/น้ำมัน
   - มีผลต่อราคาก๊าซ/ค่าไฟ หรือความเสี่ยงในการจัดหาพลังงานของกลุ่ม ปตท.

ถ้าไม่เข้าเกณฑ์ด้านบน เช่น ข่าวการตลาด downstream, PR, promotion, EV ที่ไม่เชื่อม supply–demand หรือความสามารถในการจัดหาพลังงาน
ให้ถือว่า "ไม่ใช่" สำหรับ scope นี้
"""

# ========================= Gemini Wrapper =========================
def call_gemini(prompt, max_retries=MAX_RETRIES):
    """
    ฟังก์ชันกลางสำหรับเรียกใช้ Gemini:
      - เช็คว่ายังไม่ใช้เกินโควตาในวันนั้น (GEMINI_DAILY_BUDGET)
      - ถ้าเรียกแล้ว error ชั่วคราว (เช่น 429, 500, unavailable) จะ retry ใหม่
      - มีการหน่วงเวลาระหว่าง retry เพื่อไม่ให้ spam API

    รับ:
      - prompt: ข้อความที่ส่งให้โมเดล
      - max_retries: จำนวนครั้งสูงสุดที่จะลองใหม่

    คืน:
      - object response จาก model.generate_content(prompt)
    """
    global GEMINI_CALLS

    # ถ้าใช้ครบ budget แล้ว → หยุดทันที
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError(f"ถึงงบ Gemini ประจำวันแล้ว ({GEMINI_CALLS}/{GEMINI_DAILY_BUDGET})")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # เรียกโมเดลด้วย prompt ที่กำหนด
            resp = model.generate_content(prompt)
            # นับจำนวนครั้งที่เรียกโมเดลสำเร็จ
            GEMINI_CALLS += 1
            return resp
        except Exception as e:
            err_str = str(e)
            # ถ้าเป็น error ชั่วคราว (rate limit / unavailable ฯลฯ) ให้ลองใหม่ได้
            if attempt < max_retries and any(x in err_str for x in ["429", "exhausted", "temporarily", "unavailable", "deadline", "500", "503"]):
                # เพิ่มเวลาหน่วงตามจำนวนรอบที่พยายาม (backoff)
                time.sleep(min(60, 5 * attempt))
                continue

            # กรณีอื่น ๆ หรือถึงครั้งสุดท้ายแล้วแต่ยัง error → เก็บ error ไว้
            last_error = e
            if attempt < max_retries:
                time.sleep(3 * attempt)
            else:
                # ลองครบทุกครั้งแล้วยังไม่สำเร็จ → โยน error กลับออกไป
                raise last_error

    # สำรอง เผื่อ logic มาถึงนี้ (จริง ๆ น่าจะไม่ถึง)
    raise last_error

# ===== Filter: ใช่/ไม่ใช่ =====
def llm_ptt_subsidiary_impact_filter(news):
    """
    ใช้ Gemini ช่วยตอบว่า:
      “ข่าวนี้เกี่ยวข้องอย่างมีนัยสำคัญกับ Upstream/ธุรกิจก๊าซ ของกลุ่ม ปตท. หรือไม่?”

    วิธีตอบ:
      - ถ้า LLM ตอบ "ใช่"  → คืนค่า True
      - ถ้าตอบ "ไม่ใช่" หรือเกิด error → คืนค่า False

    news: dict ที่มีอย่างน้อย
      - title
      - summary
      - (option) detail
    """
    prompt = f'''
{PTT_CONTEXT}

บทบาทของคุณ: ทำหน้าที่เป็น "News Screener" ของกลุ่ม ปตท. ด้านปิโตรเลียมขั้นต้นและก๊าซธรรมชาติ

คำตอบที่อนุญาตมีแค่ 2 คำเท่านั้น:
- "ใช่"    = ข่าวนี้เกี่ยวข้องเชิงสาระสำคัญกับ Upstream หรือธุรกิจก๊าซของกลุ่ม ปตท. ตามเกณฑ์ 4 ข้อ
- "ไม่ใช่" = ข่าวนี้เป็น downstream/PR/เรื่องทั่วไปที่ไม่เข้าเกณฑ์ด้านบน

ข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เนื้อหาเพิ่มเติม: {news.get('detail','')}

ให้ตอบสั้น ๆ เพียงคำเดียว: "ใช่" หรือ "ไม่ใช่"
'''
    try:
        # เรียก Gemini ให้ช่วยตัดสินตาม prompt ด้านบน
        resp = call_gemini(prompt)
        ans = (resp.text or "").strip().replace("\n", "")
        # ถ้าขึ้นต้นด้วย "ใช่" ให้ถือว่า True
        return ans.startswith("ใช่")
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        # ถ้ามี error ใด ๆ → ให้ถือว่าข่าวนี้ "ไม่ผ่าน" filter
        return False

# ===== Tag ข่าว: สรุป + บริษัท / ประเด็น / ภูมิภาค =====
def gemini_tag_news(news):
    """
    ใช้ Gemini เพื่อ:
      - เขียนสรุปข่าวแบบย่อ (ภาษาไทย)
      - ติด tag ว่ากระทบบริษัทในเครือใดบ้าง (PTTEP / PTTLNG / PTTGL / PTTNGD)
      - ระบุประเภทข่าว (topic_type)
      - ระบุ region ที่เกี่ยวข้อง (global / asia / us ... )
      - เขียน impact_reason (อธิบายผลกระทบต่อกลุ่ม ปตท.)

    คืนค่า:
      - dict JSON ตาม schema ด้านล่าง
    """
    # schema กำหนด structure ของ JSON ที่อยากให้ LLM ตอบกลับมา
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},  # สรุปข่าวภาษาไทย
            "impact_companies": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]
                }
            },
            "topic_type": {
                "type": "string",
                "enum": [
                    "supply_disruption",
                    "price_move",
                    "policy",
                    "investment",
                    "geopolitics",
                    "other"
                ]
            },
            "region": {
                "type": "string",
                "enum": [
                    "global",
                    "asia",
                    "europe",
                    "middle_east",
                    "us",
                    "other"
                ]
            },
            "impact_reason": {"type": "string"}
        },
        "required": ["summary", "impact_companies", "topic_type", "region", "impact_reason"]
    }

    # prompt ส่งให้ LLM:
    #   - ใส่ PTT_CONTEXT เพื่อให้ LLM เข้าใจบริบท
    #   - อธิบายว่าให้ตอบ JSON เท่านั้น
    #   - กำหนดภาษาและโครงสร้างคำตอบให้ชัดเจน
    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ของกลุ่ม ปตท. (เฉพาะธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติ)
หน้าที่: "สรุปข่าว และติดแท็ก" ตาม value chain และเกณฑ์ 4 ข้อด้านบน

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุป (จาก RSS): {news['summary']}
เนื้อหาเพิ่มเติม: {news.get('detail','')}

ข้อกำหนดด้านภาษา:
- ให้เขียน summary เป็นภาษาไทย
- ให้เขียน impact_reason เป็นภาษาไทย
- อนุญาตให้ใช้ชื่อเฉพาะ/ชื่อบริษัทเป็นภาษาอังกฤษได้ แต่เนื้อความหลักต้องเป็นภาษาไทย
- ห้ามตอบสรุปข่าวเป็นภาษาอังกฤษทั้งหมด

ให้ตอบกลับเป็น JSON ตาม schema นี้เท่านั้น:
{json.dumps(schema, ensure_ascii=False)}

คำอธิบาย field แบบย่อ:
- summary: สรุปว่าเกิดอะไร ที่ไหน เกี่ยวกับน้ำมัน/ก๊าซ/โครงสร้างพื้นฐานอย่างไร (เขียนเป็นภาษาไทย)
- impact_companies: เลือก 0–2 บริษัทจาก ["PTTEP","PTTLNG","PTTGL","PTTNGD"]
- topic_type: ประเภทข่าว (price_move, policy ฯลฯ)
- region: พื้นที่ที่เกี่ยวข้อง (global, asia, us ฯลฯ)
- impact_reason: อธิบายสั้น ๆ ว่าข่าวนี้กระทบกลุ่ม ปตท. ผ่านช่องทางไหน (เขียนเป็นภาษาไทย)

ห้ามตอบอย่างอื่น นอกจาก JSON ตาม schema
"""

    try:
        resp = call_gemini(prompt)
        raw = (resp.text or "").strip()

        # เผื่อกรณี LLM ใส่ ```json ... ``` ครอบไว้ → ต้องลอกออกก่อนค่อย json.loads
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()

        data = json.loads(raw)
        return data

    except Exception as e:
        print("[WARN] JSON parse fail in gemini_tag_news:", e)
        # ถ้า parse ไม่ได้ → ใช้ค่า fallback แบบปลอดภัย
        return {
            "summary": news.get("summary") or news.get("title") or "ไม่สามารถสรุปข่าวได้",
            "impact_companies": [],
            "topic_type": "other",
            "region": "other",
            "impact_reason": "fallback – ใช้สรุปจาก RSS แทน"
        }

# ========================= Logic =========================
def is_ptt_related_from_output(impact_companies) -> bool:
    """
    ฟังก์ชันช่วยเช็คว่า:
      - ถ้า impact_companies มีชื่อบริษัทในเครืออย่างน้อย 1 ตัว → ถือว่าเกี่ยวข้องกับ PTT
      - ถ้าว่าง → ถือว่าไม่เกี่ยวข้อง
    """
    return bool(impact_companies)


def fetch_news_9pm_to_6am():
    """
    ดึงข่าวจาก RSS ทุกแหล่ง ในช่วงเวลา:
      - 21:00 ของเมื่อวาน
      - ถึง 06:00 ของวันนี้ (เวลาไทย)

    ขั้นตอน:
      1) กำหนด start_time / end_time ตาม timezone กรุงเทพฯ
      2) วนทุก source ใน news_sources
         - ใช้ feedparser.parse(url) ดึง feed
         - loop feed.entries
            - แปลงเวลาที่ลงข่าว (published/updated) → datetime
            - แปลง timezone เป็น Asia/Bangkok
            - ถ้าอยู่ในช่วงเวลาเป้าหมาย → เก็บลง all_news
      3) ลบข่าวซ้ำโดยดูจาก URL ที่ normalize แล้ว
      4) คืนค่า list ของข่าวที่อยู่ในช่วงเวลา
    """
    # ------------------------------
    # 1) ตั้งช่วงเวลาเป้าหมาย: เมื่อวาน 21:00 → วันนี้ 06:00
    # ------------------------------
    # เวลาปัจจุบัน (วันนี้) ตาม timezone กรุงเทพฯ
    now_local = datetime.now(bangkok_tz)

    # start_time:
    #   - ย้อนกลับไป 1 วันจากตอนนี้ (เมื่อวานเวลาเดียวกัน)
    #   - แล้วเปลี่ยนเวลาเป็น 21:00:00
    start_time = (now_local - timedelta(days=1)).replace(
        hour=21, minute=0, second=0, microsecond=0
    )

    # end_time:
    #   - ใช้วันที่ "วันนี้" เหมือน now_local
    #   - แต่เปลี่ยนเวลาเป็น 06:00:00
    end_time = now_local.replace(
        hour=6, minute=0, second=0, microsecond=0
    )

    all_news = []

    # ------------------------------
    # 2) ดึงข่าวจากทุก RSS source
    # ------------------------------
    for _, info in news_sources.items():
        try:
            # ใช้ feedparser อ่าน RSS feed
            feed = feedparser.parse(info["url"])

            # วนทุก entry ใน feed
            for entry in feed.entries:
                # พยายามดึงเวลาที่ลงข่าวจาก field ต่าง ๆ
                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)

                if not pub_str and getattr(entry, "published_parsed", None):
                    # กรณีไม่มี published เป็น string แต่มีเป็น struct_time
                    t = entry.published_parsed
                    # แปลง struct_time → datetime แบบ timezone = UTC
                    pub_dt = datetime(*t[:6], tzinfo=pytz.UTC).astimezone(bangkok_tz)
                else:
                    if not pub_str:
                        # ถ้าไม่มีข้อมูลเวลาเลย → ข้ามข่าวนี้
                        continue
                    # ใช้ dateutil_parser.parse() แปลง string → datetime
                    pub_dt = dateutil_parser.parse(pub_str)
                    if pub_dt.tzinfo is None:
                        # ถ้าไม่มี timezone → สมมติว่าเป็น UTC
                        pub_dt = pytz.UTC.localize(pub_dt)
                    # แปลง timezone มาเป็น Asia/Bangkok
                    pub_dt = pub_dt.astimezone(bangkok_tz)

                # ------------------------------
                # 3) เลือกเฉพาะข่าวที่อยู่ในช่วงเวลา 21:00 เมื่อวาน – 06:00 วันนี้
                # ------------------------------
                if not (start_time <= pub_dt <= end_time):
                    # ถ้าอยู่นอกช่วง → ไม่เอา
                    continue

                # ดึง field อื่น ๆ จาก entry
                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                link = getattr(entry, "link", "")
                title = getattr(entry, "title", "-")

                # สร้าง dict เก็บข้อมูลข่าว
                all_news.append({
                    "site": info["site"],
                    "category": info["category"],
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": pub_dt,  # datetime ด้วย
                    "date": pub_dt.strftime("%d/%m/%Y %H:%M"),  # string ไว้แสดงผล
                })
        except Exception as e:
            # ถ้าอ่าน feed จาก source นี้ล้มเหลว → แค่เตือนและไปอ่าน source ถัดไป
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")

    # ------------------------------
    # 4) ลบข่าวซ้ำ: ใช้ URL หลัง normalize เป็น key
    # ------------------------------
    seen, uniq = set(), []
    for n in all_news:
        key = _normalize_link(n.get("link", ""))
        if key and key not in seen:
            seen.add(key)
            uniq.append(n)

    return uniq

# --------- Coverage-first selection ----------
# รายชื่อบริษัทในเครือที่สนใจเป็นหลัก
KEY_COMPANIES = ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]

# ประเภทข่าวหลักที่อยากให้มีตัวแทนครบ
KEY_TOPICS = ["supply_disruption", "price_move", "policy", "investment", "geopolitics"]


def select_news_coverage_first(news_list, max_items=10):
    """
    เลือกข่าว/กลุ่มข่าวโดยใช้แนวคิด "coverage-first":
      1) พยายามให้มีข่าวของแต่ละบริษัทใน KEY_COMPANIES ให้ครอบคลุม
      2) พยายามให้มีข่าวแต่ละประเภท KEY_TOPICS หากเป็นไปได้
      3) ถ้ายังไม่ครบจำนวน max_items → เติมข่าวใหม่ ๆ ลงไปจนครบ

    รับ:
      - news_list: list ของข่าว (รวมทั้งข่าวเดี่ยว และกลุ่มข่าว)
      - max_items: จำนวนสูงสุดที่ต้องการเลือก

    คืน:
      - list ของข่าว/กลุ่มข่าวที่เลือกแล้ว ตามลำดับ
    """
    if not news_list:
        return []

    # เรียงข่าวจากใหม่ไปเก่า (ตาม field 'published')
    sorted_news = sorted(news_list, key=lambda n: n.get("published"), reverse=True)

    selected = []
    used_ids = set()

    def _add_if_not_selected(candidate):
        """
        ฟังก์ชันช่วย:
          - เช็คว่าข่าวนี้ถูกเลือกไปแล้วหรือยัง (ดูจาก URL normalize)
          - ถ้ายัง และจำนวนยังไม่เกิน max_items → เพิ่มเข้า selected
        """
        key = _normalize_link(candidate.get("link", "")) or id(candidate)
        if key in used_ids:
            return False
        if len(selected) >= max_items:
            return False
        selected.append(candidate)
        used_ids.add(key)
        return True

    # รอบที่ 1: พยายามให้มีข่าวของแต่ละบริษัท
    for comp in KEY_COMPANIES:
        if len(selected) >= max_items:
            break
        for n in sorted_news:
            companies = n.get("ptt_companies") or []
            if comp in companies:
                if _add_if_not_selected(n):
                    break

    # รอบที่ 2: พยายามให้มีทุก topic_type สำคัญ
    for topic in KEY_TOPICS:
        if len(selected) >= max_items:
            break
        # ถ้าใน selected มี topic นี้แล้ว → ข้าม
        if any((x.get("topic_type") == topic) for x in selected):
            continue
        # หาใน sorted_news ว่ามีข่าว topic นี้ไหม
        for n in sorted_news:
            if n.get("topic_type") == topic:
                if _add_if_not_selected(n):
                    break

    # รอบที่ 3: เติมข่าวที่เหลือ (ใหม่ ๆ ก่อน) จนเต็มจำนวน max_items
    for n in sorted_news:
        if len(selected) >= max_items:
            break
        _add_if_not_selected(n)

    return selected

# --------- Grouping ข่าวตาม topic + region ----------
def group_related_news(news_list, min_group_size=3):
    """
    จัดกลุ่มข่าวตามคู่ (topic_type, region)
      - ถ้ากลุ่มไหนมีข่าว >= min_group_size → รวมเป็น "กลุ่มข่าว" (is_group=True)
      - ถ้าน้อยกว่านั้น → ปล่อยเป็นข่าวเดี่ยวตามเดิม

    คืนค่า:
      - list ที่มีทั้ง item แบบ:
        - ข่าวเดี่ยว (dict ปกติ)
        - กลุ่มข่าว (dict ที่มี is_group=True, news_items=[...])
    """
    buckets = {}

    # แบ่งข่าวลง bucket ตาม key = (topic_type, region)
    for n in news_list:
        key = (n.get("topic_type", "other"), n.get("region", "other"))
        buckets.setdefault(key, []).append(n)

    grouped_items = []

    for (topic, region), items in buckets.items():
        if len(items) >= min_group_size:
            # ถ้าข่าวในกลุ่มนี้ถึงเกณฑ์ → รวมเป็นกลุ่มข่าว
            all_companies = []
            for it in items:
                all_companies.extend(it.get("ptt_companies") or [])
            # ลบชื่อบริษัทซ้ำ แต่คงลำดับเดิม (ใช้ dict.fromkeys trick)
            all_companies = list(dict.fromkeys(all_companies))

            # เรียงข่าวในกลุ่มจากใหม่ไปเก่า
            items_sorted = sorted(items, key=lambda x: x.get("published"), reverse=True)
            anchor = items_sorted[0]  # ใช้ข่าวใหม่สุดเป็น anchor ของกลุ่ม

            group_obj = {
                "is_group": True,
                "topic_type": topic,
                "region": region,
                "ptt_companies": all_companies,
                "news_items": items_sorted,

                # meta หลักของกลุ่ม → ใช้จากข่าว anchor
                "title": anchor.get("title", "-"),
                "site": "หลายแหล่งข่าว",
                "category": anchor.get("category", ""),
                "date": anchor.get("date", ""),
                "published": anchor.get("published"),
                "link": anchor.get("link", ""),
            }
            grouped_items.append(group_obj)
        else:
            # ถ้ากลุ่มเล็ก (จำนวนข่าวน้อยกว่า min_group_size) → แปลงกลับเป็นรายการข่าวเดี่ยว ๆ
            grouped_items.extend(items)

    return grouped_items


def gemini_summarize_group(group):
    """
    ใช้ Gemini สรุปภาพรวมของ "กลุ่มข่าว" หลายข่าวที่อยู่ใน topic/region เดียวกัน

    รับ:
      - group: dict ที่มี key "news_items" เป็น list ของข่าวย่อย

    คืน:
      - dict: { "summary": "...", "impact_reason": "..." } ภาษาไทย
    """
    items = group.get("news_items", [])
    if not items:
        return {
            "summary": "ไม่พบข่าวในกลุ่ม",
            "impact_reason": "-"
        }

    # รวบรวมรายการหัวข้อ+สรุปของข่าวย่อยทั้งหมดไว้ในบล็อกเดียว (news_block)
    lines = []
    for idx, n in enumerate(items, 1):
        line = f"{idx}. {n.get('title','-')} — {n.get('summary','')}"
        lines.append(line)
    news_block = "\n".join(lines)

    # prompt สำหรับสรุปภาพรวมของทั้งกลุ่มข่าว
    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ที่ต้องสรุป "ภาพรวม" ของชุดข่าวหลายข่าวในประเด็นเดียวกัน
เป้าหมาย: ผู้บริหารอ่านบับเบิลเดียวแล้วเข้าใจภาพรวมของกลุ่มข่าวนี้

กลุ่มข่าว (หัวข้อและสรุปย่อย):
{news_block}

ข้อกำหนดด้านภาษา:
- ให้เขียน summary เป็นภาษาไทย
- ให้เขียน impact_reason เป็นภาษาไทย
- อนุญาตให้มีชื่อประเทศ/ชื่อบริษัท/ชื่อโครงการเป็นภาษาอังกฤษได้
- แต่โดยรวมต้องเป็นประโยคภาษาไทย ไม่ใช่ย่อหน้าอังกฤษล้วน

ให้ตอบกลับเป็น JSON รูปแบบ:
{{
  "summary": "<สรุปภาพรวมของทั้งกลุ่ม 3–5 ประโยค (ภาษาไทย)>",
  "impact_reason": "<สรุปว่ากลุ่มข่าวนี้กระทบกลุ่ม ปตท. ผ่าน upstream/gas อย่างไร (ภาษาไทย)>"
}}

ห้ามตอบอย่างอื่น นอกจาก JSON ตามรูปแบบข้างต้น
"""

    try:
        resp = call_gemini(prompt)
        raw = (resp.text or "").strip()

        # เผื่อ LLM ใส่ ```json ...``` ครอบ → แกะออกก่อน
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()

        data = json.loads(raw)
        return data
    except Exception as e:
        print("[WARN] JSON parse fail in gemini_summarize_group:", e)
        # fallback ถ้า parse ไม่ได้
        return {
            "summary": "ไม่สามารถสรุปภาพรวมของกลุ่มข่าวได้",
            "impact_reason": "-"
        }

# --------- Labels & Human-friendly text ----------
# แผนที่ topic_type → ป้ายภาษาไทยสำหรับแสดงใน LINE
TOPIC_LABELS_TH = {
    "supply_disruption": "Supply ขัดข้อง/ลดลง",
    "price_move": "ราคาน้ำมัน/ก๊าซเปลี่ยน",
    "policy": "นโยบาย/กฎหมาย",
    "investment": "โครงการลงทุน/M&A",
    "geopolitics": "ภูมิรัฐศาสตร์/สงคราม",
    "other": "อื่น ๆ ที่เกี่ยวกับ Upstream/ก๊าซ",
}

# แผนที่ region → ป้ายสั้น ๆ ภาษาไทย/อังกฤษ
REGION_LABELS_TH = {
    "global": "Global",
    "asia": "Asia",
    "europe": "Europe",
    "middle_east": "Middle East",
    "us": "US",
    "other": "อื่น ๆ",
}

# ข้อความอธิบายประเภทข่าว ให้อ่านเข้าใจง่าย
HUMAN_TOPIC_EXPLANATION = {
    "price_move": "ข่าวนี้เกี่ยวกับการเปลี่ยนแปลงราคาน้ำมันหรือก๊าซ ซึ่งอาจกระทบรายได้ของ PTTEP และต้นทุนก๊าซ/LNG ของกลุ่ม ปตท.",
    "supply_disruption": "ข่าวนี้สะท้อนความเสี่ยงด้านซัพพลาย เช่น การหยุดผลิต ท่อก๊าซเสีย หรือเหตุการณ์ที่ทำให้ปริมาณก๊าซ/น้ำมันในตลาดลดลง",
    "investment": "ข่าวนี้เกี่ยวกับโครงการลงทุนหรือดีลขนาดใหญ่ ซึ่งอาจเพิ่มหรือลดกำลังการผลิตในห่วงโซ่พลังงาน",
    "policy": "ข่าวนี้เกี่ยวกับนโยบาย ภาษี หรือกฎระเบียบของภาครัฐ ที่อาจทำให้ต้นทุนหรือความเสี่ยงด้านพลังงานของกลุ่ม ปตท. เปลี่ยนไป",
    "geopolitics": "ข่าวนี้เป็นเหตุการณ์ภูมิรัฐศาสตร์หรือความขัดแย้งระหว่างประเทศ ที่อาจทำให้ตลาดพลังงานผันผวนหรือซัพพลายไม่แน่นอน",
    "other": "ข่าวนี้เกี่ยวข้องกับธุรกิจปิโตรเลียมขั้นต้นหรือก๊าซของกลุ่ม ปตท. ในมุมอื่น ๆ ที่ควรติดตาม",
}


def create_flex_message(news_items):
    """
    แปลงรายการข่าว (เดี่ยว/กลุ่ม) ที่ผ่านการ tag + สรุปแล้ว
    ให้กลายเป็น Flex Message รูปแบบ carousel ของ LINE

    รูปแบบ:
      - 1 ข่าวหรือ 1 กลุ่มข่าว = 1 bubble
      - แต่ละครั้งส่งได้หลาย bubble ในรูปแบบ carousel (สูงสุด ~10 bubble)

    ขั้นตอนคร่าว ๆ:
      1) เตรียมข้อมูลแสดงผล เช่น title, เวลา, site, summary, impact
      2) ถ้าเป็นกลุ่มข่าว → แสดงรายการหัวข้อข่าวย่อยด้วย
      3) สร้าง bubble ตาม Spec Flex Message
      4) แบ่ง bubble เป็นชุด ๆ ชุดละไม่เกิน 10 → สร้างเป็น list ของ carousel
    """
    # วันที่ปัจจุบันสำหรับ altText
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def join_companies(codes):
        """
        เปลี่ยน list เช่น ["PTTEP","PTTLNG"] → "PTTEP, PTTLNG"
        ถ้า list ว่าง → "ไม่มีระบุ"
        """
        codes = codes or []
        return ", ".join(codes) if codes else "ไม่มีระบุ"

    bubbles = []

    for item in news_items:
        # URL รูป: ถ้าไม่มี หรือไม่ใช่ http(s) → ใช้ DEFAULT_ICON_URL
        img = item.get("image") or DEFAULT_ICON_URL
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL

        # ดึง topic/region แล้ว map เป็น label ภาษาไทย
        topic_key = item.get("topic_type", "other")
        region_key = item.get("region", "other")
        topic_label = TOPIC_LABELS_TH.get(topic_key, "อื่น ๆ")
        region_label = REGION_LABELS_TH.get(region_key, "อื่น ๆ")
        human_note = HUMAN_TOPIC_EXPLANATION.get(topic_key, HUMAN_TOPIC_EXPLANATION["other"])

        # แสดงว่าข่าวนี้กระทบบริษัทใดบ้าง
        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm"
        }

        # แสดงประเภทข่าว + ภูมิภาค
        meta_line = {
            "type": "text",
            "text": f"ประเภท: {topic_label} | ภูมิภาค: {region_label}",
            "size": "xs",
            "color": "#555555",
            "wrap": True,
            "margin": "sm"
        }

        # ถ้าเป็นกลุ่มข่าว → เตรียม box สำหรับแสดงรายการข่าวย่อย
        group_sublist_box = None
        if item.get("is_group"):
            # จำกัดจำนวนข่าวย่อยที่แสดงใน bubble นี้ (เช่น 5 ข่าว)
            sub_items = item.get("news_items", [])[:5]
            sub_lines = []
            for sub in sub_items:
                line = f"• [{sub.get('site','')}] {sub.get('title','-')}"
                sub_lines.append(line)
            sub_text = "\n".join(sub_lines) if sub_lines else "-"

            group_sublist_box = {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "ข่าวย่อยในกลุ่มนี้:",
                        "size": "xs",
                        "weight": "bold",
                        "color": "#000000",
                        "wrap": True
                    },
                    {
                        "type": "text",
                        "text": sub_text,
                        "size": "xs",
                        "color": "#444444",
                        "wrap": True
                    }
                ]
            }

        # title สำหรับข่าวเดี่ยว vs กลุ่มข่าว
        title_text = item.get("title", "-")
        if item.get("is_group"):
            count_sub = len(item.get("news_items", []))
            title_text = f"{topic_label} ({region_label}) – {count_sub} ข่าวสำคัญ"

        # ส่วนเนื้อหา (body) ของ bubble
        body_contents = [
            {
                "type": "text",
                "text": title_text,
                "weight": "bold",
                "size": "lg",
                "wrap": True,
                "color": "#111111"
            },
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": f"🗓 {item.get('date','-')}",
                        "size": "xs",
                        "color": "#aaaaaa",
                        "flex": 5
                    },
                    {
                        "type": "text",
                        "text": f"📌 {item.get('category','')}",
                        "size": "xs",
                        "color": "#888888",
                        "align": "end",
                        "flex": 5
                    }
                ]
            },
            {
                "type": "text",
                "text": f"🌍 {item.get('site','')}",
                "size": "xs",
                "color": "#448AFF",
                "margin": "sm"
            },
            impact_line,
            meta_line,
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
                "type": "text",
                "text": "หมายเหตุ:",
                "size": "xs",
                "weight": "bold",
                "color": "#000000",
                "margin": "md",
                "wrap": True,
            },
            {
                "type": "text",
                "text": human_note,
                "size": "xs",
                "color": "#444444",
                "wrap": True,
            },
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "contents": [
                    {
                        "type": "text",
                        "text": "ผลกระทบต่อกลุ่ม ปตท.",
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
                        "weight": "bold"
                    },
                ]
            }
        ]

        # ถ้าเป็นกลุ่มข่าว → แปะ box ข่าวย่อยเพิ่มเติม
        if group_sublist_box:
            body_contents.append(group_sublist_box)

        # ประกอบ bubble ตาม Spec Flex Message ของ LINE
        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": img,
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
                        "text": "หมายเหตุ: การวิเคราะห์อยู่ในช่วงทดสอบ",
                        "size": "xs",
                        "color": "#FF0000",
                        "wrap": True,
                        "margin": "md"
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {
                            "type": "uri",
                            "label": "อ่านต่อ",
                            "uri": item.get("link", "#")
                        }
                    }
                ]
            }
        }
        bubbles.append(bubble)

    # แบ่ง bubbles เป็นหลาย carousel ชุดละไม่เกิน 10 bubble
    carousels = []
    for i in range(0, len(bubbles), 10):
        carousels.append({
            "type": "flex",
            "altText": f"ข่าวเกี่ยวข้องกับ ปตท. {now_thai}",
            "contents": {
                "type": "carousel",
                "contents": bubbles[i:i+10]
            }
        })
    return carousels


def broadcast_flex_message(access_token, flex_carousels):
    """
    ส่ง Flex Message ออก LINE Broadcast:
      - ถ้า DRY_RUN = True → ไม่ยิงจริง แค่ print payload ออกทาง console (ใช้ทดสอบ)
      - ถ้า error (เช่น status_code >= 300) → หยุดส่ง carousel ถัดไป

    รับ:
      - access_token: LINE channel access token
      - flex_carousels: list ของข้อความ (แต่ละอันเป็น flex message ready-to-send)
    """
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }

    for idx, carousel in enumerate(flex_carousels, 1):
        payload = {"messages": [carousel]}

        if DRY_RUN:
            # โหมดทดสอบ: แสดงตัวอย่าง payload เฉพาะบางส่วน
            print(f"[DRY_RUN] Carousel #{idx}: {json.dumps(payload)[:500]}...")
            continue

        try:
            # ยิง POST ไปที่ LINE Broadcast API
            resp = S.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            print(f"Broadcast #{idx} status:", resp.status_code, getattr(resp, "text", ""))

            if resp.status_code >= 300:
                # ถ้าส่งไม่ผ่าน เช่น 400/401/500 → หยุดไม่ส่งอันถัดไป
                break

            # เว้นช่วงเล็กน้อยระหว่างการส่งแต่ละ carousel
            time.sleep(1.2)
        except Exception as e:
            print("[LINE ERROR]", e)
            break

# ========================= MAIN =========================
def main():
    """
    ฟังก์ชันหลัก (workflow ทั้งหมด):

    1) ดึงข่าวช่วงเวลา 21:00 ของเมื่อวาน – 06:00 ของวันนี้
    2) ใช้ Gemini ช่วยกรองว่า:
       - "ข่าวเกี่ยวข้องอย่างมีนัยสำคัญกับ Upstream/Gas ของกลุ่ม ปตท. หรือไม่"
    3) สำหรับข่าวที่ผ่าน filter:
       - ใช้ Gemini ช่วยติดแท็กและเขียนสรุปข่าว + เหตุผลผลกระทบ
    4) รวมข่าวที่มี topic_type + region เหมือนกัน ให้เป็น "กลุ่มข่าว" (ถ้ามีจำนวนมากพอ)
    5) ถ้าเป็นกลุ่มข่าว → ขอ meta-summary อีกครั้ง (สรุปภาพรวมทั้งกลุ่ม)
    6) เลือกข่าว/กลุ่มข่าวแบบ coverage-first (ตามบริษัท + topic)
    7) กันส่งข่าวซ้ำ (วันนี้/เมื่อวาน)
    8) พยายามดึงรูปประกอบข่าวจากหน้าเว็บจริง
    9) สร้าง Flex Message และ broadcast ทาง LINE
    10) บันทึก "ลิงก์ที่ส่งแล้ว" ลงไฟล์ของวันนี้
    """

    # 1) ดึงข่าวช่วงเวลาเป้าหมาย
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")

    if not all_news:
        print("ไม่พบข่าว")
        return

    # เตรียมค่า delay min/max สำหรับการหน่วงเวลาเรียก LLM
    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS

    # 2) Filter: ให้ Gemini ช่วยเช็คว่าเกี่ยวข้อง Upstream/Gas หรือไม่
    filtered_news = []
    for news in all_news:
        # ถ้า summary จาก RSS สั้นเกินไป (< 50 ตัวอักษร) → ใส่ title ให้ LLM ใช้ช่วยตัดสินใน field detail
        news['detail'] = news['title'] if len((news.get('summary') or '')) < 50 else ''

        # ถ้า LLM ตอบว่า "ใช่" → เก็บข่าวนี้ไว้ใน filtered_news
        if llm_ptt_subsidiary_impact_filter(news):
            filtered_news.append(news)

        # หน่วงเวลาสุ่มเล็กน้อย เพื่อไม่ให้เรียก LLM ถี่เกินไป
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ข่าวผ่านฟิลเตอร์ (เกี่ยวข้อง Upstream/Gas): {len(filtered_news)} ข่าว")

    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้อง")
        return

    # 3) Tagging: ใช้ Gemini เขียนสรุป + ติด tag บริษัท/topic/region ให้แต่ละข่าว
    tagged_news = []
    print(f"ส่งให้ Gemini ติดแท็ก {len(filtered_news)} ข่าว")

    for news in filtered_news:
        tag = gemini_tag_news(news)

        # สรุปข่าวจาก LLM แล้ว normalize โคลอนให้เรียบร้อย
        news['gemini_summary'] = _normalize_colons(tag.get('summary', '')).strip() or 'ไม่พบสรุปข่าว'

        # เลือกเฉพาะบริษัท PTT ที่สนใจจาก impact_companies
        companies = [c for c in (tag.get('impact_companies') or []) if c in {"PTTEP", "PTTLNG", "PTTGL", "PTTNGD"}]
        # ลบชื่อบริษัทที่ซ้ำ และคงลำดับด้วย dict.fromkeys
        news['ptt_companies'] = list(dict.fromkeys(companies))

        # topic_type / region จากผล tag
        news['topic_type'] = tag.get('topic_type', 'other')
        news['region'] = tag.get('region', 'other')

        # เกลาข้อความ impact_reason ก่อนเก็บลง news
        news['gemini_reason'] = _polish_impact_text(tag.get('impact_reason', '').strip()) or '-'

        # เลือกเฉพาะข่าวที่มีบริษัทในเครืออย่างน้อย 1 ตัว
        if is_ptt_related_from_output(news['ptt_companies']):
            tagged_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not tagged_news:
        print("ไม่พบข่าวที่ผูกกับบริษัทในเครือ PTT โดยตรง")
        return

    # 4) Grouping: รวมข่าวที่ topic+region เดียวกันให้เป็นกลุ่ม (ถ้ามี >= min_group_size)
    collapsed_list = group_related_news(tagged_news, min_group_size=3)

    # 5) ถ้าเป็นกลุ่มข่าว → ขอ meta-summary จาก Gemini เพิ่ม
    for item in collapsed_list:
        if item.get("is_group"):
            data = gemini_summarize_group(item)
            item["gemini_summary"] = _normalize_colons(data.get("summary", "")).strip()
            item["gemini_reason"] = _polish_impact_text(data.get("impact_reason", "").strip() or "-")

    # 6) เลือกข่าว/กลุ่มข่าวแบบ coverage-first (ไม่เกิน 10 bubble ต่อรอบ broadcast)
    top_news = select_news_coverage_first(collapsed_list, max_items=10)

    # 7) กันส่งข่าวซ้ำ 2 วันล่าสุด
    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if _normalize_link(n.get('link', '')) not in sent_links]

    if not top_news_to_send:
        print("ข่าววันนี้/เมื่อวานส่งครบแล้ว")
        return

    # 8) พยายามดึงรูปประกอบของแต่ละข่าว (ถ้าไม่ได้ → ใช้ DEFAULT_ICON_URL)
    for item in top_news_to_send:
        img = fetch_article_image(item.get("link", "")) or ""
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    # 9) แปลงเป็น Flex Message แล้ว broadcast ทาง LINE
    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)

    # 10) บันทึกลิงก์ที่ส่งไปแล้วของวันนี้
    save_sent_links([n.get("link", "") for n in top_news_to_send])
    print("เสร็จสิ้น.")

# รัน main() เมื่อไฟล์นี้ถูกเรียกโดยตรง (เช่น python script.py)
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # ถ้าเกิด error ใหญ่ ๆ ที่ไม่ถูกจับในจุดอื่น → แสดงออกทาง console
        print("[ERROR]", e)
