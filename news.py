import os          # ใช้จัดการตัวแปรสภาพแวดล้อม (Environment Variables) และไฟล์/โฟลเดอร์บนเครื่อง
import re          # ใช้ Regular Expression (regex) สำหรับค้นหา/แทนที่/ทำความสะอาดข้อความ
import json        # ใช้แปลงข้อมูล Python (dict/list) เป็น JSON string และกลับจาก JSON เป็น object
import time        # ใช้ฟังก์ชัน sleep เพื่อหน่วงเวลา (กันยิง API ถี่เกินไป)
import random      # ใช้สุ่มตัวเลข (เช่น สุ่มเวลาหน่วง เพื่อให้ pattern การเรียก API ดูไม่เป็นบอทเกินไป)
from datetime import datetime, timedelta   # ใช้จัดการวันที่และเวลารวมถึงบวก/ลบเวลา
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode  # ใช้แยก/ประกอบ URL และ query string

import feedparser   # ไลบรารีช่วยอ่าน RSS feed จากเว็บไซต์ข่าว
from dateutil import parser as dateutil_parser  # ช่วย parse string เวลา (เช่น "Wed, 20 Nov 2025 10:00 GMT") เป็น datetime
import pytz         # ใช้จัดการ timezone (เช่น แปลงเวลาเป็น Asia/Bangkok)
import requests     # ไลบรารีมาตรฐานสำหรับส่ง HTTP request ไปยังเว็บ/API
import google.generativeai as genai  # ไลบรารีสำหรับเรียกใช้โมเดล Gemini ของ Google

# ===== โหลดค่าจาก .env (ถ้ามี) =====
# จุดประสงค์:
#   - ให้สามารถเก็บ API KEY ต่าง ๆ ไว้ในไฟล์ .env แทนการ hard-code ในโค้ด
#   - ทำให้โค้ดสามารถย้ายไป run ที่อื่นได้ โดยแค่ตั้งค่า ENV ให้ตรง
try:
    from dotenv import load_dotenv
    load_dotenv()  # โหลดค่าตัวแปร Environment จากไฟล์ .env (ถ้ามีไฟล์นี้อยู่ในโฟลเดอร์)
except Exception:
    # ถ้าไม่มี dotenv หรือโหลดไม่สำเร็จ ก็ไม่เป็นไร (จะไปหวังพึ่ง ENV ที่ตั้งจากระบบแทน)
    pass

# ========================= CONFIG =========================
# ดึงค่า config จาก Environment (เช่น .env หรือ Secrets ในระบบ CI/CD)
# ถ้าหาไม่เจอให้ใช้ "" แล้วค่อยเช็คอีกทีว่ามันว่างไหม
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

# ป้องกันไม่ให้โค้ด run ต่อ ถ้าไม่มี API KEY ที่จำเป็น
# - GEMINI_API_KEY: ใช้เรียกโมเดล Gemini (ถ้าไม่มี แปลว่าเรียก LLM ไม่ได้)
if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")

# - LINE_CHANNEL_ACCESS_TOKEN: ใช้ยิงข้อความไปที่ LINE Messaging API
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

# กำหนดชื่อโมเดล Gemini ที่จะใช้
# - อ่านค่าจาก ENV (GEMINI_MODEL_NAME)
# - ถ้าไม่มี ให้ใช้ "gemini-2.5-flash" เป็นค่าเริ่มต้น
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

# บอกไลบรารี genai ให้ใช้ API KEY ตัวนี้สำหรับทุกคำสั่งต่อไป
genai.configure(api_key=GEMINI_API_KEY)

# สร้าง object โมเดล Gemini ไว้ใช้งานตลอดโค้ด (เรียก generate_content ผ่านตัวแปร model)
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# จำกัดจำนวนครั้งเรียก Gemini ต่อวัน (กันกรณีโค้ด loop เพ้อ หรือโดนเรียกซ้ำบ่อยเกิน)
# ค่า default = 250 ถ้าไม่มีตั้งค่าใน ENV
GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))

# จำนวนครั้งสูงสุดที่อนุญาตให้ retry ถ้าเรียก Gemini แล้วเจอ error ชั่วคราว (เช่น 429, 500, timeout)
MAX_RETRIES = 6

# ช่วงเวลา delay แบบสุ่มระหว่างการเรียก Gemini แต่ละครั้ง
# - ใช้ random.uniform(SLEEP_MIN, SLEEP_MAX) เพื่อให้ pattern ไม่คงที่เกินไป
SLEEP_BETWEEN_CALLS = (6.0, 7.0)

# flag สำหรับโหมดทดสอบ:
# - ถ้า DRY_RUN = true → จะไม่ส่งข้อความจริงเข้า LINE
# - แต่จะแค่ print payload ที่ “จะส่ง” ออกหน้าจอแทน (ปลอดภัยเวลาเทส)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

# ตั้ง timezone เป็น Asia/Bangkok เพื่อให้เวลาที่ใช้ในระบบอิงตามเวลาไทย
bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)  # เวลา ณ ขณะนี้ใน timezone ไทย

# ใช้ requests.Session เพื่อ reuse connection
# - ข้อดี: ไม่ต้องเปิด/ปิด TCP connection ใหม่ทุกครั้ง → เร็วขึ้น และประหยัดทรัพยากร
# - เหมาะกับงานที่เรียกเว็บหลายครั้งในสคริปต์เดียว (เช่น ดึง RSS, ดึงหน้าเว็บข่าว หารูป)
S = requests.Session()
# ตั้ง header User-Agent เพื่อให้ server ปลายทางรู้ว่าเป็น client แบบ browser ทั่วไป
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15  # กำหนด timeout สูงสุดต่อ request (หน่วย: วินาที)

# โฟลเดอร์ไว้เก็บไฟล์ text บันทึกลิงก์ข่าวที่ “ส่งไปแล้ว” แยกตามวัน
# - ใช้กันการส่งข่าวซ้ำในวันเดียวกัน และเช็คย้อนหลังเมื่อวานได้ง่าย
SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)  # ถ้าโฟลเดอร์ยังไม่มี ให้สร้างขึ้นมา

# ========================= Helpers =========================
def _normalize_link(url: str) -> str:
    """
    ทำความสะอาด / ปรับรูปแบบ URL ให้เป็นมาตรฐาน เพื่อใช้เช็คว่าลิงก์ "ซ้ำกัน" หรือเปล่า

    เหตุผลที่ต้อง normalize:
      - ลิงก์เดียวกันอาจถูกเขียนต่างกันได้ เช่น
            https://example.com/article?id=123&utm_source=facebook
            https://example.com/article?id=123
        ทั้งสองลิงก์จริง ๆ แล้วชี้ไปที่บทความเดียวกัน แต่มีพารามิเตอร์ tracking เพิ่มมา

    สิ่งที่ฟังก์ชันนี้ทำ:
      1) ใช้ urlparse แยกส่วนต่าง ๆ ของ URL (scheme, netloc, path, query ฯลฯ)
      2) บังคับให้ hostname (netloc) เป็นตัวพิมพ์เล็ก เช่น WWW.Example.com → www.example.com
      3) หากไม่มี scheme ให้ถือเป็น https
      4) อ่าน query string แปลงเป็น list ของ (key, value)
      5) ลบพารามิเตอร์ที่เป็น tracking หรือขยะ เช่น:
         - key ที่ขึ้นต้นด้วย "utm_" (utm_source, utm_medium, …)
         - fbclid, gclid, ref, mc_cid, mc_eid ฯลฯ
      6) ประกอบ URL กลับด้วย urlunparse โดยใช้ query ใหม่ที่ถูกกรองแล้ว

    ถ้ามี error (เช่น URL แปลกมากจน parse ไม่ได้):
      - จะคืนค่าข้อความเดิมที่ strip ช่องว่างหัวท้ายแล้ว
    """
    try:
        p = urlparse(url)
        netloc = p.netloc.lower()                      # ทำ host ให้เป็นตัวเล็ก
        scheme = (p.scheme or "https").lower()        # ถ้าไม่มี scheme ให้ใช้ https

        # รายชื่อพารามิเตอร์ใน query ที่เรามองว่าเป็น tracking หรือไม่สำคัญ
        bad_keys = {"fbclid", "gclid", "ref", "ref_", "mc_cid", "mc_eid"}
        q = []
        # parse_qsl: แปลง "a=1&b=2" เป็น list ของคู่ (key, value)
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            # ถ้า key เริ่มด้วย utm_ หรืออยู่ใน bad_keys ให้ข้าม
            if k.startswith("utm_") or k in bad_keys:
                continue
            q.append((k, v))

        # ประกอบ URL กลับ โดยใช้ scheme/netloc ที่ normalize แล้ว และ query ที่ถูกกรองแล้ว
        return urlunparse(p._replace(scheme=scheme, netloc=netloc, query=urlencode(q)))
    except Exception:
        # ถ้า parse ไม่ผ่าน หรือเจอ error ใด ๆ:
        # - คืนค่าเดิม (ตัดช่องว่างหัวท้าย) เพื่อลดโอกาสพัง
        return (url or "").strip()


