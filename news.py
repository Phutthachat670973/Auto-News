import os          # นำเข้าโมดูลมาตรฐานชื่อ os → ใช้จัดการ "สิ่งที่เกี่ยวกับระบบปฏิบัติการ (Operating System)"
                   # เช่น อ่าน/เขียนตัวแปรสภาพแวดล้อม (Environment Variables), จัดการไฟล์/โฟลเดอร์, path ต่าง ๆ

import re          # นำเข้าโมดูล re (regular expression) → ใช้ค้นหา/แทนที่ข้อความตาม pattern ที่ซับซ้อน เช่น ตัดคำในวงเล็บ, ลบ utm_ ออกจาก URL

import json        # นำเข้าโมดูล json → แปลงข้อมูล Python (dict, list ฯลฯ) เป็น JSON string หรือแปลง JSON string กลับเป็น object ใน Python

import time        # นำเข้าโมดูล time → ใช้ฟังก์ชันเกี่ยวกับเวลา เช่น time.sleep() เอาไว้หน่วงเวลา (เช่น ก่อนเรียก API รอบถัดไป)

import random      # นำเข้าโมดูล random → ใช้สุ่มตัวเลข (เช่น สุ่มจำนวนวินาทีที่ใช้หน่วง เพื่อไม่ให้เรียก API ที่เวลาเดิมเป๊ะ ๆ ทุกครั้ง)

from datetime import datetime, timedelta   # นำเข้า class datetime และ timedelta จากโมดูล datetime
                                           # - datetime: ใช้แทนจุดเวลาหนึ่ง ๆ (วันที่+เวลา)
                                           # - timedelta: ใช้แทนช่วงห่างของเวลา (เช่น 1 วัน, 2 ชั่วโมง ฯลฯ)

from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
# จากโมดูล urllib.parse นำเข้า:
# - urlparse   : แยก URL ให้ออกเป็นส่วน ๆ (scheme, netloc, path, query ฯลฯ)
# - urlunparse : ประกอบชิ้นส่วน URL กลับมาเป็น string เดียว
# - parse_qsl  : แปลง query string (เช่น "a=1&b=2") ให้กลายเป็น list ของ (key, value)
# - urlencode  : แปลง list ของ (key, value) ให้กลับเป็น query string

import feedparser   # ไลบรารีภายนอกชื่อ feedparser → ใช้อ่าน RSS/Atom feed, แปลง XML จากเว็บข่าวให้เป็น object ที่ loop ได้ง่าย

from dateutil import parser as dateutil_parser  # นำเข้า parser จาก dateutil แล้วตั้งชื่อเล่นว่า dateutil_parser
                                               # ใช้แปลง string วันที่/เวลา (เช่น "Wed, 20 Nov 2025 10:00:00 GMT") ให้กลายเป็น datetime

import pytz         # ไลบรารี timezone → ใช้สร้าง/จัดการ time zone เช่น Asia/Bangkok, UTC เพื่อให้เวลาไม่ผิดเขต

import requests     # ไลบรารี requests → ใช้ยิง HTTP request (GET/POST/...) ไปยังเว็บหรือ API ต่าง ๆ แทนที่จะใช้ urllib แบบ low level

import google.generativeai as genai  # ไลบรารีสำหรับเรียกใช้ Google Gemini
                                     # ตั้งชื่อเล่นว่า genai เพื่อเรียก genai.configure(...) และ genai.GenerativeModel(...)

# ===== โหลดค่าจาก .env (ถ้ามี) =====
try:
    from dotenv import load_dotenv  # พยายาม import ฟังก์ชัน load_dotenv จากแพ็กเกจ python-dotenv
                                   # ถ้าโปรเจ็กต์นี้มี .env อยู่ ฟังก์ชันนี้จะอ่านไฟล์ .env แล้วเอาค่าไปใส่ใน Environment Variables ให้
    load_dotenv()                  # เรียกใช้ load_dotenv() → โหลดค่าตัวแปรต่าง ๆ จากไฟล์ .env (เช่น GEMINI_API_KEY, LINE_CHANNEL_ACCESS_TOKEN)
except Exception:
    pass                           # ถ้า import ไม่ได้ หรือ error อื่น ๆ เกิดขึ้น (เช่น ไม่มีติดตั้ง python-dotenv) ก็จะ "มองข้าม" ไป ไม่ทำอะไร

# ========================= CONFIG =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# อ่านค่าตัวแปรสภาพแวดล้อมชื่อ "GEMINI_API_KEY"
# - ถ้าไม่เจอให้ใช้ค่า default เป็น "" (สตริงว่าง)
# - จากนั้น .strip() ตัด space/ช่องว่างหัวท้ายออก เผื่อมีการใส่เกินมา
# ค่านี้คือ API key ที่ใช้ยืนยันตัวตนกับบริการ Gemini

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
# อ่านค่าตัวแปรสภาพแวดล้อมชื่อ "LINE_CHANNEL_ACCESS_TOKEN"
# ไว้ใช้เป็น Token ที่จำเป็นสำหรับยิง API ไปหา LINE Messaging API (broadcast ข้อความ)
# strip() เหมือนด้านบน เพื่อลดปัญหาช่องว่างเกินมา

if not GEMINI_API_KEY:
    # ตรวจว่าถ้า GEMINI_API_KEY เป็นค่าว่าง ("" หรือ None) ให้ raise error
    raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")
    # บังคับให้โปรแกรมหยุดทำงาน พร้อมข้อความนี้ → ป้องกันไม่ให้เรียก Gemini โดยไม่มี key

if not LINE_CHANNEL_ACCESS_TOKEN:
    # ถ้าไม่พบ LINE_CHANNEL_ACCESS_TOKEN ก็ให้หยุดเช่นเดียวกัน
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
# อ่านชื่อโมเดลจาก Environment ชื่อ GEMINI_MODEL_NAME
# - ถ้าไม่มี ให้ default เป็น "gemini-2.5-flash"
# - .strip() ตัด space
# - ถ้าผลลัพธ์กลายเป็น "" อีก ก็ใช้ or "gemini-2.5-flash" ซ้ำอีกรอบเพื่อความชัวร์

genai.configure(api_key=GEMINI_API_KEY)
# ตั้งค่าให้ไลบรารี genai รู้ว่าเราจะใช้ API key อะไรในการเรียกโมเดล
# ถ้าไม่ได้เรียก configure ก่อน model.generate_content() อาจจะ error

model = genai.GenerativeModel(GEMINI_MODEL_NAME)
# สร้าง object โมเดล Gemini โดยใช้ชื่อโมเดลที่กำหนดไว้
# ตัวแปร model นี้จะใช้เรียก model.generate_content(prompt) ภายหลัง

GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))
# อ่านตัวแปรสภาพแวดล้อม GEMINI_DAILY_BUDGET → กำหนด "จำนวนครั้งสูงสุด" ที่อนุญาตให้เรียก Gemini ต่อวัน
# - ถ้าไม่มีให้ใช้ default = "250"
# - ครอบด้วย int(...) เพื่อแปลงจาก string เป็นจำนวนเต็ม

MAX_RETRIES = 6
# จำนวนครั้งสูงสุดที่จะ "ลองใหม่" (retry) ถ้าเรียก Gemini แล้วเจอ error ชั่วคราว เช่น 429, 500, 503

