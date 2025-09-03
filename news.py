"""
สคริปต์: ส่งสรุปข่าวพลังงาน/เศรษฐกิจไปยัง LINE (Flex Message) โดยใช้ฟีด RSS + สรุปด้วย Gemini

โฟลว์หลัก (Main Flow):
1) โหลดคีย์/ค่าคอนฟิกจาก Environment (.env): GEMINI_API_KEY, LINE_CHANNEL_ACCESS_TOKEN, GEMINI_MODEL_NAME, งบใช้งาน LLM ต่อวัน ฯลฯ
2) ดึงข่าวจากแหล่ง RSS ที่กำหนด (ช่วงเวลา 21:00 เมื่อวาน → 06:00 วันนี้ ตามเวลา Bangkok)
3) กรองข่าวด้วยโมเดล (llm_ptt_subsidiary_impact_filter) ว่า "เกี่ยวกับ upstream/กลุ่ม PTT" ไหม (ตอบ ใช่/ไม่ใช่)
4) วิเคราะห์ข่าวที่ผ่านฟิลเตอร์ด้วยโมเดล (gemini_summary_and_score) ให้ได้สรุป + คะแนนผลกระทบ + เหตุผล + บริษัทที่ได้รับผลกระทบ
5) จัดรูปแบบข้อมูลสำหรับ LINE Flex Message (ภาพ, หัวข้อ, เวลา, สรุป, เหตุผล, คะแนน)
6) ส่ง Broadcast ไปยัง LINE (แบ่งเป็น Carousel ละไม่เกิน 10 bubble) และบันทึกลิงก์ที่ส่งแล้วเพื่อกันส่งซ้ำในวันนี้/เมื่อวาน


- ใช้ requests.Session() เพื่อ HTTP ที่เบาและเร็วขึ้น
- ใช้ feedparser เพื่ออ่าน RSS อย่างยืดหยุ่นกับหลากหลายฟีด
- ใช้ pytz/datetime เพื่อจัดการ timezone Asia/Bangkok
- ใช้ระบบ backoff/retry เบื้องต้นเมื่อเรียก Gemini ล้มเหลวชั่วคราว
- เก็บสถานะ "ลิงก์ที่ส่งแล้ว" เป็นไฟล์ข้อความรายวันในโฟลเดอร์ sent_links

ความปลอดภัย:
- อย่าฝังคีย์ไว้ในโค้ด ให้ใช้ .env หรือ Secret Manager เท่านั้น
- ตรวจสอบ/ทำความสะอาดพารามิเตอร์ติดตาม (utm_ / fbclid ฯลฯ) เพื่อลดความซ้ำซ้อนของ URL
"""

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

# ===== โหลดไฟล์ .env ถ้ามี (ไม่จำเป็นต้องมีเสมอไป) =====
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # ถ้าไม่มี dotenv ก็ข้ามได้ ไม่ถือเป็นข้อผิดพลาด
    pass

# ========================= CONFIG =========================
# ดึงคีย์สำคัญจาก Environment/Secrets; ถ้าไม่พบให้หยุดโปรแกรมทันที
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

# ตั้งค่าชื่อโมเดล Gemini; ถ้าไม่ตั้งเองจะใช้ค่าเริ่มต้น "gemini-2.5-flash"
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"

# กำหนดค่าให้ SDK ของ Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

# โควต้า/รีทไร สำหรับควบคุมงบโมเดลและหลีกเลี่ยง rate limit
GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))  # จำนวนครั้งสูงสุดต่อวัน
MAX_RETRIES = 6  # จำนวนครั้งสูงสุดที่ยอมให้รีทไรเมื่อ error ชั่วคราว
SLEEP_BETWEEN_CALLS = (6.0, 7.0)  # เวลาพักระหว่างเรียกโมเดล (หน่วยวินาที)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"  # โหมดทดลอง (ไม่ยิง LINE จริง)

# ตั้ง timezone เป็นกรุงเทพฯ
bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)

# ===== สร้าง HTTP session ใช้ซ้ำ (เร็ว/เบา และตั้ง UA เบื้องต้น) =====
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15  # เวลา timeout สูงสุดของ HTTP (วินาที)

# ========== จัดการลิงก์ที่เคยส่งไปแล้ว (กันส่งซ้ำวันนี้/เมื่อวาน) ==========
SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)