def get_sent_links_file(date=None):
    """
    คืน path (ชื่อไฟล์แบบเต็ม) ของไฟล์ที่ใช้เก็บ "ลิงก์ข่าวที่ส่งแล้ว" สำหรับวันหนึ่ง ๆ

    รูปแบบไฟล์:
      - โฟลเดอร์: SENT_LINKS_DIR (เช่น "sent_links")
      - ชื่อไฟล์: YYYY-MM-DD.txt   เช่น "2025-11-19.txt"

    ตัวอย่าง:
      - วันที่ 2025-11-19 → sent_links/2025-11-19.txt

    ถ้าไม่ส่ง date เข้ามา:
      - จะใช้วันที่ปัจจุบันตาม timezone ไทย
    """
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")


def load_sent_links_today_yesterday():
    """
    อ่านลิงก์ข่าวที่เคย "ส่งไปแล้ว" จากไฟล์ของ:
      - วันนี้ (day 0)
      - เมื่อวาน (day 1)

    เหตุผลที่ต้องอ่านวันนี้ + เมื่อวาน:
      - ป้องกันการส่งข่าวซ้ำในช่วงเปลี่ยนวัน
      - เช่น ข่าวตอนตี 1 ของวันนี้ อาจไปทับกับข่าวที่เคยส่งตอนดึกของเมื่อวาน

    ขั้นตอน:
      1) สำหรับ i ใน {0, 1}:
         - คำนวณวันที่ = now - i วัน
         - หาว่าไฟล์ของวันนั้นอยู่ที่ไหน (get_sent_links_file)
         - ถ้าไฟล์มีอยู่:
             - เปิดอ่านทีละบรรทัด
             - strip ช่องว่าง → normalize URL ด้วย _normalize_link
             - ถ้า URL ไม่ว่าง → ใส่ลงใน set sent_links
      2) คืนค่า sent_links ซึ่งเป็น set ของลิงก์ที่ส่งไปแล้ว (ทั้ง 2 วันรวมกัน)
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
    บันทึกลิงก์ข่าวที่ "เพิ่งส่งออกไป" ลงไฟล์ของวันที่กำหนด (หรือวันนี้ ถ้าไม่ระบุ)

    วิธีใช้:
      - หลังจากเลือกชุดข่าวที่จะส่งแล้ว
      - ส่งเข้า LINE เสร็จ
      - เรียก save_sent_links([...]) เพื่อเพิ่มลิงก์เหล่านั้นไว้ใน log ของวันนั้น

    ทำไมต้องบันทึก:
      - เพื่อที่รอบถัดไป (หรือวันถัดไป) จะได้รู้ว่าลิงก์ไหนเคยส่งไปแล้ว → กันส่งซ้ำ

    การทำงาน:
      1) หา path ไฟล์ของวันนั้นด้วย get_sent_links_file(date)
      2) เปิดไฟล์ในโหมด append ("a") ถ้าไม่มีไฟล์จะสร้างใหม่
      3) เขียนลิงก์แต่ละตัวลงไฟล์ ทีละบรรทัด
         - ก่อนเขียนให้ normalize URL อีกที เพื่อให้ format เหมือนกัน
    """
    path = get_sent_links_file(date)
    with open(path, "a", encoding="utf-8") as f:
        for url in new_links:
            f.write(_normalize_link(url) + "\n")


def _polish_impact_text(text: str) -> str:
    """
    ทำความสะอาดข้อความ impact_reason ที่ได้จาก Gemini ให้ดูเรียบร้อยขึ้น
    โดยเน้นแก้เคสที่ LLM ชอบเติมคำช่วย/คำวงเล็บที่เราไม่อยากโชว์ใน LINE

    สิ่งที่ทำ:
      1) ลบข้อความในวงเล็บที่มีคำประเภท:
         - "บวก", "ลบ", "ไม่ชัดเจน", "สั้น", "กลาง", "ยาว"
         เช่น "(ผลกระทบเบื้องต้น: บวกระยะสั้น)" → ลบทั้งวงเล็บออก
      2) ลดช่องว่างที่ซ้ำกันหลายช่องให้เหลือช่องเดียว
      3) แก้รูปแบบเครื่องหมายจุลภาค/จุด ให้สวยขึ้น:
         - ", ," → ", "
         - ", ." → "."

    return:
      - string ใหม่ที่ถูกเกลาความเรียบร้อยแล้ว
    """
    if not text:
        return text
    # ลบวงเล็บที่มีคำบวก/ลบ/ไม่ชัดเจน/สั้น/กลาง/ยาว อยู่ข้างใน
    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)
    # ลดช่องว่างซ้ำให้เหลือช่องเดียว
    text = re.sub(r"\s{2,}", " ", text)
    # แก้ , , ให้เป็น , (เว้นช่องเดียว)
    text = re.sub(r"\s*,\s*,", ", ", text)
    # แก้ ", ." ให้เหลือ "."
    text = re.sub(r"\s*,\s*\.", ".", text)
    return text.strip()

# ========================= FEEDS =========================
# รายชื่อ RSS feed ที่จะใช้เป็นแหล่งข่าวต้นทาง
# - แต่ละ key คือชื่อแหล่ง (ใช้ภายในโค้ด)
# - url: ที่อยู่ RSS feed
# - category: หมวดข่าว (Energy/Economy ฯลฯ)
# - site: ชื่อสั้น ๆ สำหรับโชว์ใน LINE
news_sources = {
    "Oilprice": {"url": "https://oilprice.com/rss/main", "category": "Energy", "site": "Oilprice"},
    "CleanTechnica": {"url": "https://cleantechnica.com/feed/", "category": "Energy", "site": "CleanTechnica"},
    "HydrogenFuelNews": {"url": "https://www.hydrogenfuelnews.com/feed/", "category": "Energy", "site": "Hydrogen Fuel News"},
    "Economist": {"url": "https://www.economist.com/latest/rss.xml", "category": "Economy", "site": "Economist"},
    "YahooFinance": {"url": "https://finance.yahoo.com/news/rssindex", "category": "Economy", "site": "Yahoo Finance"},
}

# URL รูปภาพ default ถ้าหารูปจากหน้าเว็บข่าวไม่ได้
DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ตัวนับจำนวนครั้งที่เรียก Gemini ดังนั้นจะรู้ว่าใช้ quota ไปเท่าไหร่ในวันนี้
GEMINI_CALLS = 0