SLEEP_BETWEEN_CALLS = (6.0, 7.0)
# กำหนดช่วงเวลาหน่วงระหว่างการเรียก Gemini แต่ละครั้ง (หน่วยวินาที)
# ภายหลังจะใช้ random.uniform(6.0, 7.0) เพื่อสุ่มเวลาหน่วง 6–7 วินาที

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
# ตรวจว่ากำลังรันในโหมด DRY_RUN หรือไม่
# - อ่าน ENV "DRY_RUN" ถ้าไม่มีให้ใช้ "false"
# - แปลงเป็นตัวพิมพ์เล็ก แล้วเปรียบเทียบเท่ากับ "true"
# - ถ้าเป็น "true" → DRY_RUN = True (จะไม่ยิง LINE จริง แค่ print ให้ดู)

bangkok_tz = pytz.timezone("Asia/Bangkok")
# สร้าง object timezone สำหรับเขตเวลา "Asia/Bangkok"
# - pytz.timezone("Asia/Bangkok") หมายถึง "นิยามเขตเวลา" ตามกฎของ IANA time zone database
# - ตัวแปร bangkok_tz นี้เอาไว้ใช้แปลง datetime ให้เป็นเวลาไทย หรือสร้าง datetime ที่ผูกกับเขตเวลาไทยโดยตรง

now = datetime.now(bangkok_tz)
# สร้าง datetime "เวลาปัจจุบัน" โดยอ้างอิง timezone เป็น bangkok_tz (เวลาไทย)
# ถ้าใช้ datetime.now() เฉย ๆ จะได้ "naive datetime" หรืออิง timezone ของระบบ ซึ่งอาจไม่ใช่ Asia/Bangkok
# แต่การส่ง bangkok_tz เข้าไปทำให้ได้ "aware datetime" ที่ระบุชัดเจนว่าเป็นเวลาไทย

S = requests.Session()
# สร้าง session object ของ requests
# - ใช้ร่วมกันตลอด script เพื่อ:
#   * reuse connection (performance ดีขึ้น)
#   * กำหนด header หรือ config อื่น ๆ ครั้งเดียวใช้ได้ทุก request

S.headers.update({"User-Agent": "Mozilla/5.0"})
# กำหนด HTTP header "User-Agent" ให้กับ session S ทุกครั้งที่ยิง request
# - ใช้ "Mozilla/5.0" เพื่อทำตัวเหมือน browser ทั่วไป
# - บางเว็บจะบล็อคถ้า User-Agent ว่าง หรือดูเหมือน script/บอทเกินไป

TIMEOUT = 15
# เวลา timeout สูงสุด (หน่วยวินาที) ในการยิง HTTP request
# ถ้าเกิน 15 วินาทีแล้วยังไม่ตอบ จะ raise exception → ป้องกันไม่ให้ script ค้างนาน

SENT_LINKS_DIR = "sent_links"
# ชื่อโฟลเดอร์ที่จะใช้เก็บไฟล์บันทึกลิงก์ข่าวที่ "ส่งไปแล้ว" (แยกไฟล์ตามวัน)
# ไว้กันการส่งซ้ำภายใน 2 วันล่าสุด

os.makedirs(SENT_LINKS_DIR, exist_ok=True)
# สร้างโฟลเดอร์ชื่อ sent_links ถ้าโฟลเดอร์ยังไม่มี
# - exist_ok=True หมายความว่า ถ้าโฟลเดอร์มีอยู่แล้วไม่ต้อง error ให้ผ่านไปเฉย ๆ

# ========================= Helpers =========================
def _normalize_link(url: str) -> str:
    """
    ฟังก์ชันนี้ทำหน้าที่ "ทำความสะอาด (normalize)" URL
    เพื่อลบพวก parameter ที่ไม่สำคัญต่อเนื้อข่าว เช่น utm_, fbclid, gclid
    จุดประสงค์: ใช้เช็คว่าข่าวซ้ำกันหรือไม่ (ลิงก์เดียวกันแม้ parameter ต่างกันเล็กน้อย)
    """
    try:
        p = urlparse(url)
        # ใช้ urlparse แยก URL เป็นส่วน ๆ แล้วเก็บในตัวแปร p (เป็น ParseResult)
        # ตัวอย่าง: https://example.com/path?a=1&b=2
        # p.scheme = "https", p.netloc="example.com", p.path="/path", p.query="a=1&b=2"

        netloc = p.netloc.lower()
        # เอา netloc (ส่วน domain + port) มาแปลงเป็นตัวพิมพ์เล็กทั้งหมด
        # เพราะ domain ไม่สนตัวใหญ่เล็ก → เพื่อให้เปรียบเทียบได้ง่ายขึ้น

        scheme = (p.scheme or "https").lower()
        # ถ้า URL ไม่มี scheme (เช่น "example.com/...") ให้ default เป็น "https"
        # จากนั้นแปลงเป็นตัวพิมพ์เล็กทั้งหมด เช่น "HTTPS" → "https"

        bad_keys = {"fbclid", "gclid", "ref", "ref_", "mc_cid", "mc_eid"}
        # กำหนด set ของชื่อ query parameter ที่ไม่อยากเก็บไว้
        # เช่น fbclid, gclid, ref ต่าง ๆ ที่เว็บใช้ track แต่ไม่ได้บอกว่าเป็นข่าวคนละอัน

        q = []
        # เตรียม list ว่างไว้เก็บ query parameters ใหม่ที่คัดกรองแล้ว

        for k, v in parse_qsl(p.query, keep_blank_values=True):
            # ใช้ parse_qsl แยก query string เช่น "a=1&b=2" ให้ได้ list ของคู่ (key, value)
            # loop ทีละคู่ (k, v)

            if k.startswith("utm_") or k in bad_keys:
                # ถ้า key เริ่มด้วย "utm_" (utm_source, utm_medium ฯลฯ) หรืออยู่ใน bad_keys
                # → ข้าม ไม่เอา parameter นี้
                continue

            q.append((k, v))
            # ถ้าไม่ใช่พวกที่จะลบทิ้ง → เก็บไว้ใน list q

        return urlunparse(p._replace(scheme=scheme, netloc=netloc, query=urlencode(q)))
        # ประกอบ URL กลับมาใหม่ด้วย urlunparse:
        # - ใช้ p._replace(...) เปลี่ยน scheme, netloc, query
        # - query ใหม่คือ urlencode(q) (แปลง list คู่ (k,v) เป็น query string อีกครั้ง)

    except Exception:
        # ถ้ามี error (เช่น url แปลกมาก parse ไม่ได้)
        return (url or "").strip()
        # ก็คืนค่า URL เดิม (ถ้าเป็น None ให้เป็น "") แล้ว strip ช่องว่างหัวท้ายกันไว้ก่อน


def get_sent_links_file(date=None):
    """
    คืน path ของไฟล์ที่บันทึกลิงก์ข่าวที่ส่งแล้วประจำวันนั้น ๆ
    เช่น ถ้าวันที่เป็น 2025-11-21 → จะได้ "sent_links/2025-11-21.txt"
    """
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
        # ถ้าไม่ได้ส่ง date มา → ใช้วันที่ปัจจุบัน (เวลาไทย) แล้วแปลงเป็น string แบบ "YYYY-MM-DD"

    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")
    # ต่อชื่อโฟลเดอร์ (sent_links) กับชื่อไฟล์วันที่ ให้ได้ path เต็ม
    # เช่น "sent_links/2025-11-21.txt"