def _normalize_link(url: str) -> str:
    """ทำความสะอาด/ปรับรูปแบบ URL ให้เป็นมาตรฐานเพื่อลดความซ้ำซ้อน
    - บังคับ scheme เป็น https ถ้าไม่ระบุ
    - แปลง netloc เป็นตัวพิมพ์เล็ก
    - ลบพารามิเตอร์ tracking ที่ไม่จำเป็น (utm_*, fbclid, gclid, ref, ฯลฯ)
    """
    try:
        p = urlparse(url)
        netloc = p.netloc.lower()
        scheme = (p.scheme or "https").lower()

        # กำจัดคีย์ติดตามยอดนิยม
        bad_keys = {"fbclid", "gclid", "ref", "ref_", "mc_cid", "mc_eid"}
        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            if k.startswith("utm_") or k in bad_keys:
                continue
            q.append((k, v))
        return urlunparse(p._replace(scheme=scheme, netloc=netloc, query=urlencode(q)))
    except Exception:
        # ถ้า parse ไม่สำเร็จ ให้คืนค่าเดิมที่ตัดช่องว่างแล้ว
        return (url or "").strip()


def get_sent_links_file(date=None):
    """คืน path ของไฟล์บันทึกลิงก์ที่ส่งแล้ว แยกตามวันที่ (YYYY-MM-DD.txt)"""
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")


def load_sent_links_today_yesterday():
    """อ่านไฟล์ลิงก์ที่ส่งไปแล้วสำหรับวันนี้และเมื่อวาน → คืนเป็น set สำหรับเช็คซ้ำเร็ว ๆ"""
    sent_links = set()
    for i in range(2):  # loop วันนี้และเมื่อวาน
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
    """บันทึกชุดลิงก์ที่เพิ่งส่งสำเร็จ ลงไฟล์ของวันนั้น ๆ (ต่อท้าย)"""
    path = get_sent_links_file(date)
    with open(path, "a", encoding="utf-8") as f:
        for url in new_links:
            f.write(_normalize_link(url) + "\n")


# ========================= FEEDS =========================
# รายการแหล่งข่าวที่สนใจ (สามารถเพิ่ม/ลดได้)
news_sources = {
    "Oilprice": {"url": "https://oilprice.com/rss/main", "category": "Energy", "site": "Oilprice"},
    "CleanTechnica": {"url": "https://cleantechnica.com/feed/", "category": "Energy", "site": "CleanTechnica"},
    "HydrogenFuelNews": {"url": "https://www.hydrogenfuelnews.com/feed/", "category": "Energy", "site": "Hydrogen Fuel News"},
    "Economist": {"url": "https://www.economist.com/latest/rss.xml", "category": "Economy", "site": "Economist"},
    "YahooFinance": {"url": "https://finance.yahoo.com/news/rssindex", "category": "Economy", "site": "Yahoo Finance"},
}

# รูปภาพเริ่มต้นเมื่อดึงรูปจากบทความไม่ได้
DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ตัวนับการเรียกใช้โมเดลต่อวัน (เพื่อควบคุมงบ)
GEMINI_CALLS = 0


# ========================= Helpers =========================
# เรกซ์สำหรับแทนที่สัญลักษณ์เครื่องหมายโคลอนที่เป็นฟอนต์พิเศษให้เหลือ ":"
COLON_RX = re.compile(r"[：﹕꞉︓⦂⸿˸]")


def _normalize_colons(text: str) -> str:
    """ปรับเครื่องหมายโคลอน (:) ให้เป็นมาตรฐาน ป้องกันปัญหาการแสดงผลใน LINE"""
    return COLON_RX.sub(":", text or "")


def _polish_impact_text(text: str) -> str:
    """ทำความสะอาดข้อความเหตุผลผลกระทบ: ตัดวงเล็บคำทั่วไป/ช่องว่างซ้ำ/คอมมาซ้ำ ๆ"""
    if not text:
        return text
    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*,\s*,", ", ", text)
    text = re.sub(r"\s*,\s*\.", ".", text)
    return text.strip()