# regex สำหรับหาโคลอน (:) เวอร์ชันแปลก ๆ เพื่อแทนที่ให้เป็น ":" ปกติ
COLON_RX = re.compile(r"[：﹕꞉︓⦂⸿˸]")

def _normalize_colons(text: str) -> str:
    """
    แทนที่เครื่องหมายโคลอนแบบตัวพิเศษ (เช่น : จาก unicode รูปแบบอื่น ๆ)
    ให้เป็น ":" ปกติตัวเดียว
    - ป้องกันเคสที่ LLM หรือเว็บใส่สัญลักษณ์แปลก ๆ แล้วทำให้ดูไม่สวย หรือ search ยาก
    """
    return COLON_RX.sub(":", text or "")


def fetch_article_image(url: str) -> str:
    """
    พยายามดึง URL ของรูปประกอบข่าวจากหน้าเว็บจริง (ไม่ใช่จาก RSS โดยตรง)

    ขั้นตอน:
      1) ใช้ requests.Session (S) โหลด HTML ของหน้าเว็บ (GET url)
      2) ถ้า status_code >= 400 ถือว่าผิดปกติ → คืน "" (ไม่มีรูป)
      3) ถอด HTML เป็น string แล้วใช้ regex หา:
         3.1) <meta property="og:image" content="..." >
              - ส่วนใหญ่เว็บจะใส่รูป preview ใน tag นี้
         3.2) ถ้าไม่เจอ → ลองหา <meta name="twitter:image" ...>
         3.3) ถ้าไม่เจอ → หา <img src="..."> ตัวแรกในหน้า
              - ถ้า src ขึ้นต้นด้วย "//" ให้เติม scheme เดิมเข้าไป เช่น "https:"
              - ถ้า src ขึ้นต้นด้วย "/" ให้ต่อ domain ของเว็บนำหน้า
              - ถ้าเป็น path หรือ URL เต็มอยู่แล้วก็ใช้ตรง ๆ
      4) ถ้าหาไม่ได้เลย → คืน "" (ให้ไปใช้ DEFAULT_ICON_URL ภายหลัง)

    หมายเหตุ:
      - ฟังก์ชันนี้ "แค่หา URL รูป" ไม่ได้โหลดไฟล์รูปจริง
      - ถ้าเกิด exception (timeout, parse error ฯลฯ) → คืน "" เพื่อไม่ให้โค้ดล้ม
    """
    try:
        r = S.get(url, timeout=TIMEOUT)
        if r.status_code >= 400:
            return ""
        html = r.text

        # กรณี 1: หารูปจาก meta og:image
        m = re.search(r'<meta[^>]+property=[\'\"]og:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            return m.group(1)

        # กรณี 2: ลองหา meta twitter:image
        m = re.search(r'<meta[^>]+name=[\'\"]twitter:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            return m.group(1)

        # กรณี 3: ถ้าไม่เจอ meta เลย ลองเอา <img> ตัวแรกในหน้า
        m = re.search(r'<img[^>]+src=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            src = m.group(1)
            # ถ้า src เริ่มด้วย "//" แปลว่า scheme-relative URL → เติม scheme ของหน้าเดิมเข้าไป
            if src.startswith("//"):
                parsed = urlparse(url)
                return f"{parsed.scheme}:{src}"
            # ถ้า src เริ่มด้วย "/" แปลว่า path บนโดเมนเดิม → ต่อด้วย scheme+netloc
            if src.startswith("/"):
                parsed = urlparse(url)
                return f"{parsed.scheme}://{parsed.netloc}{src}"
            # กรณีอื่นให้ใช้ src ตามที่ได้มา (อาจเป็น URL เต็ม หรือ path ที่เครื่อง client เข้าใจเอง)
            return src
        return ""
    except Exception:
        # ถ้าดึง HTML ไม่ได้หรือ regex พัง:
        # - คืน string ว่าง → ภายหลังจะ fallback ไปใช้ DEFAULT_ICON_URL อีกที
        return ""

# ========================= Upstream & Gas Context =========================
# PTT_CONTEXT = ข้อความบริบทสำหรับส่งเข้า LLM (Gemini)
#   - อธิบายว่า "ธุรกิจปิโตรเลียมขั้นต้นและก๊าซ" ของกลุ่ม ปตท. คืออะไร
#   - อธิบายเกณฑ์ 4 ข้อ ว่าข่าวแบบไหนถือว่า "เกี่ยวข้องอย่างมีนัยสำคัญ"
#   - เนื้อหานี้มีผลต่อการตัดสินใจของ LLM โดยตรง → ถ้าแก้ต้องระวัง scope ให้คงเดิม
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
    ฟังก์ชันห่อ (wrapper) สำหรับเรียกใช้ Gemini ให้ปลอดภัยขึ้น:
      - เช็ค quota ต่อวัน (GEMINI_DAILY_BUDGET) ก่อนเรียก
      - ถ้าเรียกแล้วเกิด error ชั่วคราว (เช่น 429, 500, timeout) จะลอง retry ให้เอง
      - มีการหน่วงเวลาเพิ่มขึ้นตามจำนวนรอบที่ retry เพื่อลดโหลด

    พารามิเตอร์:
      - prompt: ข้อความที่ส่งเข้า LLM
      - max_retries: จำนวนรอบสูงสุดที่จะลองใหม่ (default ใช้ค่า global)

    พฤติกรรม:
      1) ถ้า GEMINI_CALLS >= GEMINI_DAILY_BUDGET → raise RuntimeError ทันที
      2) Loop attempt จาก 1 ถึง max_retries:
         - พยายามเรียก model.generate_content(prompt)
         - ถ้าสำเร็จ:
             - เพิ่มค่า GEMINI_CALLS +1
             - return resp (object ของ Gemini)
         - ถ้า error:
             - แปลง error เป็น string
             - ถ้าภายในข้อความมีคำที่บอกว่าเป็น error ชั่วคราว (429, exhausted, 500, 503 ฯลฯ)
               และยังมีรอบให้ retry:
                 → sleep รอ (เวลาขึ้นกับ attempt เช่น 5s, 10s, … แต่ไม่เกิน 60)
                 → continue ไปลองใหม่
             - ถ้าเป็น error อื่น หรือหมดรอบ retry แล้ว:
                 → เก็บ error ไว้ใน last_error
                 → ถ้าถึงรอบสุดท้ายแล้ว ให้ raise last_error ออกไป
    """
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
            # เงื่อนไขมองหา error ชั่วคราวที่พอจะ retry ได้
            if attempt < max_retries and any(x in err_str for x in ["429","exhausted","temporarily","unavailable","deadline","500","503"]):
                time.sleep(min(60, 5 * attempt))
                continue
            last_error = e
            if attempt < max_retries:
                time.sleep(3 * attempt)
            else:
                # ถ้าเป็นรอบสุดท้ายแล้ว → โยน error ออกไปให้คนเรียกจัดการเอง
                raise last_error
    raise last_error  # เผื่อออกจาก loop แบบไม่ปกติ

# ===== Filter: ใช่/ไม่ใช่ =====
def llm_ptt_subsidiary_impact_filter(news):
    """
    ฟังก์ชันนี้ใช้ Gemini ช่วย "เฟ้นข่าว"
    เพื่อดูว่าข่าวหนึ่ง ๆ ควรถูกนับว่า "เกี่ยวข้องกับ Upstream/Gas ของกลุ่ม ปตท." ตามเกณฑ์ใน PTT_CONTEXT หรือไม่

    อินพุต:
      - news: dict ของข่าวหนึ่งรายการ
          คาดหวัง key สำคัญ:
            - 'title'   : หัวข้อข่าว
            - 'summary' : สรุปจาก RSS (อาจจะยาว/สั้น)
            - 'detail'  : ข้อความเพิ่ม (อาจว่าง หรือใช้ title แทน)

    การทำงาน:
      1) สร้าง prompt:
         - ใส่ PTT_CONTEXT (เกณฑ์และ value chain ของ PTT)
         - ระบุบทบาท LLM ว่าเป็น "News Screener"
         - ป้อน title, summary, detail ของข่าวเข้าไป
         - กำชับว่า "อนุญาตให้ตอบได้แค่ 2 แบบ: 'ใช่' หรือ 'ไม่ใช่'"

      2) เรียก call_gemini(prompt)
      3) อ่าน resp.text, strip เว้นบรรทัด, เอามาใช้เช็ค:
         - ถ้า string ที่ได้เริ่มต้นด้วย "ใช่" → return True
         - อย่างอื่น (รวมถึง error) → return False

    ใช้ที่ไหน:
      - ใน main() ตอนกรองข่าวช่วงแรก (ขั้นตอน "เรียง LLM กรองใช้/ไม่ใช้")
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
        resp = call_gemini(prompt)
        ans = (resp.text or "").strip().replace("\n", "")
        return ans.startswith("ใช่")
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        # ถ้าเรียก LLM ไม่สำเร็จ ให้ถือว่าข่าวนี้ "ไม่ผ่านฟิลเตอร์" ไปโดยอัตโนมัติ
        return False