def load_sent_links_today_yesterday():
    """
    อ่านลิงก์ข่าวที่ถูกบันทึกไว้ในไฟล์ของ 'วันนี้' และ 'เมื่อวาน'
    แล้วรวมเป็น set เพื่อง่ายต่อการเช็คว่าลิงก์เคยส่งไปแล้วหรือยัง
    """
    sent_links = set()
    # ใช้ set เพราะ:
    # - ไม่ซ้ำ
    # - เช็ค membership เร็ว (O(1))

    for i in range(2):
        # loop i = 0, 1 → หมายถึงวันนี้ (0 วันก่อน) และเมื่อวาน (1 วันก่อน)
        date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        # ใช้ตัวแปร now (ที่เก็บเวลาไทยตอนเริ่มรันไฟล์)
        # - ลบด้วย timedelta(days=i) เพื่อเลื่อนวัน
        # - แปลงเป็น string "YYYY-MM-DD"

        path = get_sent_links_file(date)
        # ได้ path ของไฟล์ที่เก็บลิงก์ของวันนั้น

        if os.path.exists(path):
            # ถ้าไฟล์มีอยู่จริง → เปิดอ่าน
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    # อ่านทุกบรรทัด (แต่ละบรรทัดคือ 1 URL)
                    url = _normalize_link(line.strip())
                    # ตัดช่องว่างหัวท้าย แล้ว normalize URL อีกที (กันกรณีเขียนซ้ำหรือมี utm_ แปลก ๆ)

                    if url:
                        sent_links.add(url)
                        # ถ้า URL ไม่ว่าง → ใส่เข้า set sent_links

    return sent_links
    # คืนค่า set ที่มี URL ทั้งวันนี้ + เมื่อวาน


def save_sent_links(new_links, date=None):
    """
    บันทึกชุดลิงก์ที่ส่งไปแล้วใหม่ ลงในไฟล์ของ 'วันนั้น ๆ'
    โดยเขียนต่อท้ายไฟล์เดิม (append)
    """
    path = get_sent_links_file(date)
    # หาว่าไฟล์ของวันที่ไหนควรเก็บข้อมูล (ถ้า date=None จะใช้วันนี้)

    with open(path, "a", encoding="utf-8") as f:
        # เปิดไฟล์ในโหมด "append" (a) → เขียนต่อท้าย ไม่ลบของเดิม
        for url in new_links:
            f.write(_normalize_link(url) + "\n")
            # เขียน URL แบบ normalize แล้วตามด้วย newline 1 บรรทัดต่อ 1 URL


def _polish_impact_text(text: str) -> str:
    """
    ปรับรูปแบบข้อความ impact_reason ที่ได้จาก LLM ให้สวยและอ่านง่ายขึ้น:
    - ลบส่วนในวงเล็บที่เป็น label พวก (บวก/ลบ/ไม่ชัดเจน/สั้น/กลาง/ยาว)
    - ลดช่องว่างซ้ำ ๆ
    - แก้ pattern พวก ', ,' หรือ ', .' ให้ถูกต้อง
    """
    if not text:
        return text
        # ถ้าข้อความว่าง (None หรือ "") ให้คืนกลับไปเลย ไม่ทำอะไร

    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)
    # ใช้ regex ลบข้อความในวงเล็บ () ที่ภายในมีคำว่า "บวก/ลบ/ไม่ชัดเจน/สั้น/กลาง/ยาว" อยู่
    # เช่น "(ผลกระทบ: บวก ระยะกลาง)" → ถูกลบออก

    text = re.sub(r"\s{2,}", " ", text)
    # แทนที่ช่องว่างที่ติดกันตั้งแต่ 2 ตัวขึ้นไป ด้วยช่องว่างเดียว (" ")
    # เช่น "คำ   นี้" → "คำ นี้"

    text = re.sub(r"\s*,\s*,", ", ", text)
    # จัด format " , ," หรือ ", ," ให้กลายเป็น ", " แบบเดียว

    text = re.sub(r"\s*,\s*\.", ".", text)
    # จัด format " , ." ให้กลายเป็น "." อย่างเดียว

    return text.strip()
    # ตัดช่องว่างหัวท้ายอีกรอบ แล้วส่งข้อความกลับ