def fetch_article_image(url: str) -> str:
    """พยายามดึงภาพปกจากหน้าเว็บบทความ: ลองหา meta og:image / twitter:image หรือ <img src=...>
    - ใช้ requests แบบเบา ๆ (ไม่เรนเดอร์ JS)
    - คืนค่าว่างถ้าหาไม่เจอหรือ error
    """
    try:
        r = S.get(url, timeout=TIMEOUT)
        if r.status_code >= 400:
            return ""
        html = r.text

        # 1) <meta property="og:image" content="...">
        m = re.search(r'<meta[^>]+property=[\'\"]og:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            return m.group(1)

        # 2) <meta name="twitter:image" content="...">
        m = re.search(r'<meta[^>]+name=[\'\"]twitter:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            return m.group(1)

        # 3) ถ้าไม่เจอ meta ให้ fallback หา <img src="..."> แรก ๆ
        m = re.search(r'<img[^>]+src=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            src = m.group(1)
            # แปลง URL แบบ // หรือ / ให้เป็น absolute ตามโดเมนของบทความ
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
# คอนเท็กซ์/เกณฑ์เพื่อให้โมเดลใช้ตัดสินว่า "เกี่ยวข้อง upstream/กลุ่ม PTT" หรือไม่
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
    """เรียกใช้โมเดล Gemini พร้อมควบคุมงบต่อวันและทำ backoff อัตโนมัติเมื่อเจอข้อผิดพลาดชั่วคราว
    - เพิ่มตัวนับ GEMINI_CALLS ทุกครั้งที่เรียกสำเร็จ
    - รีทไรเมื่อพบ error ประเภท 429/500/503 หรือข้อความบ่งชี้ความชั่วคราว
    - โยน error ทิ้งเมื่อเกินจำนวนรีทไร
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
            # ถ้าเป็นข้อผิดพลาดชั่วคราว ให้หน่วงเวลาแล้วลองใหม่
            if attempt < max_retries and any(x in err_str for x in [
                "429", "exhausted", "temporarily", "unavailable", "deadline", "500", "503"
            ]):
                time.sleep(min(60, 5 * attempt))
                continue
            last_error = e
            if attempt < max_retries:
                time.sleep(3 * attempt)
            else:
                # หมดสิทธิ์รีทไร โยน error ออกไปให้ผู้เรียกตัดสินใจต่อ
                raise last_error
    # สำรอง (โดยปกติจะไม่ถึงบรรทัดนี้)
    raise last_error


def llm_ptt_subsidiary_impact_filter(news):
    """ให้โมเดลตัดสินแบบ binary: ข่าวนี้ "เกี่ยวข้อง (ใช่)" กับ upstream/กลุ่ม PTT หรือ "ไม่ใช่"
    - ใช้ PTT_CONTEXT กำกับเกณฑ์
    - ป้อน title/summary/detail ให้โมเดล
    - คืนค่าเป็น True (ใช่) / False (ไม่ใช่)
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
    """เรียกโมเดลให้สรุปและให้คะแนนผลกระทบในรูปแบบ JSON ตาม schema ที่กำหนด
    โครงสร้างที่คาดหวัง:
      {
        "summary": str,                          # สรุปข่าวกระชับ ชี้เหตุการณ์+กลไก
        "score": int (1..5),                     # ระดับผลกระทบต่อ upstream
        "score_breakdown": [                     # รายการองค์ประกอบคะแนน
          {"points": int, "reason": str}, ...   # ผลรวม points ต้องเท่ากับ score
        ],
        "impact_companies": ["PTTEP","PTTLNG","PTTGL","PTTNGD"],  # ระบุไม่เกิน 2 ตัว
        "impact_reason": str                     # อธิบายกลไกเฉพาะ (เลี่ยงคำกว้าง ๆ)
      }
    ถ้า parse ไม่ได้ จะคืนค่า fallback ที่ปลอดภัย
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
                "items": {"type": "string", "enum": ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]}
            },
            "impact_reason": {"type": "string"}
        },
        "required": ["summary", "score", "score_breakdown", "impact_companies", "impact_reason"]
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
        # เผื่อโมเดลห่อด้วย ```json ... ``` ให้ลอกกรอบออกก่อน
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        # ถ้าแตก JSON ให้คืนค่ามาตรฐานที่ปลอดภัย
        print("[WARN] JSON parse fail:", e)
        return {
            "summary": "ไม่สามารถแปลง JSON ได้",
            "score": 3,
            "score_breakdown": [
                {"points": 2, "reason": "default"},
                {"points": 1, "reason": "fallback"}
            ],
            "impact_companies": [],
            "impact_reason": "-",
        }


# ========================= Logic =========================

def is_ptt_related_from_output(impact_companies) -> bool:
    """ตัวช่วยเช็คว่าโมเดลระบุรายชื่อบริษัทในเครือ PTT มาหรือไม่"""
    return bool(impact_companies)


def fetch_news_9pm_to_6am():
    """ดึงข่าวจากฟีดทั้งหมดในช่วงเวลา 21:00 (เมื่อวาน) → 06:00 (วันนี้) ตามเวลาไทย
    - คืนรายการข่าว (list of dict) โดยเติมข้อมูลพื้นฐาน เช่น site/category/title/summary/link/published/date
    - มีการ de-duplicate ด้วย URL ที่ normalize แล้ว
    """
    now_local = datetime.now(bangkok_tz)
    start_time = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end_time = now_local.replace(hour=6, minute=0, second=0, microsecond=0)
    print("ช่วง fetch:", start_time, "ถึง", end_time)

    all_news = []
    for _, info in news_sources.items():
        try:
            feed = feedparser.parse(info["url"])  # อ่านฟีด RSS
            for entry in feed.entries:
                # --- แปลงเวลาเผยแพร่ให้เป็น timezone Bangkok ---
                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if not pub_str and getattr(entry, "published_parsed", None):
                    # บางฟีดให้เป็น struct_time → ต้องสร้าง datetime เอง
                    t = entry.published_parsed
                    pub_dt = datetime(*t[:6], tzinfo=pytz.UTC).astimezone(bangkok_tz)
                else:
                    if not pub_str:
                        # ถ้าไม่มีเวลาเผยแพร่ชัดเจน ข้ามรายการนี้ไป
                        continue
                    pub_dt = dateutil_parser.parse(pub_str)
                    if pub_dt.tzinfo is None:
                        # ถ้าไม่มีข้อมูลโซนเวลา สมมติเป็น UTC แล้วค่อยแปลงมา Bangkok
                        pub_dt = pytz.UTC.localize(pub_dt)
                    pub_dt = pub_dt.astimezone(bangkok_tz)

                # --- กรองเฉพาะข่าวในช่วงเวลาที่กำหนด ---
                if not (start_time <= pub_dt <= end_time):
                    continue

                # --- ดึงข้อมูลหลักจาก entry; บางฟีดใช้ description แทน summary ---
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

    # --- ลบรายการที่ลิงก์ซ้ำ (เทียบจาก URL ที่ normalize แล้ว) ---
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
    """ให้คะแนนข่าวเบื้องต้นเพื่อจัดอันดับก่อนส่งเข้าโมเดลวิเคราะห์ละเอียด
    สูตรคะแนนอย่างง่าย:
      - recency (ความสดใหม่) ปรับสเกล 0..3
      - category weight: Energy=3, Economy=2, อื่น ๆ=1
      - length (สัดส่วนความยาวสรุป) cap ที่ 1
    """
    ranked = []
    for n in news_list:
        age_h = (now - n["published"]).total_seconds() / 3600.0
        recency = max(0.0, (72.0 - min(72.0, age_h))) / 72.0 * 3.0
        cat_w = {"Energy": 3.0, "Economy": 2.0, "Politics": 1.0}.get(n["category"], 1.0)
        length = min(len(n.get("summary", "")) / 500.0, 1.0)
        score = recency + cat_w + length
        ranked.append((score, n))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in ranked]


def create_flex_message(news_items):
    """ประกอบโครงสร้าง LINE Flex Message แบบ Carousel จากรายการข่าวที่ผ่านการวิเคราะห์แล้ว
    - รองรับสูงสุดครั้งละ 10 bubble ต่อ 1 carousel; ถ้าเกินจะแตกเป็นหลาย carousel
    - แต่ละ bubble มี hero image, หัวเรื่อง, เวลา, แหล่งที่มา, สรุป, เหตุผล, คะแนน, ปุ่มลิงก์
    """
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def join_companies(codes):
        codes = codes or []
        return ", ".join(codes) if codes else "ไม่มีระบุ"

    bubbles = []
    for item in news_items:
        # แปลง score_breakdown ให้เป็นสตริงหลายบรรทัด (เพื่อแสดงใน Flex)
        bd = item.get("score_breakdown", [])
        bd_lines = [f"- {x.get('points', 0)} คะแนน: {x.get('reason', '-')}\n" for x in bd]
        bd_clean = "".join(bd_lines).strip() or "-"

        # แสดงรายการบริษัทที่กระทบ (ถ้ามี)
        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm",
        }

        # ตั้งรูป hero: ถ้าไม่มี ให้ใช้ DEFAULT_ICON_URL
        img = item.get("image") or DEFAULT_ICON_URL
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL

        # เนื้อหาใน body ของ bubble
        body_contents = [
            {"type": "text", "text": item.get("title", "-"), "weight": "bold", "size": "lg", "wrap": True, "color": "#111111"},
            {
                "type": "box", "layout": "horizontal", "margin": "sm",
                "contents": [
                    {"type": "text", "text": f"🗓 {item.get('date','-')}", "size": "xs", "color": "#aaaaaa", "flex": 5},
                    {"type": "text", "text": f"📌 {item.get('category','')}", "size": "xs", "color": "#888888", "align": "end", "flex": 5}
                ]
            },
            {"type": "text", "text": f"🌍 {item.get('site','')}", "size": "xs", "color": "#448AFF", "margin": "sm"},
            impact_line,
            {"type": "text", "text": item.get("gemini_summary") or "ไม่พบสรุปข่าว", "size": "md", "wrap": True, "margin": "md", "color": "#1A237E", "weight": "bold"},
            {
                "type": "box", "layout": "vertical", "margin": "lg",
                "contents": [
                    {"type": "text", "text": "ผลกระทบ / เหตุผลคะแนน", "weight": "bold", "size": "lg", "color": "#D32F2F"},
                    {"type": "text", "text": (item.get("gemini_reason") or "-"), "size": "md", "wrap": True, "color": "#C62828", "weight": "bold"},
                    {"type": "text", "text": f"คะแนนรวม: {item.get('gemini_score','-')} คะแนน", "size": "lg", "wrap": True, "color": "#000000", "weight": "bold"},
                    {"type": "text", "text": bd_clean, "size": "sm", "wrap": True, "color": "#8E0000", "weight": "bold"}
                ]
            }
        ]

        bubble = {
            "type": "bubble", "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": body_contents},
            "footer": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "หมายเหตุ: การวิเคราะห์ทั้งหมดอยู่ในช่วงทดสอบ ขออภัยในความไม่สะดวก", "size": "xs", "color": "#FF0000", "wrap": True, "margin": "md", "weight": "regular"},
                    {"type": "button", "style": "primary", "color": "#1DB446", "action": {"type": "uri", "label": "อ่านต่อ", "uri": item.get("link", "#")}}
                ]
            }
        }
        bubbles.append(bubble)

    # --- แบ่งเป็นหลาย Carousel ถ้าข่าวเกิน 10 รายการ ---
    carousels = []
    for i in range(0, len(bubbles), 10):
        carousels.append({
            "type": "flex",
            "altText": f"ข่าวเกี่ยวข้องกับ ปตท. {now_thai}",
            "contents": {"type": "carousel", "contents": bubbles[i:i+10]}
        })
    return carousels


def broadcast_flex_message(access_token, flex_carousels):
    """ส่งข้อความแบบ Broadcast ไปยัง LINE ด้วย Flex Message
    - ถ้า DRY_RUN=True จะแสดง payload บางส่วนแทนการยิงจริง (เพื่อทดสอบ)
    - ยิงทีละ carousel และหน่วงเวลาสั้น ๆ ระหว่างชุด
    """
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
                # ถ้าเจอ error ให้หยุดเพื่อเลี่ยงการยิงซ้ำโดยไม่จำเป็น
                break
            time.sleep(1.2)
        except Exception as e:
            print("[LINE ERROR]", e)
            break


# ========================= MAIN =========================

def main():
    """ฟังก์ชันหลักที่ผูกทุกอย่างเข้าด้วยกัน"""
    # 1) ดึงข่าวตามช่วงเวลา
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว")
        return

    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS

    # 2) กรองข่าวด้วยโมเดล (ใช่/ไม่ใช่)
    filtered_news = []
    for news in all_news:
        # ถ้า summary สั้นมาก ให้ใช้ title เป็น detail เพิ่มบริบทให้โมเดล
        if len((news.get('summary') or '')) < 50:
            news['detail'] = news['title']
        else:
            news['detail'] = ''

        if llm_ptt_subsidiary_impact_filter(news):
            filtered_news.append(news)
        # หน่วงเพื่อเคารพ rate limit
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ข่าวที่เกี่ยวข้องกับบริษัทลูก PTT (ผ่านฟิลเตอร์): {len(filtered_news)} ข่าว")
    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้องบริษัทลูก PTT")
        return

    # 3) จัดอันดับเบื้องต้น แล้วเลือกตัวเต็งไม่เกิน 10 ข่าว
    ranked = rank_candidates(filtered_news, use_keyword_boost=False)
    top_candidates = ranked[: min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์เพียง {len(top_candidates)} ข่าว (จำกัด 10)")

    # 4) ให้โมเดลสรุป/ให้คะแนน/เหตุผล/บริษัทที่กระทบ ต่อข่าวแต่ละชิ้น
    ptt_related_news = []
    for news in top_candidates:
        data = gemini_summary_and_score(news)

        # --- จัดฟิลด์ให้อยู่ในรูปแบบที่ UI ใช้ได้สะดวก ---
        news['gemini_summary'] = _normalize_colons(data.get('summary', '')).strip() or 'ไม่พบสรุปข่าว'

        score = int(data.get('score', 3))
        news['gemini_score'] = max(1, min(5, score))  # บังคับให้อยู่ในช่วง 1..5

        bd = data.get('score_breakdown', [])
        # บังคับให้ผลรวม points = score (ถ้าโมเดลให้มาไม่เป๊ะ)
        total_points = sum(int(x.get('points', 0)) for x in bd)
        if bd and total_points != news['gemini_score']:
            diff = news['gemini_score'] - total_points
            bd[-1]['points'] = int(bd[-1].get('points', 0)) + diff
        news['score_breakdown'] = bd

        # เหตุผลผลกระทบ (ทำความสะอาดคำทั่วไป)
        reason = _polish_impact_text(data.get('impact_reason', '').strip())
        news['gemini_reason'] = reason or '-'

        # บริษัทในเครือ PTT ที่กระทบ (คงลำดับ, ตัดซ้ำ)
        companies = [c for c in data.get('impact_companies', []) if c in {"PTTEP","PTTLNG","PTTGL","PTTNGD"}]
        news['ptt_companies'] = list(dict.fromkeys(companies))

        # เก็บเฉพาะข่าวที่โมเดลระบุว่ามีการอ้างอิงถึงบริษัทในเครือจริง ๆ
        if is_ptt_related_from_output(companies):
            ptt_related_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not ptt_related_news:
        print("ไม่พบข่าวที่โมเดลระบุว่ากระทบต่อกลุ่ม PTT จากตัวเต็ง 10 ข่าว")
        return

    # 5) เรียงตามคะแนนมาก → เวลาใหม่ และตัดทอนเหลือ 10 ข่าวบนสุดสำหรับการส่ง
    ptt_related_news.sort(key=lambda n: (n.get('gemini_score', 0), n.get('published', datetime.min)), reverse=True)
    top_news = ptt_related_news[:10]

    # 6) กันส่งซ้ำด้วยการเช็คลิงก์ที่ส่งแล้วในวันนี้/เมื่อวาน
    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if _normalize_link(n.get('link','')) not in sent_links]
    if not top_news_to_send:
        print("ข่าววันนี้กับเมื่อวานส่งครบหมดแล้ว ไม่มีข่าวใหม่")
        return

    # 7) เติมภาพปกของบทความ (ถ้าหาไม่ได้จะ fallback เป็น DEFAULT_ICON_URL)
    for item in top_news_to_send:
        img = fetch_article_image(item.get("link", "")) or ""
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    # 8) ประกอบ Flex และส่งไปยัง LINE (หรือพิมพ์ payload ถ้า DRY_RUN)
    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)

    # 9) บันทึกลิงก์ที่ส่งแล้วเพื่อกันส่งซ้ำในรอบถัดไป
    save_sent_links([n.get("link", "") for n in top_news_to_send])
    print("เสร็จสิ้น.")


# จุดเริ่มโปรแกรม
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # จับ error ระดับบนสุดเพื่อไม่ให้ stack trace ที่ยาวเกินไปไหลออกหน้าจอ production
        print("[ERROR]", e)