# ===== Tag ข่าว: สรุป + บริษัท / ประเด็น / ภูมิภาค =====
def gemini_tag_news(news):
    """
    ใช้ Gemini ทำ "การวิเคราะห์ข่าวเชิงโครงสร้าง" สำหรับข่าวหนึ่งชิ้น

    สิ่งที่อยากได้จาก LLM (output):
      - summary         : ข้อความสรุปข่าวแบบ human-friendly
      - impact_companies: list บริษัทในเครือ PTT ที่ได้รับผลกระทบ (PTTEP, PTTLNG, PTTGL, PTTNGD)
      - topic_type      : ประเภทข่าว เช่น supply_disruption, price_move, policy ฯลฯ
      - region          : พื้นที่ที่เกี่ยวข้อง เช่น asia, us, middle_east, global
      - impact_reason   : เหตุผลว่าข่าวนี้อาจกระทบกลุ่ม ปตท. อย่างไร

    อินพุต:
      - news: dict ที่มีอย่างน้อย
          - 'title'
          - 'summary' (จาก RSS)
          - 'detail' (ถ้ามี)

    การทำงาน:
      1) สร้าง schema (dict) บอกโครงสร้าง JSON ที่ LLMต้องตอบกลับ
      2) สร้าง prompt:
         - แนบ PTT_CONTEXT (บริบทธุรกิจ)
         - ระบุบทบาท LLM ว่าเป็น Analyst
         - ป้อน title + summary + detail
         - แนบ schema แบบ JSON เพื่อให้ LLM รู้ว่าให้ตอบรูปแบบไหน
      3) เรียก call_gemini(prompt)
      4) รับ resp.text:
         - ถ้า LLM ใส่ ```json ... ``` ห่อมา → ตัดส่วนหัว/ท้ายออก
         - แปลงเป็น dict ด้วย json.loads
      5) ถ้า parse JSON ไม่ได้ → ส่ง dict fallback คืนไปแทน (ใช้ summary จาก RSS)

    หมายเหตุ:
      - ฟังก์ชันนี้ "ยังไม่" กรองบริษัทหรือ topic_type เพิ่มเติม
      - การคัดว่า impact_companies ต้องเป็น PTT ในเครือ จะทำใน main อีกที
    """
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
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

    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ของกลุ่ม ปตท. (เฉพาะธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติ)
หน้าที่: "สรุปข่าว และติดแท็ก" ตาม value chain และเกณฑ์ 4 ข้อด้านบน

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุป (จาก RSS): {news['summary']}
เนื้อหาเพิ่มเติม: {news.get('detail','')}

ให้ตอบกลับเป็น JSON ตาม schema นี้เท่านั้น:
{json.dumps(schema, ensure_ascii=False)}

คำอธิบาย field แบบย่อ:
- summary: สรุปว่าเกิดอะไร ที่ไหน เกี่ยวกับน้ำมัน/ก๊าซ/โครงสร้างพื้นฐานอย่างไร
- impact_companies: เลือก 0–2 บริษัทจาก ["PTTEP","PTTLNG","PTTGL","PTTNGD"]
- topic_type: ประเภทข่าว (price_move, policy ฯลฯ)
- region: พื้นที่ที่เกี่ยวข้อง (global, asia, us ฯลฯ)
- impact_reason: อธิบายสั้น ๆ ว่าข่าวนี้กระทบกลุ่ม ปตท. ผ่านช่องทางไหน

ห้ามตอบอย่างอื่น นอกจาก JSON ตาม schema
"""

    try:
        resp = call_gemini(prompt)
        raw = (resp.text or "").strip()

        # เผื่อกรณี LLM ใส่ ```json ... ``` ห่อมา → ตัดส่วนเกินออก
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()

        data = json.loads(raw)
        return data

    except Exception as e:
        print("[WARN] JSON parse fail in gemini_tag_news:", e)
        # ถ้า parse ไม่สำเร็จ → คืน dict fallback เพื่อให้ระบบยังเดินต่อได้
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
    ตรวจว่า LLM ระบุว่าข่าวนี้เกี่ยวข้องกับบริษัทในเครือ PTT หรือไม่
    วิธีตัดสิน:
      - ถ้า impact_companies มีชื่อบริษัทอย่างน้อย 1 ตัว → return True
      - ถ้าว่างเปล่า → return False

    ใช้ใน main หลังจาก LLM tag แล้ว:
      - เอาเฉพาะข่าวที่ impact_companies ไม่ว่างไปใช้ต่อ (เกี่ยวข้องกับ PTT โดยตรง)
    """
    return bool(impact_companies)