# ========================= FEEDS =========================
news_sources = {
    # dict นี้เก็บ "แหล่งข่าว" หลายแหล่งที่ต้องไปดึง RSS
    "Oilprice": {
        "url": "https://oilprice.com/rss/main",  # URL RSS หลักของ Oilprice.com
        "category": "Energy",                    # หมวดหมู่หลัก กำหนดเองว่าเป็น Energy
        "site": "Oilprice"                       # ชื่อสั้น ๆ ของแหล่งข่าว
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
# เวลา loop news_sources.items() จะได้ (key, info)
# - key = "Oilprice", "CleanTechnica", ...
# - info = dict ที่มี url, category, site

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"
# รูป default ที่ใช้ใน Flex Message ถ้าไม่มีรูปข่าวจริง ๆ จากเว็บ

GEMINI_CALLS = 0
# ตัวนับจำนวนครั้งที่เรียก Gemini (model.generate_content)
# ใช้ร่วมกับ GEMINI_DAILY_BUDGET เพื่อไม่ให้เกินงบต่อวัน

COLON_RX = re.compile(r"[：﹕꞉︓⦂⸿˸]")
# เตรียม regex ไว้ค้นหา "เครื่องหมายโคลอนแปลก ๆ" หลายรูปแบบในภาษาต่าง ๆ
# เช่น โคลอนภาษาจีน ฯลฯ เพื่อเปลี่ยนให้เป็น ":" แบบปกติ

def _normalize_colons(text: str) -> str:
    """
    เปลี่ยนเครื่องหมายโคลอนหลากหลายรูปแบบให้กลายเป็น ":" ตัวเดียวแบบ ASCII
    ป้องกันปัญหาฟอนต์หรือการแสดงผล
    """
    return COLON_RX.sub(":", text or "")
    # ถ้า text เป็น None → ใช้ "" แทน
    # แล้วใช้ regex ที่ compile ไว้แทนทุกตัวที่ match ด้วย ":"

def fetch_article_image(url: str) -> str:
    """
    พยายามดึง URL รูปภาพจากหน้าเว็บข่าว (เพื่อใช้ใน Flex Message)
    ขั้นตอน:
    1) GET หน้าเว็บ (HTML)
    2) หาดูใน meta property="og:image"
    3) ถ้าไม่มี ลอง meta name="twitter:image"
    4) ถ้าไม่มีอีก ลองหา <img src="..."> ตัวแรก
    5) คืนค่า URL ของรูป หรือ "" ถ้าหาไม่ได้
    """
    try:
        r = S.get(url, timeout=TIMEOUT)
        # ใช้ session S ยิง GET ไปยัง URL ที่ให้มา
        # กำหนด timeout = TIMEOUT วินาที

        if r.status_code >= 400:
            return ""
            # ถ้า status code >= 400 ถือว่า error → ไม่ต้องพยายามอ่าน HTML ต่อ คืนค่าว่าง

        html = r.text
        # ถ้า OK → เก็บเนื้อหา HTML เป็น string

        m = re.search(
            r'<meta[^>]+property=[\'\"]og:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]',
            html,
            re.I
        )
        # ใช้ regex หา tag <meta ... property="og:image" ... content="รูป">
        # - [^>]+  คือจับทุกตัวนอกจาก ">" ให้มากที่สุด
        # - ([^'"]+) คือกลุ่มที่จะจับ URL รูปจริง ๆ
        # re.I = ignore case

        if m:
            return m.group(1)
            # ถ้าเจอ → คืน URL ของรูปจาก group(1)

        m = re.search(
            r'<meta[^>]+name=[\'\"]twitter:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]',
            html,
            re.I
        )
        # ถ้าไม่เจอ og:image → ลองค้น meta name="twitter:image"
        if m:
            return m.group(1)

        m = re.search(r'<img[^>]+src=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        # ถ้าไม่เจอ meta เลย → ลองหาแท็ก <img src="..."> ตัวแรกในหน้า
        if m:
            src = m.group(1)
            # ดึงค่าจาก src

            if src.startswith("//"):
                # กรณี src เป็นแบบ protocol-relative เช่น "//example.com/image.jpg"
                parsed = urlparse(url)
                return f"{parsed.scheme}:{src}"
                # ต่อ scheme เดิมของหน้า (http/https) เข้ากับ src

            if src.startswith("/"):
                # กรณี src เป็น path เริ่มต้นด้วย "/" เช่น "/images/a.jpg"
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}{src}"
                # ประกอบเป็น URL เต็ม: scheme://domain + path

            return src
            # กรณีอื่น ๆ (เช่น src เป็น URL เต็มอยู่แล้ว) คืนค่าไปเลย

        return ""
        # ถ้าค้นทุกแบบแล้วไม่เจอ → คืนค่าว่าง

    except Exception:
        return ""
        # ถ้าเกิด exception (เช่น request ล้มเหลว, HTML แปลก) → คืนค่าว่าง

# ========================= Upstream & Gas Context =========================
PTT_CONTEXT = """
[บริบทธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของกลุ่ม ปตท. — ฉบับย่อ]
...
"""
# PTT_CONTEXT เป็นตัวแปร string ยาว (multi-line string) เก็บ "บริบท" ของธุรกิจกลุ่ม ปตท.
# ใช้เป็น text ประกอบ prompt ที่ส่งให้ Gemini → เพื่อให้โมเดลเข้าใจ scope, คำจำกัดความ, เกณฑ์คัดข่าว
# ทำให้ผลลัพธ์ที่ได้ "มีกรอบความคิด" ตรงตามที่ต้องการมากขึ้น

# ========================= Gemini Wrapper =========================
def call_gemini(prompt, max_retries=MAX_RETRIES):
    """
    ฟังก์ชันกลางสำหรับเรียกโมเดล Gemini:
    - ตรวจงบ GEMINI_DAILY_BUDGET
    - จัดการ retry ถ้าเจอ error ชั่วคราว (429, 500, 503 ฯลฯ)
    - ถ้าสำเร็จ → คืน object resp จาก model.generate_content
    """
    global GEMINI_CALLS
    # ประกาศว่าจะใช้ตัวแปร global GEMINI_CALLS เพื่อเพิ่มค่าจากในฟังก์ชันนี้

    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        # ถ้าจำนวนการเรียกตอนนี้ >= งบที่กำหนดไว้ต่อวัน
        raise RuntimeError(f"ถึงงบ Gemini ประจำวันแล้ว ({GEMINI_CALLS}/{GEMINI_DAILY_BUDGET})")
        # ให้หยุด และแจ้งว่าถึงงบแล้ว

    last_error = None
    # ใช้เก็บ error ล่าสุดเผื่อต้อง raise ทิ้งตอนหมด retry

    for attempt in range(1, max_retries + 1):
        # ลองเรียกตั้งแต่ attempt = 1 ถึง max_retries
        try:
            resp = model.generate_content(prompt)
            # เรียก Gemini ด้วย prompt ที่เราเตรียมไว้
            GEMINI_CALLS += 1
            # เพิ่มตัวนับจำนวนการเรียก 1 ครั้ง
            return resp
            # ถ้าไม่ error → คืนผลลัพธ์ทันที

        except Exception as e:
            err_str = str(e)
            # แปลง exception เป็น string เพื่อเช็คข้อความข้างใน

            if attempt < max_retries and any(x in err_str for x in ["429", "exhausted", "temporarily", "unavailable", "deadline", "500", "503"]):
                # ถ้ายังเหลือโอกาส retry และ error น่าจะเป็นแบบชั่วคราว (เช่น 429 Too Many Requests, server busy)
                time.sleep(min(60, 5 * attempt))
                # รอสักพักก่อนลองใหม่:
                # - 5 วินาที * attempt (เช่นครั้งที่ 1 รอ 5 วินาที, ครั้งที่ 2 รอ 10 วินาที)
                # - แต่ไม่เกิน 60 วินาที
                continue
                # ข้ามไป attempt ถัดไป

            last_error = e
            # ถ้าไม่ใช่ error ชั่วคราว หรือเกินเงื่อนไขด้านบน → เก็บ error ไว้ก่อน

            if attempt < max_retries:
                time.sleep(3 * attempt)
                # ถ้ายังเหลือรอบให้ลองใหม่อยู่ แต่ไม่เข้ากลุ่ม error ข้างบน
                # ก็ยัง retry ได้ โดยรอ 3, 6, 9,... วินาทีแล้วไป attempt ถัดไป
            else:
                raise last_error
                # ถ้าหมดรอบ retry แล้ว → ยอมแพ้ raise error ออกไปให้คนเรียกจัดการ

    raise last_error
    # เผื่อหลุดจาก loop โดยไม่ return (เป็น safety) ก็ raise error ทิ้งเหมือนกัน

# ===== Filter: ใช่/ไม่ใช่ =====
def llm_ptt_subsidiary_impact_filter(news):
    """
    ใช้ Gemini ช่วยตัดสินว่า "ข่าวนี้เกี่ยวข้องอย่างมีนัยสำคัญ" กับ Upstream/ก๊าซของ PTT หรือไม่
    - อินพุตคือ dict news ที่มี title, summary, detail
    - โมเดลจะตอบแค่ "ใช่" หรือ "ไม่ใช่"
    - ฟังก์ชันนี้คืนค่า True ถ้าเริ่มต้นด้วย "ใช่"
    """
    prompt = f'''
{PTT_CONTEXT}

บทบาทของคุณ: ทำหน้าที่เป็น "News Screener" ...
...
ข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เนื้อหาเพิ่มเติม: {news.get('detail','')}

ให้ตอบสั้น ๆ เพียงคำเดียว: "ใช่" หรือ "ไม่ใช่"
'''
    # สร้างข้อความ prompt โดย:
    # - ใส่บริบท PTT_CONTEXT
    # - ใส่คำอธิบายบทบาท
    # - ใส่ข้อมูลข่าว (หัวข้อ, สรุป, detail)
    # ใช้ f-string เพื่อแทรกค่าจาก news dict

    try:
        resp = call_gemini(prompt)
        # เรียกฟังก์ชันกลาง call_gemini เพื่อให้ Gemini ประมวลผล prompt

        ans = (resp.text or "").strip().replace("\n", "")
        # ดึง text จาก resp (ถ้า resp.text เป็น None ให้ใช้ "")
        # - .strip() เพื่อตัดช่องว่างหัวท้าย
        # - .replace("\n","") ลบ newline ออก

        return ans.startswith("ใช่")
        # ถ้าขึ้นต้นคำตอบด้วย "ใช่" → ถือว่าข่าวนี้เกี่ยวข้อง (True)
        # อย่างอื่น → False

    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        # ถ้าเรียกโมเดลแล้ว error → print ให้ดูเพื่อ debug
        return False
        # และถือว่าไม่ผ่านฟิลเตอร์

# ===== Tag ข่าว: สรุป + บริษัท / ประเด็น / ภูมิภาค =====
def gemini_tag_news(news):
    """
    ใช้ Gemini ช่วย:
    - สรุปข่าวเป็นภาษาไทย
    - ติดแท็ก impact_companies (PTTEP, PTTLNG, PTTGL, PTTNGD, TTM)
    - ระบุ topic_type (supply_disruption, price_move, ...)
    - ระบุ region (global, asia, us, ...)
    - อธิบาย impact_reason เป็นภาษาไทย
    ให้ตอบกลับเป็น JSON ตาม schema ด้านล่าง
    """
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "impact_companies": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD", "TTM"]
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
    # schema นี้ไม่ใช่ของจริงที่ใช้ validate ด้วย library แต่ใช้เป็น "คำอธิบาย" ให้โมเดลรู้ว่ารูปแบบ JSON ที่ต้องการเป็นอย่างไร

    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ...
อินพุตข่าว:
หัวข้อ: {news['title']}
สรุป (จาก RSS): {news['summary']}
เนื้อหาเพิ่มเติม: {news.get('detail','')}

...
ให้ตอบกลับเป็น JSON ตาม schema นี้เท่านั้น:
{json.dumps(schema, ensure_ascii=False)}

...
"""
    # สร้าง prompt ยาว ๆ โดย:
    # - ใส่ PTT_CONTEXT
    # - ใส่ตัวข่าว
    # - แปะ schema ที่แปลงเป็น JSON string ด้วย json.dumps(... ensure_ascii=False) เพื่อให้ภาษาไทยอ่านง่าย

    try:
        resp = call_gemini(prompt)
        # เรียก Gemini

        raw = (resp.text or "").strip()
        # ดึงข้อความดิบจากโมเดล

        if raw.startswith("```"):
            # ถ้าโมเดลตอบมาเป็น code block เช่น ```json ... ```
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()
            # ใช้ regex ลบส่วน head/tail ของ ``` ออกให้เหลือ JSON เพียว ๆ

        data = json.loads(raw)
        # แปลง string JSON ให้เป็น dict ใน Python
        return data

    except Exception as e:
        print("[WARN] JSON parse fail in gemini_tag_news:", e)
        # ถ้า parse ไม่ผ่าน หรือเรียกโมเดลแล้วมีปัญหา → print เตือน

        return {
            "summary": news.get("summary") or news.get("title") or "ไม่สามารถสรุปข่าวได้",
            "impact_companies": [],
            "topic_type": "other",
            "region": "other",
            "impact_reason": "fallback – ใช้สรุปจาก RSS แทน"
        }
        # คืนค่า fallback แบบง่าย ๆ
        # - summary ใช้จาก RSS หรือ title
        # - ไม่ระบุ impact_companies
        # - topic_type/region = other
        # - impact_reason = ข้อความ fallback

# ========================= Logic =========================
def is_ptt_related_from_output(impact_companies) -> bool:
    """
    พิจารณาจาก output ที่ tag มาแล้ว:
    - ถ้า list impact_companies ไม่ว่าง → แปลว่าข่าวนี้ผูกกับบริษัทในเครือ PTT โดยตรง
    """
    return bool(impact_companies)
    # bool(list) จะเป็น True ถ้า list มีอย่างน้อย 1 element

def fetch_news_yesterday_full_day():
    """
    ดึงข่าวจาก RSS ทุกแหล่ง ของช่วงเวลา:
    - ตั้งแต่ 00:00 ของ "เมื่อวาน" (เวลาไทย)
    - จนถึง 00:00 ของ "วันนี้" (เวลาไทย)
    แล้วคัดข่าวซ้ำออกตาม URL ที่ normalize แล้ว
    คืนค่าเป็น list ของ dict ข่าว
    """
    now_local = datetime.now(bangkok_tz)
    # เวลาปัจจุบันแบบ aware (timezone = Asia/Bangkok)

    end_time = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    # end_time = เที่ยงคืนของวันนี้ (เช่นถ้าวันนี้ 21 พ.ย. เวลา 10:00 → end_time = 21 พ.ย. 00:00)

    start_time = end_time - timedelta(days=1)
    # start_time = เที่ยงคืนของเมื่อวาน (เช่น 20 พ.ย. 00:00)

    all_news = []
    # list เก็บข่าวทั้งหมดจากทุก feed ก่อนคัดซ้ำ

    for _, info in news_sources.items():
        # loop ผ่านทุกแหล่งข่าวใน news_sources
        # _ = key (ไม่ใช้), info = dict ที่มี url/category/site
        try:
            feed = feedparser.parse(info["url"])
            # ใช้ feedparser ดึง RSS จาก URL แล้ว parse เป็น object feed

            for entry in feed.entries:
                # loop ผ่านแต่ละข่าว (entry) ใน feed.entries

                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
                # พยายามดึง string วันที่เผยแพร่:
                # - ก่อนใช้ entry.published ถ้าไม่มีค่อยลอง entry.updated
                # - ถ้าไม่เจอทั้งคู่ → pub_str = None

                if not pub_str and getattr(entry, "published_parsed", None):
                    # กรณีไม่มี pub_str แต่มี published_parsed (struct_time)
                    t = entry.published_parsed
                    pub_dt = datetime(*t[:6], tzinfo=pytz.UTC).astimezone(bangkok_tz)
                    # แปลง struct_time t เป็น datetime โดยใช้ปี,เดือน,วัน,ชั่วโมง,นาที,วินาที (t[:6])
                    # กำหนด tzinfo=pytz.UTC แล้วแปลงมาเป็นเวลาไทย bangkok_tz

                else:
                    if not pub_str:
                        continue
                        # ถ้าไม่มีข้อมูลเวลาเลย → ข้ามข่าวนี้

                    pub_dt = dateutil_parser.parse(pub_str)
                    # ใช้ dateutil_parser.parse แปลง string เวลาเป็น datetime

                    if pub_dt.tzinfo is None:
                        pub_dt = pytz.UTC.localize(pub_dt)
                        # ถ้า datetime ที่ได้ "ไม่มี timezone" (naive) → สมมติว่าเป็นเวลา UTC แล้วใส่ tzinfo ให้

                    pub_dt = pub_dt.astimezone(bangkok_tz)
                    # แปลง datetime จาก timezone เดิมมาเป็นเวลาไทย

                if not (start_time <= pub_dt < end_time):
                    continue
                    # ถ้าเวลาข่าวไม่อยู่ในช่วง [start_time, end_time) → ข้าม

                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                # ดึงสรุปจาก entry.summary ถ้าไม่มีให้ใช้ entry.description
                # ถ้ายังไม่มีให้ใช้ "" (string ว่าง)

                link = getattr(entry, "link", "")
                # ดึงลิงก์ข่าว

                title = getattr(entry, "title", "-")
                # ดึงหัวข้อข่าว ถ้าไม่มีให้ใช้ "-"

                all_news.append({
                    "site": info["site"],               # ชื่อแหล่งข่าว
                    "category": info["category"],       # หมวดหมู่ Energy/Economy
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": pub_dt,                # datetime เวลาไทย
                    "date": pub_dt.strftime("%d/%m/%Y %H:%M"),  # string เวลาไทยเอาไปแสดงใน Flex
                })

        except Exception as e:
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")
            # ถ้าอ่าน feed แหล่งใดล้มเหลว → print เตือน แต่ไม่หยุดทั้งโปรแกรม

    seen, uniq = set(), []
    # seen = set ไว้เก็บลิงก์ที่เจอแล้ว (normalize)
    # uniq = list ข่าวที่ผ่านการคัดซ้ำแล้ว

    for n in all_news:
        key = _normalize_link(n.get("link", ""))
        # normalize URL ของข่าว

        if key and key not in seen:
            # ถ้ามี key และยังไม่เคยเจอใน seen
            seen.add(key)
            uniq.append(n)
            # เพิ่ม key เข้า seen และเพิ่มข่าวนี้เข้า uniq

    return uniq
    # คืน list ข่าวแบบไม่ซ้ำตาม URL

# --------- Coverage-first selection ----------
KEY_COMPANIES = ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD", "TTM"]
# รายชื่อบริษัทในเครือที่เราโฟกัสเวลาคัดข่าวให้กระจาย coverage

KEY_TOPICS = ["supply_disruption", "price_move", "policy", "investment", "geopolitics"]
# topic ที่อยากให้มี coverage ครบ ๆ ในชุดข่าวที่ส่ง

def select_news_coverage_first(news_list, max_items=10):
    """
    เลือกข่าวจาก news_list โดยมีกลยุทธ์:
    1) พยายามให้ทุกบริษัทใน KEY_COMPANIES มีข่าว (ถ้ามี)
    2) พยายามให้ทุก topic ใน KEY_TOPICS มีข่าว (ถ้ามี)
    3) ถ้าเหลือช่องว่าง → เติมข่าวที่เหลือจากใหม่ไปเก่า
    """
    if not news_list:
        return []

    sorted_news = sorted(news_list, key=lambda n: n.get("published"), reverse=True)
    # เรียงข่าวจากใหม่ไปเก่า ตาม field "published"

    selected = []
    used_ids = set()
    # selected = ข่าวที่เลือกแล้ว
    # used_ids = set เก็บ "ตัวระบุ" ของข่าวที่เลือกแล้ว (ใช้ URL normalize หรือ id object)

    def _add_if_not_selected(candidate):
        """
        ฟังก์ชันย่อย: พยายามเพิ่มข่าวหนึ่งชิ้นเข้า selected
        - กันไม่ให้เกิน max_items
        - กันไม่ให้เลือกข่าวซ้ำ
        """
        key = _normalize_link(candidate.get("link", "")) or id(candidate)
        # ใช้ URL normalize เป็น key; ถ้าไม่มี กำหนด fallback เป็น id(candidate) (เลขอ้างอิง object ใน memory ช่วงรันนี้)

        if key in used_ids:
            return False
            # ถ้าข่าวนี้ถูกเลือกไปแล้ว → ไม่เพิ่ม

        if len(selected) >= max_items:
            return False
            # ถ้าเลือกครบ max_items แล้ว → ไม่เพิ่ม

        selected.append(candidate)
        used_ids.add(key)
        return True

    # รอบที่ 1: coverage ตามบริษัท
    for comp in KEY_COMPANIES:
        if len(selected) >= max_items:
            break

        for n in sorted_news:
            companies = n.get("ptt_companies") or []
            if comp in companies:
                if _add_if_not_selected(n):
                    break
                    # ถ้าสามารถเพิ่มข่าวของบริษัท comp นี้ได้แล้ว → ไปบริษัทถัดไป

    # รอบที่ 2: coverage ตาม topic
    for topic in KEY_TOPICS:
        if len(selected) >= max_items:
            break

        if any((x.get("topic_type") == topic) for x in selected):
            # ถ้าใน selected มีข่าว topic นี้อยู่แล้ว → ข้าม ไม่ต้องหาเพิ่ม
            continue

        for n in sorted_news:
            if n.get("topic_type") == topic:
                if _add_if_not_selected(n):
                    break

    # รอบที่ 3: เติมข่าวที่เหลือจนเต็ม max_items
    for n in sorted_news:
        if len(selected) >= max_items:
            break
        _add_if_not_selected(n)

    return selected

# --------- Grouping ข่าวตาม topic + region ----------
def group_related_news(news_list, min_group_size=3):
    """
    เอาข่าวที่ tag แล้วมาจัดกลุ่มเป็น "กลุ่มข่าว" ถ้ามีข่าวใน topic+region เดียวกันมากพอ
    - key ของกลุ่ม = (topic_type, region)
    - ถ้าข่าวในกลุ่มนั้นมี >= min_group_size → รวมเป็นกลุ่ม
    - ถ้าน้อยกว่านั้น → ปล่อยเป็นข่าวเดี่ยวเหมือนเดิม
    """
    buckets = {}
    # dict ที่ mapping key (topic, region) -> list ข่าว

    for n in news_list:
        key = (n.get("topic_type", "other"), n.get("region", "other"))
        buckets.setdefault(key, []).append(n)
        # ใช้ setdefault เพื่อสร้าง list ว่างถ้ายังไม่เคยมี key นี้ แล้ว append ข่าวเข้าไป

    grouped_items = []
    # list ที่จะเก็บทั้ง "กลุ่มข่าว" (is_group=True) และข่าวเดี่ยว (is_group ไม่มี)

    for (topic, region), items in buckets.items():
        if len(items) >= min_group_size:
            # ถ้าข่าวในกลุ่มนี้เยอะพอ → สร้าง group object

            all_companies = []
            for it in items:
                all_companies.extend(it.get("ptt_companies") or [])
            # รวม impact_companies ของทุกข่าวในกลุ่ม

            all_companies = list(dict.fromkeys(all_companies))
            # ใช้ dict.fromkeys เพื่อลบรายการซ้ำ แล้วย้อนกลับเป็น list

            items_sorted = sorted(items, key=lambda x: x.get("published"), reverse=True)
            # เรียงข่าวในกลุ่มจากใหม่ไปเก่า

            anchor = items_sorted[0]
            # เลือกข่าวใหม่สุดเป็น anchor ใช้หัวข้อ/วัน/หมวดหมู่หลัก

            group_obj = {
                "is_group": True,
                "topic_type": topic,
                "region": region,
                "ptt_companies": all_companies,
                "news_items": items_sorted,              # list ข่าวย่อยที่อยู่ในกลุ่ม
                "title": anchor.get("title", "-"),
                "site": "หลายแหล่งข่าว",
                "category": anchor.get("category", ""),
                "date": anchor.get("date", ""),
                "published": anchor.get("published"),
                "link": anchor.get("link", ""),
            }
            grouped_items.append(group_obj)
        else:
            # ถ้าข่าวในกลุ่มนี้น้อยกว่า min_group_size → ไม่รวมกลุ่ม
            grouped_items.extend(items)
            # ใส่ข่าวเดี่ยว ๆ ลงไปแบบเดิม

    return grouped_items

def gemini_summarize_group(group):
    """
    ใช้ Gemini ช่วย "สรุปภาพรวม" ของกลุ่มข่าว (group) ที่มีหลายข่าวอยู่ใน topic+region เดียวกัน
    - news_items = list ข่าวย่อย
    - ส่งหัวข้อ + สรุปย่อยของทุกข่าวเข้าไป
    - ให้โมเดลสรุปออกมาเป็น JSON ที่มี summary + impact_reason (ทั้งคู่เป็นภาษาไทย)
    """
    items = group.get("news_items", [])
    if not items:
        return {
            "summary": "ไม่พบข่าวในกลุ่ม",
            "impact_reason": "-"
        }

    lines = []
    for idx, n in enumerate(items, 1):
        line = f"{idx}. {n.get('title','-')} — {n.get('summary','')}"
        lines.append(line)
    news_block = "\n".join(lines)
    # news_block = ข้อความที่มีหัวข้อ+สรุปของทุกข่าวในกลุ่ม รวมเป็นหลายบรรทัด 1..2..3..

    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ...
กลุ่มข่าว (หัวข้อและสรุปย่อย):
{news_block}

...
ให้ตอบกลับเป็น JSON รูปแบบ:
{{
  "summary": "<...>",
  "impact_reason": "<...>"
}}
...
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
        print("[WARN] JSON parse fail in gemini_summarize_group:", e)
        return {
            "summary": "ไม่สามารถสรุปภาพรวมของกลุ่มข่าวได้",
            "impact_reason": "-"
        }

# --------- Labels & Human-friendly text ----------
TOPIC_LABELS_TH = {
    "supply_disruption": "Supply ขัดข้อง/ลดลง",
    "price_move": "ราคาน้ำมัน/ก๊าซเปลี่ยน",
    "policy": "นโยบาย/กฎหมาย",
    "investment": "โครงการลงทุน/M&A",
    "geopolitics": "ภูมิรัฐศาสตร์/สงคราม",
    "other": "อื่น ๆ ที่เกี่ยวกับ Upstream/ก๊าซ",
}
# map จาก topic_type (อังกฤษ) ไปเป็นคำอธิบายสั้น ๆ ภาษาไทย ไว้ใช้โชว์บน Flex

REGION_LABELS_TH = {
    "global": "Global",
    "asia": "Asia",
    "europe": "Europe",
    "middle_east": "Middle East",
    "us": "US",
    "other": "อื่น ๆ",
}
# map จาก region code → label ภาษาไทย/อังกฤษผสม ใช้แสดงใน Flex

HUMAN_TOPIC_EXPLANATION = {
    "price_move": "ข่าวนี้เกี่ยวกับการเปลี่ยนแปลงราคาน้ำมันหรือก๊าซ ...",
    "supply_disruption": "ข่าวนี้สะท้อนความเสี่ยงด้านซัพพลาย ...",
    "investment": "ข่าวนี้เกี่ยวกับโครงการลงทุนหรือดีลขนาดใหญ่ ...",
    "policy": "ข่าวนี้เกี่ยวกับนโยบาย ภาษี หรือกฎระเบียบ ...",
    "geopolitics": "ข่าวนี้เป็นเหตุการณ์ภูมิรัฐศาสตร์ ...",
    "other": "ข่าวนี้เกี่ยวข้องกับธุรกิจปิโตรเลียมขั้นต้นหรือก๊าซ ...",
}
# ข้อความอธิบาย topic แบบ "ภาษาคน" ให้ผู้อ่านเข้าใจเร็ว ๆ

def create_flex_message(news_items):
    """
    แปลงรายการข่าว (news_items) ให้กลายเป็น payload Flex Message รูปแบบ Carousel สำหรับส่งไป LINE
    - ใส่รูป, หัวข้อ, วันที่, แหล่งข่าว, สรุป, เหตุผลกระทบ PTT
    - ถ้าข่าวไหนเป็นกลุ่ม (is_group=True) จะมีรายการข่าวย่อยด้วย
    - คืนค่าเป็น list ของ object Flex (รองรับกรณีข่าว >10 อัน แบ่งหลาย carousel)
    """
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    # วันที่ปัจจุบัน (เวลาไทย) เอาไว้ใช้ใน altText

    def join_companies(codes):
        # ฟังก์ชันช่วย: แสดงชื่อบริษัทในเครือเป็น string เดียวคั่นด้วย comma
        codes = codes or []
        return ", ".join(codes) if codes else "ไม่มีระบุ"

    bubbles = []
    # list เก็บ "หนึ่ง bubble ต่อหนึ่งข่าวหรือกลุ่มข่าว"

    for item in news_items:
        img = item.get("image") or DEFAULT_ICON_URL
        # ถ้ามี 'image' ใน item → ใช้รูปนั้น, ถ้าไม่มีก็ใช้ DEFAULT_ICON_URL

        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
            # กันกรณี 'image' เป็น string ประหลาดที่ไม่ใช่ URL → fallback เป็นรูป default

        topic_key = item.get("topic_type", "other")
        region_key = item.get("region", "other")
        topic_label = TOPIC_LABELS_TH.get(topic_key, "อื่น ๆ")
        region_label = REGION_LABELS_TH.get(region_key, "อื่น ๆ")
        human_note = HUMAN_TOPIC_EXPLANATION.get(topic_key, HUMAN_TOPIC_EXPLANATION["other"])

        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm"
        }

        meta_line = {
            "type": "text",
            "text": f"ประเภท: {topic_label} | ภูมิภาค: {region_label}",
            "size": "xs",
            "color": "#555555",
            "wrap": True,
            "margin": "sm"
        }

        group_sublist_box = None
        # ถ้าเป็นข่าวกลุ่ม เราจะสร้าง box เพิ่มแสดงข่าวย่อย

        if item.get("is_group"):
            sub_items = item.get("news_items", [])[:5]
            # เอาข่าวย่อยมาไม่เกิน 5 รายการ

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

        title_text = item.get("title", "-")
        if item.get("is_group"):
            count_sub = len(item.get("news_items", []))
            title_text = f"{topic_label} ({region_label}) – {count_sub} ข่าวสำคัญ"
            # ถ้าเป็นกลุ่มข่าว ให้เปลี่ยนหัวข้อเป็นสไตล์ "Supply ขัดข้อง ... – X ข่าวสำคัญ"

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
                "text": "***",
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

        if group_sublist_box:
            body_contents.append(group_sublist_box)

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

    carousels = []
    for i in range(0, len(bubbles), 10):
        # LINE Flex Carousel จำกัดไม่ให้มี bubble เกิน 12 ชิ้น (ที่นี่เลือกใช้ 10 เพื่อเผื่อ)
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
    ส่ง Flex Message แบบ broadcast ไปยัง LINE Official Account
    - access_token = LINE_CHANNEL_ACCESS_TOKEN
    - flex_carousels = list ของ payload ที่สร้างจาก create_flex_message
    """
    url = 'https://api.line.me/v2/bot/message/broadcast'
    # endpoint สำหรับ broadcast ข้อความไปหา follower ทั้งหมด

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    # ตั้ง header:
    # - Content-Type: JSON
    # - Authorization: Bearer <channel access token>

    for idx, carousel in enumerate(flex_carousels, 1):
        # loop ทีละ carousel (เผื่อกรณีมีมากกว่า 1 หน้า)
        payload = {"messages": [carousel]}
        # ตาม spec LINE broadcast ต้องส่ง {"messages": [ ... ]}

        if DRY_RUN:
            print(f"[DRY_RUN] Carousel #{idx}: {json.dumps(payload)[:500]}...")
            # ถ้าอยู่ในโหมด DRY_RUN → แค่ print payload บางส่วนให้ดู (ไม่ยิง API จริง)
            continue

        try:
            resp = S.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            # ยิง POST ไปยัง LINE broadcast API
            print(f"Broadcast #{idx} status:", resp.status_code, getattr(resp, "text", ""))

            if resp.status_code >= 300:
                break
                # ถ้ามี error (>=300) → หยุดไม่ส่ง carousel ต่อไปแล้ว

            time.sleep(1.2)
            # หน่วงเล็กน้อยเผื่อไม่ให้ยิงติด ๆ กันเร็วเกินไป

        except Exception as e:
            print("[LINE ERROR]", e)
            break
            # ถ้าเกิด error (เช่น network) → print แล้วหยุด loop