def fetch_news_9pm_to_6am():
    """
    ดึงข่าวจาก RSS ทุกแหล่ง (ใน news_sources) แต่จะ "เก็บเฉพาะข่าวที่ลงในช่วงเวลาเป้าหมาย" คือ:
      - 21:00 ของเมื่อวาน (ตามเวลาไทย)
      - ถึง 06:00 ของวันนี้ (ตามเวลาไทย)

    ขั้นตอน:
      1) คำนวณ start_time และ end_time ตาม timezone ไทย
      2) loop ผ่านทุกแหล่งข่าวใน news_sources:
         - ใช้ feedparser.parse(url) เพื่ออ่าน RSS
         - สำหรับ entry แต่ละข่าว:
             a) พยายามหาเวลาเผยแพร่:
                - ถ้ามี 'published_parsed' (struct_time) → แปลงเป็น datetime UTC → แปลงเป็น Asia/Bangkok
                - ถ้าไม่มี 'published_parsed' แต่มี 'published' หรือ 'updated' เป็น string:
                      → ใช้ dateutil_parser.parse แปลงเป็น datetime
                      → ถ้าไม่มี timezone → สมมติว่าเป็น UTC แล้วแปลงเป็น Asia/Bangkok
             b) ถ้า pub_dt ไม่อยู่ในช่วง (start_time <= pub_dt <= end_time) → ข้าม
             c) อ่านข้อมูลอื่น:
                  - summary: จาก entry.summary หรือ entry.description
                  - link   : entry.link
                  - title  : entry.title
                แล้ว append ลง all_news เป็น dict:
                  {
                    "site": ชื่อแหล่งข่าว (เช่น Oilprice),
                    "category": หมวด (Energy/Economy),
                    "title": หัวข้อ,
                    "summary": ข้อความสรุปจาก RSS,
                    "link": URL ข่าว,
                    "published": datetime (เวลาไทย),
                    "date": string เวลาไทยแบบ dd/mm/YYYY HH:MM
                  }
      3) หลังได้ all_news แล้ว:
         - ทำการลบรายการซ้ำ โดยดูจาก URL ที่ normalize ด้วย _normalize_link:
              - ถ้า key (URL normalize) ยังไม่เคยเห็น → เก็บเข้า uniq
              - ถ้าเคยแล้ว → ข้าม
      4) คืนค่า uniq (list ของข่าวที่อยู่ในช่วงเวลาเป้าหมาย และไม่ซ้ำกัน)

    """
    now_local = datetime.now(bangkok_tz)
    # เวลาเริ่ม: 21:00 ของ "เมื่อวาน"
    start_time = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    # เวลาจบ: 06:00 ของ "วันนี้"
    end_time = now_local.replace(hour=6, minute=0, second=0, microsecond=0)

    all_news = []
    for _, info in news_sources.items():
        try:
            feed = feedparser.parse(info["url"])
            for entry in feed.entries:
                # 1) พยายามดึงเวลาเผยแพร่จาก field ต่าง ๆ
                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if not pub_str and getattr(entry, "published_parsed", None):
                    # เคสที่ RSS ให้มาเป็น struct_time (เช่น time.struct_time)
                    t = entry.published_parsed
                    pub_dt = datetime(*t[:6], tzinfo=pytz.UTC).astimezone(bangkok_tz)
                else:
                    if not pub_str:
                        # ถ้าไม่มี published และไม่มี updated ให้ข้ามข่าวนี้ เพราะไม่รู้เวลา
                        continue
                    pub_dt = dateutil_parser.parse(pub_str)
                    if pub_dt.tzinfo is None:
                        # ถ้าเวลาไม่มี timezone → ถือว่าเป็น UTC แล้วค่อยแปลงมาเป็น Asia/Bangkok
                        pub_dt = pytz.UTC.localize(pub_dt)
                    pub_dt = pub_dt.astimezone(bangkok_tz)

                # 2) ข้ามข่าวที่อยู่นอกช่วงเวลาเป้าหมาย
                if not (start_time <= pub_dt <= end_time):
                    continue

                # 3) ดึงข้อมูลสำคัญออกจาก entry
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
            # ถ้าอ่าน RSS ของเว็บไหน fail → log ไว้เฉย ๆ แล้วไปเว็บถัดไป
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")

    # 4) ลบข่าวที่ URL ซ้ำกันออก โดยใช้ _normalize_link เป็นกุญแจ
    seen, uniq = set(), []
    for n in all_news:
        key = _normalize_link(n.get("link", ""))
        if key and key not in seen:
            seen.add(key)
            uniq.append(n)
    return uniq

# --------- Coverage-first selection ----------
# รายชื่อบริษัทในเครือที่ต้องการ "คุม coverage" เป็นพิเศษ
KEY_COMPANIES = ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]

# รายการ topic_type ที่ถือว่าสำคัญ อยากมีตัวแทนครบถ้าเป็นไปได้
KEY_TOPICS = ["supply_disruption", "price_move", "policy", "investment", "geopolitics"]

def select_news_coverage_first(news_list, max_items=10):
    """
    เลือกชุดข่าว/กลุ่มข่าวจากรายการทั้งหมด โดยใช้หลักคิด "coverage-first":
      - ไม่ใช่แค่เลือกข่าวใหม่สุด 10 ข่าว แต่พยายามคุมให้:
        1) ครอบคลุมบริษัทในเครือหลาย ๆ ตัว (จาก KEY_COMPANIES)
        2) ครอบคลุมประเภทข่าว (topic_type) หลายแบบ (จาก KEY_TOPICS)
        3) ถ้ายังไม่ครบ max_items → เติมด้วยข่าวใหม่ ๆ

    อินพุต:
      - news_list: list ของ news dict ที่อาจเป็น "ข่าวเดี่ยว" หรือ "กลุ่มข่าว"
      - max_items: จำนวนรายการสูงสุดที่อยากได้ (เช่น 10 bubble)

    การทำงาน:
      1) เรียงข่าวทั้งหมดตามเวลา published จากใหม่ → เก่า
      2) เตรียม list selected และ set used_ids (กันเลือกข่าวซ้ำ)
         - key ใน used_ids ใช้ URL ที่ normalize แล้ว (ถ้าไม่มีลิงก์ใช้ id(candidate) แทน)
      3) ฟังก์ชันย่อย _add_if_not_selected:
         - เช็คว่า key นี้เคยเลือกไปหรือยัง / selected เต็มหรือยัง
         - ถ้ายัง → เพิ่มเข้า selected และ mark ว่าใช้ key นี้แล้ว
      4) รอบที่ 1: loop KEY_COMPANIES
         - พยายามหาข่าวที่มี company นั้นอยู่ใน ptt_companies
         - ถ้าเจอ → _add_if_not_selected แล้ว break ออกจาก loopข่าว
      5) รอบที่ 2: loop KEY_TOPICS
         - ถ้าใน selected ยังไม่มี topic นั้นเลย:
             - พยายามหาข่าวใน sorted_news ที่ topic_type = topic
             - ถ้าเจอ → _add_if_not_selected
      6) รอบที่ 3:
         - loop ผ่านข่าวทั้งหมดตามลำดับเวลาอีกครั้ง
         - เติมข่าวใหม่ ๆ ลง selected จนกว่าจะถึง max_items

    return:
      - list ของข่าว/กลุ่มข่าวที่คัดแล้ว ตามเกณฑ์ coverage-first
    """
    if not news_list:
        return []

    # เรียงข่าว/กลุ่มข่าวจากใหม่ไปเก่า
    sorted_news = sorted(news_list, key=lambda n: n.get("published"), reverse=True)

    selected = []
    used_ids = set()

    def _add_if_not_selected(candidate):
        """
        เพิ่ม candidate เข้า selected ถ้า:
          - ยังไม่เคยเพิ่มมาก่อน (เช็คจาก URL normalize หรือ id object)
          - จำนวนใน selected ยังไม่ถึง max_items
        """
        key = _normalize_link(candidate.get("link", "")) or id(candidate)
        if key in used_ids:
            return False
        if len(selected) >= max_items:
            return False
        selected.append(candidate)
        used_ids.add(key)
        return True

    # รอบที่ 1: เน้นให้มีข่าวที่พูดถึงแต่ละบริษัทใน KEY_COMPANIES ให้มากที่สุดเท่าที่มี
    for comp in KEY_COMPANIES:
        if len(selected) >= max_items:
            break
        for n in sorted_news:
            companies = n.get("ptt_companies") or []
            if comp in companies:
                if _add_if_not_selected(n):
                    break

    # รอบที่ 2: เน้นให้มีข่าวในแต่ละ topic_type สำคัญ
    for topic in KEY_TOPICS:
        if len(selected) >= max_items:
            break
        # ถ้ามี topic นี้ใน selected อยู่แล้ว → ข้าม
        if any((x.get("topic_type") == topic) for x in selected):
            continue
        for n in sorted_news:
            if n.get("topic_type") == topic:
                if _add_if_not_selected(n):
                    break

    # รอบที่ 3: เอาข่าวใหม่ ๆ มาเติมช่องว่างจนเต็ม max_items
    for n in sorted_news:
        if len(selected) >= max_items:
            break
        _add_if_not_selected(n)

    return selected

# --------- Grouping ข่าวตาม topic + region ----------
def group_related_news(news_list, min_group_size=3):
    """
    รวมข่าวที่ "พูดเรื่องคล้ายกัน" (topic_type + region เหมือนกัน) ให้เป็น "กลุ่มข่าว" เดียวกัน
    เพื่อให้ผู้บริหารอ่านภาพรวมได้ง่ายขึ้น แทนจะเจอ 5 bubble พูดเรื่องเดียวกันกระจายออกไป

    อินพุต:
      - news_list: list ของข่าว (ที่ผ่านการ tag แล้ว มี field topic_type, region, ptt_companies)
      - min_group_size: จำนวนขั้นต่ำของข่าวในกลุ่มเดียวกัน ที่จะถือว่าควร "รวมเป็นกลุ่ม"
        เช่น:
          - ถ้ามีข่าว topic=geopolitics, region=middle_east อยู่ 4 ข่าว
            และ min_group_size=3 → จะเอามารวมเป็น group เดียว

    การทำงาน:
      1) สร้าง buckets = dict:
         key = (topic_type, region)
         value = list ข่าวที่เข้าข่ายคู่นี้
      2) สำหรับแต่ละ bucket:
         - ถ้าจำนวนข่าว >= min_group_size:
             → สร้าง object "กลุ่มข่าว" (is_group=True)
                 - รวม ptt_companies จากทุกข่าว แล้ว deduplicate
                 - เรียงข่าวในกลุ่มจากใหม่ → เก่า
                 - ใช้ข่าวใหม่สุด (anchor) เป็นแหล่งข้อมูล meta (title, category, date, link)
         - ถ้าจำนวนน้อยกว่า → ไม่รวมกลุ่ม:
             → เอาข่าวทั้ง bucket แยกกันเหมือนเดิม

    ผลลัพธ์:
      - list ของ item ที่มีทั้งสองแบบ:
         - ข่าวเดี่ยว (ไม่มี field is_group)
         - กลุ่มข่าว (dict ที่มี is_group=True และมี news_items = list ของข่าวย่อย)
    """
    buckets = {}
    for n in news_list:
        key = (n.get("topic_type", "other"), n.get("region", "other"))
        buckets.setdefault(key, []).append(n)

    grouped_items = []

    for (topic, region), items in buckets.items():
        if len(items) >= min_group_size:
            # กรณีนี้: รวมเป็น "กลุ่มข่าว"
            all_companies = []
            for it in items:
                all_companies.extend(it.get("ptt_companies") or [])
            # ลบชื่อบริษัทซ้ำ โดยใช้ trick dict.fromkeys เพื่อ preserve ลำดับ
            all_companies = list(dict.fromkeys(all_companies))

            # เรียงข่าวในกลุ่มจากใหม่ไปเก่า
            items_sorted = sorted(items, key=lambda x: x.get("published"), reverse=True)
            anchor = items_sorted[0]  # ใช้ข่าวใหม่สุดเป็น anchor สำหรับ meta

            group_obj = {
                "is_group": True,
                "topic_type": topic,
                "region": region,
                "ptt_companies": all_companies,
                "news_items": items_sorted,

                # meta หลักต่าง ๆ (title/category/date/link) ใช้ตามข่าว anchor
                "title": anchor.get("title", "-"),
                "site": "หลายแหล่งข่าว",
                "category": anchor.get("category", ""),
                "date": anchor.get("date", ""),
                "published": anchor.get("published"),
                "link": anchor.get("link", ""),
            }
            grouped_items.append(group_obj)
        else:
            # ถ้าจำนวนข่าวใน bucket น้อยกว่า min_group_size → ไม่รวมกลุ่ม
            grouped_items.extend(items)

    return grouped_items


def gemini_summarize_group(group):
    """
    ใช้ Gemini สรุป "ภาพรวมของกลุ่มข่าว" ที่ถูกสร้างจาก group_related_news

    use case:
      - ถ้าเราได้กลุ่มข่าว geopolitics ใน Middle East จำนวน 5 ข่าว
        เราอยากให้ LLM ช่วยสรุป:
          - สถานการณ์โดยรวมคืออะไร
          - ในมุมมองของ PTT Supply/Upstream/Gas มีความเสี่ยง/ผลกระทบยังไง

    อินพุต:
      - group: dict ที่มีอย่างน้อย:
          - "news_items": list ข่าวย่อยในกลุ่ม
          - "topic_type", "region", "ptt_companies" ฯลฯ

    การทำงาน:
      1) ถ้า news_items ว่าง → คืนข้อความ fallback
      2) รวมข่าวในกลุ่ม:
           - สร้างบรรทัดทีละข่าว:
             "1. <title> — <summary>"
           - รวมทั้งหมดเป็นก้อนข้อความ news_block
      3) สร้าง prompt:
           - แนบ PTT_CONTEXT
           - ระบุว่า LLM เป็น Analyst ต้องสรุป "ภาพรวม" ของชุดข่าว
           - ใส่ news_block ให้ LLM อ่าน
           - บอกให้ตอบในรูป JSON:
               {
                 "summary": "<สรุปภาพรวม 3–5 ประโยค>",
                 "impact_reason": "<อธิบายว่ากลุ่มข่าวนี้กระทบ PTT ยังไง>"
               }
      4) เรียก call_gemini(prompt)
      5) ถ้า LLM ใส่ ```json ... ``` มา → ตัดออก
      6) json.loads → data
      7) ถ้า parse ไม่ได้ → คืน fallback dict

    ใช้ใน main:
      - หลังจาก group_related_news เสร็จ
      - loop item ถ้า is_group=True → เรียก gemini_summarize_group แล้วเอาผลไปตั้ง gemini_summary/gemini_reason
    """
    items = group.get("news_items", [])
    if not items:
        return {
            "summary": "ไม่พบข่าวในกลุ่ม",
            "impact_reason": "-"
        }

    # รวมหัวข้อ+สรุปของข่าวย่อยแต่ละข่าวเป็น block เดียวให้ LLM อ่านง่าย
    lines = []
    for idx, n in enumerate(items, 1):
        line = f"{idx}. {n.get('title','-')} — {n.get('summary','')}"
        lines.append(line)
    news_block = "\n".join(lines)

    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ที่ต้องสรุป "ภาพรวม" ของชุดข่าวหลายข่าวในประเด็นเดียวกัน
เป้าหมาย: ผู้บริหารอ่านบับเบิลเดียวแล้วเข้าใจภาพรวมของกลุ่มข่าวนี้

กลุ่มข่าว (หัวข้อและสรุปย่อย):
{news_block}

ให้ตอบกลับเป็น JSON รูปแบบ:
{{
  "summary": "<สรุปภาพรวมของทั้งกลุ่ม 3–5 ประโยค>",
  "impact_reason": "<สรุปว่ากลุ่มข่าวนี้กระทบกลุ่ม ปตท. ผ่าน upstream/gas อย่างไร>"
}}

ห้ามตอบอย่างอื่น นอกจาก JSON ตามรูปแบบข้างต้น
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
        # ถ้า parse JSON ไม่สำเร็จ → ส่ง fallback ให้ main ใช้งานแทน
        return {
            "summary": "ไม่สามารถสรุปภาพรวมของกลุ่มข่าวได้",
            "impact_reason": "-"
        }

# --------- Labels & Human-friendly text ----------
# map topic_type → ป้ายภาษาไทยสั้น ๆ ไว้ใช้โชว์บน bubble ใน LINE
TOPIC_LABELS_TH = {
    "supply_disruption": "Supply ขัดข้อง/ลดลง",
    "price_move": "ราคาน้ำมัน/ก๊าซเปลี่ยน",
    "policy": "นโยบาย/กฎหมาย",
    "investment": "โครงการลงทุน/M&A",
    "geopolitics": "ภูมิรัฐศาสตร์/สงคราม",
    "other": "อื่น ๆ ที่เกี่ยวกับ Upstream/ก๊าซ",
}

# map region → ป้ายชื่อภูมิภาคสำหรับแสดงในข้อความ
REGION_LABELS_TH = {
    "global": "Global",
    "asia": "Asia",
    "europe": "Europe",
    "middle_east": "Middle East",
    "us": "US",
    "other": "อื่น ๆ",
}