# ========================= MAIN =========================
def main():
    """
    Workflow หลักทั้งโปรเซส:
      1) ดึงข่าวทั้งวันของเมื่อวาน (00:00 เมื่อวาน – 00:00 วันนี้)
      2) ใช้ Gemini filter ว่าข่าวไหน "เข้า scope Upstream/Gas ของ PTT"
      3) ใช้ Gemini tag ข่าว (summary + impact_companies + topic + region + impact_reason)
      4) นำข่าวที่เกี่ยวข้องมาจัดกลุ่มตาม topic+region
      5) ถ้าเป็นกลุ่ม ให้อีกทีใช้ Gemini สรุปภาพรวมของกลุ่ม
      6) เลือก top ข่าวแบบ coverage-first (กระจายบริษัทและ topic)
      7) เช็คว่าข่าวเคยส่งใน 2 วันล่าสุดหรือยัง → กันซ้ำ
      8) ไปดึงรูปประกอบจากเว็บข่าว
      9) สร้าง Flex Message และ broadcast ทาง LINE
      10) บันทึก link ข่าวที่ส่งแล้วของวันนี้ไว้ในไฟล์
    """

    all_news = fetch_news_yesterday_full_day()
    # เรียกฟังก์ชันดึงข่าวทุกแหล่งในช่วงเวลา "ทั้งวันเมื่อวาน"
    print(f"ดึงข่าวทั้งวันของเมื่อวาน (00:00–24:00): {len(all_news)} รายการ")

    if not all_news:
        print("ไม่พบข่าว")
        return

    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS
    # แตก tuple (6.0, 7.0) เป็นตัวแปร SLEEP_MIN=6.0, SLEEP_MAX=7.0
    # ใช้เป็นช่วงเวลาในการ random sleep ระหว่างเรียกโมเดล

    # 2) Filter ด้วย Gemini
    filtered_news = []
    for news in all_news:
        news['detail'] = news['title'] if len((news.get('summary') or '')) < 50 else ''
        # เตรียม field 'detail' ส่งเข้า LLM:
        # - ถ้า summary สั้นกว่า 50 ตัวอักษร → ให้เอา title มาใส่ใน detail เผื่อ LLM มี context เพิ่ม
        # - ถ้า summary ยาวพอ → ไม่ต้องใช้ detail

        if llm_ptt_subsidiary_impact_filter(news):
            # ถ้า LLM ตอบว่า "ใช่" → ข่าวนี้เกี่ยวข้องกับ Upstream/Gas
            filtered_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
        # หน่วงแบบสุ่ม 6–7 วินาที เพื่อลดโอกาสโดน rate limit

    print(f"ข่าวผ่านฟิลเตอร์ (เกี่ยวข้อง Upstream/Gas): {len(filtered_news)} ข่าว")

    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้อง")
        return

    # 3) Tagging ด้วย Gemini
    tagged_news = []
    print(f"ส่งให้ Gemini ติดแท็ก {len(filtered_news)} ข่าว")

    for news in filtered_news:
        tag = gemini_tag_news(news)
        # เรียก Gemini ให้ช่วยสรุป+ติดแท็ก

        news['gemini_summary'] = _normalize_colons(tag.get('summary', '')).strip() or 'ไม่พบสรุปข่าว'
        # เก็บ summary ที่ LLM ให้ไว้ใน news['gemini_summary']
        # - normalize เครื่องหมายโคลอน
        # - strip ช่องว่าง
        # - ถ้าว่างให้ข้อความ fallback "ไม่พบสรุปข่าว"

        companies = [c for c in (tag.get('impact_companies') or []) if c in {"PTTEP", "PTTLNG", "PTTGL", "PTTNGD", "TTM"}]
        news['ptt_companies'] = list(dict.fromkeys(companies))
        # ดึง impact_companies ที่มาจากชุดที่เรารู้จักเท่านั้น
        # ใช้ dict.fromkeys เพื่อลบ duplications แล้วแปลงกลับเป็น list

        news['topic_type'] = tag.get('topic_type', 'other')
        news['region'] = tag.get('region', 'other')

        news['gemini_reason'] = _polish_impact_text(tag.get('impact_reason', '').strip()) or '-'
        # เก็บสาเหตุผลกระทบในรูปแบบที่ผ่าน _polish_impact_text แล้ว
        # ถ้าว่าง → ใช้ '-'

        if is_ptt_related_from_output(news['ptt_companies']):
            # ถ้าข่าวนี้มีการผูกกับบริษัทในเครือ PTT อย่างน้อย 1 ตัว → ถือว่าเกี่ยวข้องโดยตรง
            tagged_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
        # หน่วงระหว่างเรียก LLM

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not tagged_news:
        print("ไม่พบข่าวที่ผูกกับบริษัทในเครือ PTT โดยตรง")
        return

    # 4) Grouping
    collapsed_list = group_related_news(tagged_news, min_group_size=3)
    # รวมข่าวที่อยู่ใน topic+region เดียวกันให้เป็นกลุ่ม ถ้ามี >=3 ข่าว

    # 5) meta-summary สำหรับกลุ่มข่าว
    for item in collapsed_list:
        if item.get("is_group"):
            # ถ้าเป็นกลุ่ม → เรียก Gemini อีกทีเพื่อสรุปภาพรวมของทั้งกลุ่ม
            data = gemini_summarize_group(item)
            item["gemini_summary"] = _normalize_colons(data.get("summary", "")).strip()
            item["gemini_reason"] = _polish_impact_text(data.get("impact_reason", "").strip() or "-")

    # 6) Coverage-first selection
    top_news = select_news_coverage_first(collapsed_list, max_items=10)
    # เลือกข่าว/กลุ่มข่าวไม่เกิน 10 ชิ้นด้วยกลยุทธ์ coverage-first

    # 7) กันส่งข่าวซ้ำ 2 วันล่าสุด
    sent_links = load_sent_links_today_yesterday()
    # โหลด URL ที่เคยส่งไปแล้วของวันนี้+เมื่อวาน

    top_news_to_send = [n for n in top_news if _normalize_link(n.get('link', '')) not in sent_links]
    # กรอง top_news ให้เหลือเฉพาะข่าวที่ URL normalize แล้วยังไม่อยู่ใน sent_links

    if not top_news_to_send:
        print("ข่าววันนี้/เมื่อวานส่งครบแล้ว")
        return

    # 8) ดึงรูปประกอบ
    for item in top_news_to_send:
        img = fetch_article_image(item.get("link", "")) or ""
        # พยายามดึงรูปจากหน้าเว็บ

        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img
        # เก็บรูปไว้ใน field "image" ของข่าว

    # 9) แปลงเป็น Flex Message แล้ว broadcast
    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)

    # 10) บันทึกลิงก์ที่ส่งแล้ว
    save_sent_links([n.get("link", "") for n in top_news_to_send])
    print("เสร็จสิ้น.")

# รัน main() เมื่อไฟล์นี้ถูกเรียกโดยตรง (เช่น python script.py)
if __name__ == "__main__":
    try:
        main()
        # ถ้า run script ด้วยคำสั่ง python ชื่อไฟล์.py → บล็อกนี้จะทำงาน เรียก main()
        # แต่ถ้า script นี้ถูก import เป็น module จากไฟล์อื่น → บล็อกนี้จะไม่ทำงาน
    except Exception as e:
        print("[ERROR]", e)
        # ถ้า main() มี error ใด ๆ หลุดออกมา → print ให้เห็นเพื่อ debug