# ข้อความอธิบายประเภทข่าวแบบสั้น ๆ สำหรับต่อท้าย "หมายเหตุ" ใน bubble
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
    แปลง list ของ "ข่าว/กลุ่มข่าว" ที่ผ่านการวิเคราะห์แล้ว
    ให้กลายเป็น payload Flex Message ตามรูปแบบของ LINE

    อินพุต:
      - news_items: list ของ item ที่มีข้อมูลอย่างน้อย:
          - title, site, category, date, link
          - gemini_summary, gemini_reason
          - topic_type, region, ptt_companies
          - ถ้าเป็นกลุ่มข่าว: is_group=True และมี news_items (sub list)

    สิ่งที่ฟังก์ชันนี้ทำ:
      1) เตรียมฟังก์ชันย่อย join_companies() แปลง list บริษัท → string แสดงผล
      2) สำหรับ item แต่ละตัว:
         - เลือก URL รูป (item["image"] ถ้ามี, ไม่งั้น DEFAULT_ICON_URL)
         - แปลง topic_type, region เป็น label ภาษาไทย
         - ดึง human_note จาก HUMAN_TOPIC_EXPLANATION ตาม topic_type
         - สร้าง impact_line แสดงบริษัทที่ได้รับผลกระทบ
         - สร้าง meta_line แสดงประเภทข่าวและภูมิภาค
         - ถ้าเป็นกลุ่มข่าว is_group=True:
             - เตรียมกล่อง group_sublist_box แสดงชื่อข่าวย่อย 1–5 ข่าวแรก
             - เปลี่ยน title ให้เป็น "หัวข้อรวม" เช่น "ภูมิรัฐศาสตร์ (Middle East) – 5 ข่าวสำคัญ"
         - สร้าง body_contents = list ของ block ต่าง ๆ ที่จะไปอยู่ใน bubble.body
         - สร้าง hero (รูป) และ footer (ปุ่ม "อ่านต่อ" + หมายเหตุว่ากำลังทดสอบ)
         - รวมเป็น bubble dict 1 ก้อน แล้ว append เข้า bubbles
      3) หลังจากได้ bubbles ทั้งหมด:
         - แบ่งเป็นหลาย carousel ถ้า bubble > 10 (LINE แนะนำไม่ให้เกิน ~10 ต่อ carousel)
         - สร้าง message object แบบ:
             {
               "type": "flex",
               "altText": "...",
               "contents": { "type": "carousel", "contents": [...] }
             }
         - append ลง carousels
      4) return carousels → ส่งต่อให้ broadcast_flex_message

    หมายเหตุ:
      - now_thai ใช้สำหรับ altText เพื่อให้ผู้รับรู้ว่าเป็นข่าวของวันที่เท่าไร
    """
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def join_companies(codes):
        """
        แปลง list ของรหัสบริษัท เช่น ["PTTEP","PTTLNG"]
        ให้เป็น string "PTTEP, PTTLNG"
        ถ้า list ว่าง → ใช้คำว่า "ไม่มีระบุ"
        """
        codes = codes or []
        return ", ".join(codes) if codes else "ไม่มีระบุ"

    bubbles = []
    for item in news_items:
        # ถ้า item ไม่มี image หรือ image ไม่ใช่ URL http/https → ใช้ DEFAULT_ICON_URL
        img = item.get("image") or DEFAULT_ICON_URL
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL

        topic_key = item.get("topic_type", "other")
        region_key = item.get("region", "other")
        topic_label = TOPIC_LABELS_TH.get(topic_key, "อื่น ๆ")
        region_label = REGION_LABELS_TH.get(region_key, "อื่น ๆ")
        human_note = HUMAN_TOPIC_EXPLANATION.get(topic_key, HUMAN_TOPIC_EXPLANATION["other"])

        # แสดงบรรทัด "กระทบ: PTTEP, PTTLNG"
        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm"
        }

        # แสดงบรรทัด "ประเภท: ... | ภูมิภาค: ..."
        meta_line = {
            "type": "text",
            "text": f"ประเภท: {topic_label} | ภูมิภาค: {region_label}",
            "size": "xs",
            "color": "#555555",
            "wrap": True,
            "margin": "sm"
        }

        # ถ้าเป็นกลุ่มข่าว เตรียม box แสดงหัวข้อข่าวย่อยในกลุ่มนี้
        group_sublist_box = None
        if item.get("is_group"):
            sub_items = item.get("news_items", [])[:5]  # จำกัดแสดงไม่เกิน 5 ข่าว
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

        # กำหนด title ที่จะโชว์ใน bubble:
        # - ถ้าเป็นข่าวเดี่ยว: ใช้ item["title"] ตรง ๆ
        # - ถ้าเป็นกลุ่มข่าว: สร้างชื่อรวม เช่น "Supply ขัดข้อง/ลดลง (Middle East) – 4 ข่าวสำคัญ"
        title_text = item.get("title", "-")
        if item.get("is_group"):
            count_sub = len(item.get("news_items", []))
            title_text = f"{topic_label} ({region_label}) – {count_sub} ข่าวสำคัญ"

        # ตัวเนื้อหาใน body ของ bubble (แสดงหัวข่าว, เวลา, site, summary, impact ฯลฯ)
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

        # ถ้าเป็นกลุ่มข่าว ให้เพิ่มรายการข่าวย่อยเข้าไปต่อท้าย body
        if group_sublist_box:
            body_contents.append(group_sublist_box)

        # ประกอบ bubble Flex Message 1 อัน
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

    # LINE Flex: 1 carousel แสดง bubble ได้หลายอัน (ปกติไม่ควรเกินประมาณ 10 อัน)
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
    ส่ง Flex Message (แบบ carousel) ออกไปยัง LINE Broadcast

    อินพุต:
      - access_token: LINE Channel Access Token
      - flex_carousels: list ของ message object จาก create_flex_message

    การทำงาน:
      - ถ้า DRY_RUN = True:
          - ไม่ยิง HTTP จริง
          - แค่ print payload ออกมาให้ดู (ตัดให้เหลือ ~500 ตัวอักษร ต่อ carousel)
      - ถ้า DRY_RUN = False:
          1) loop ทีละ carousel (เผื่อมีหลายชุด)
          2) POST ไปที่ /v2/bot/message/broadcast พร้อม payload
          3) ถ้า status_code >= 300 → แสดง error แล้วหยุดไม่ส่งชุดต่อไป
          4) ถ้าส่งผ่าน → sleep 1.2 วินาทีก่อนส่งชุดถัดไป (ป้องกัน rate-limit)
    """
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    for idx, carousel in enumerate(flex_carousels, 1):
        payload = {"messages": [carousel]}
        if DRY_RUN:
            print(f"[DRY_RUN] Carousel #{idx}: {json.dumps(payload)[:500]}...")
            continue
        try:
            resp = S.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            print(f"Broadcast #{idx} status:", resp.status_code, getattr(resp, "text", ""))
            if resp.status_code >= 300:
                # ถ้าส่งแล้วเกิด error (เช่น token ผิด, permission ไม่มี, server error)
                # ให้หยุด loop เพื่อกัน spam ซ้ำ ๆ
                break
            time.sleep(1.2)
        except Exception as e:
            print("[LINE ERROR]", e)
            break

# ========================= MAIN =========================
def main():
    """
    ฟังก์ชันหลัก (entry point) ของสคริปต์นี้
    ลำดับการทำงานแบบ Step-by-step:

    1) ดึงข่าวจาก RSS ช่วงเวลา 21:00 เมื่อวาน – 06:00 วันนี้ (ตามเวลาไทย)
       - ใช้ fetch_news_9pm_to_6am()
       - ลบข่าว URL ซ้ำแล้ว

    2) ใช้ Gemini กรองว่าข่าวไหน "เกี่ยวข้องกับ Upstream/Gas ของ PTT" ตาม PTT_CONTEXT
       - loop ผ่าน all_news:
           - ถ้า summary สั้นมาก (< 50 ตัวอักษร) → ใส่ title ลงไปใน field news['detail']
             (เพื่อให้ LLM มีข้อมูลเพิ่ม)
           - เรียก llm_ptt_subsidiary_impact_filter(news)
           - ถ้า LLM ตอบ "ใช่" → append เข้า filtered_news
           - sleep แบบสุ่มเล็กน้อยระหว่างแต่ละข่าว (ลดโอกาสโดน rate-limit)

    3) ใช้ Gemini วิเคราะห์เชิงโครงสร้างข่าวที่ผ่านฟิลเตอร์:
       - สำหรับแต่ละ news ใน filtered_news:
           - เรียก gemini_tag_news(news)
           - ตั้ง:
               - news['gemini_summary'] = summary จาก LLM (normalize colons)
               - news['ptt_companies']  = impact_companies ที่อยู่ใน {"PTTEP", "PTTLNG", "PTTGL", "PTTNGD"}
               - news['topic_type']     = topic_type จาก LLM หรือ "other"
               - news['region']         = region จาก LLM หรือ "other"
               - news['gemini_reason']  = impact_reason ที่เกลาแล้ว ( _polish_impact_text )
           - ถ้า is_ptt_related_from_output(news['ptt_companies']) เป็น True
               → แปลว่าข่าวนี้มีผลต่อบริษัทในเครืออย่างน้อยหนึ่งราย
               → เพิ่มเข้า tagged_news
           - sleep แบบสุ่มอีกครั้งหลังเรียก LLM

    4) ตรวจ quota Gemini ที่ใช้ไป
       - print ค่า GEMINI_CALLS / GEMINI_DAILY_BUDGET ไว้ debug

    5) รวมข่าวบางส่วนให้เป็น "กลุ่มข่าว" ตาม topic_type+region:
       - เรียก group_related_news(tagged_news, min_group_size=3)
       - ได้ collapsed_list ที่มีทั้งข่าวเดี่ยวและกลุ่มข่าว

    6) สำหรับ item ที่เป็นกลุ่มข่าว is_group=True:
       - เรียก gemini_summarize_group(item)
       - ตั้ง:
           - item["gemini_summary"] = summary ระดับกลุ่มจาก LLM
           - item["gemini_reason"]  = impact_reason ระดับกลุ่มจาก LLM (เกลาแล้ว)

    7) เลือกข่าว/กลุ่มข่าวที่ "ควรส่ง" แบบ coverage-first:
       - เรียก select_news_coverage_first(collapsed_list, max_items=10)
       - ได้ top_news ที่มีไม่เกิน 10 รายการ

    8) ป้องกัน "ส่งข่าวซ้ำ" วันนี้+เมื่อวาน:
       - โหลด set ของลิงก์ที่เคยส่งไปแล้วด้วย load_sent_links_today_yesterday()
       - กรอง top_news ให้เหลือเฉพาะข่าวที่ลิงก์ "ยังไม่เคยอยู่ใน set นี้"
       - ถ้าไม่มีข่าวใหม่เลย → print ว่าข่าววันนี้/เมื่อวานส่งครบแล้ว → return

    9) พยายามดึงรูปประกอบข่าวแต่ละข่าว:
       - สำหรับ item ใน top_news_to_send:
           - เรียก fetch_article_image(item["link"])
           - ถ้าได้ URL รูปไม่ใช่ http/https → ใช้ DEFAULT_ICON_URL
           - ตั้ง item["image"] = URL รูปที่ได้

    10) แปลงรายการข่าวเป็น Flex Message:
        - เรียก create_flex_message(top_news_to_send)
        - ได้ carousels = list ของ message object สำหรับ LINE

    11) ส่งข่าวเข้า LINE:
        - เรียก broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)

    12) บันทึกลิงก์ที่ส่งแล้วของวันนี้:
        - save_sent_links([... list ของ link จาก top_news_to_send ...])

    13) print("เสร็จสิ้น.") เพื่อให้รู้ว่า flow หลักจบแล้ว
    """
    # 1) ดึงข่าวช่วงเวลาเป้าหมายจาก RSS
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว")
        return

    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS

    # 2) Filter: ให้ Gemini เช็คว่าเกี่ยวข้องกับ Upstream/Gas หรือไม่
    filtered_news = []
    for news in all_news:
        # ถ้า summary จาก RSS สั้นมาก ให้ใช้ title เพิ่มเป็น detail
        # เพื่อให้ LLM มี context มากขึ้นตอนตัดสิน
        news['detail'] = news['title'] if len((news.get('summary') or '')) < 50 else ''
        if llm_ptt_subsidiary_impact_filter(news):
            filtered_news.append(news)
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

        # สรุปข่าวแบบแก้เครื่องหมาย : แปลก ๆ ให้เรียบร้อย
        news['gemini_summary'] = _normalize_colons(tag.get('summary', '')).strip() or 'ไม่พบสรุปข่าว'

        # เลือกเฉพาะบริษัทที่อยู่ในเครือ PTT จริง ๆ
        companies = [c for c in (tag.get('impact_companies') or []) if c in {"PTTEP", "PTTLNG", "PTTGL", "PTTNGD"}]
        news['ptt_companies'] = list(dict.fromkeys(companies))
        news['topic_type'] = tag.get('topic_type', 'other')
        news['region'] = tag.get('region', 'other')

        # เกลาข้อความ impact_reason ให้สวยอ่านง่าย
        news['gemini_reason'] = _polish_impact_text(tag.get('impact_reason', '').strip()) or '-'

        # เลือกเฉพาะข่าวที่ผูกกับบริษัทในเครืออย่างน้อยหนึ่งราย
        if is_ptt_related_from_output(news['ptt_companies']):
            tagged_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")
    if not tagged_news:
        print("ไม่พบข่าวที่ผูกกับบริษัทในเครือ PTT โดยตรง")
        return

    # 4) Grouping: รวมข่าวที่ topic+region เหมือนกันให้เป็นกลุ่ม (ถ้าจำนวน >= 3 ข่าว)
    collapsed_list = group_related_news(tagged_news, min_group_size=3)

    # 5) ทำ meta-summary สำหรับกลุ่มข่าวแต่ละกลุ่ม
    for item in collapsed_list:
        if item.get("is_group"):
            data = gemini_summarize_group(item)
            item["gemini_summary"] = _normalize_colons(data.get("summary", "")).strip()
            item["gemini_reason"] = _polish_impact_text(data.get("impact_reason", "").strip() or "-")

    # 6) เลือกข่าว/กลุ่มข่าวที่ควรส่งจริง ๆ แบบ coverage-first (ไม่เกิน 10 bubble)
    top_news = select_news_coverage_first(collapsed_list, max_items=10)

    # 7) กันการส่งข่าวซ้ำในช่วงวันนี้+เมื่อวาน
    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if _normalize_link(n.get('link', '')) not in sent_links]
    if not top_news_to_send:
        print("ข่าววันนี้/เมื่อวานส่งครบแล้ว")
        return

    # 8) พยายามดึงรูปประกอบของแต่ละข่าว
    for item in top_news_to_send:
        img = fetch_article_image(item.get("link", "")) or ""
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    # 9) แปลงข่าวเป็น Flex Message แล้ว broadcast ทาง LINE
    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)

    # 10) บันทึกลิงก์ที่ส่งไปแล้วของวันนี้ เพื่อกันส่งซ้ำในรอบหน้า
    save_sent_links([n.get("link", "") for n in top_news_to_send])
    print("เสร็จสิ้น.")

# รัน main() เมื่อไฟล์นี้ถูก run โดยตรง (ไม่ใช่ import จากที่อื่น)
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # ถ้ามี error ที่หลุดออกมานอก main ให้ log ไว้ใน console เพื่อ debug ได้ง่าย
        print("[ERROR]", e)
