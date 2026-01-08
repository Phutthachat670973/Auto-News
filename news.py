# -*- coding: utf-8 -*-

import os
import re
import json
import time
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, unquote
from difflib import SequenceMatcher

import requests
import feedparser
import pytz
from dateutil import parser as dateutil_parser

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# =============================================================================
# ENV / CONFIG
# =============================================================================
TZ = pytz.timezone(os.getenv("TZ", "Asia/Bangkok"))

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN")

# Groq Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions").strip()
USE_LLM_SUMMARY = os.getenv("USE_LLM_SUMMARY", "1").strip().lower() in ["1", "true", "yes", "y"]

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "48"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "30"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))

# Debug mode - เปิดให้เห็นเหตุผลการกรอง
DEBUG_FILTERING = os.getenv("DEBUG_FILTERING", "1").strip().lower() in ["1", "true", "yes", "y"]

# สร้างตัวแปรสำหรับเลือกเว็บข่าวที่ต้องการ
ALLOWED_NEWS_SOURCES = os.getenv("ALLOWED_NEWS_SOURCES", "").strip()
if ALLOWED_NEWS_SOURCES:
    ALLOWED_NEWS_SOURCES_LIST = [s.strip().lower() for s in ALLOWED_NEWS_SOURCES.split(",") if s.strip()]
    print(f"[CONFIG] เลือกเฉพาะเว็บข่าว: {ALLOWED_NEWS_SOURCES_LIST}")
else:
    ALLOWED_NEWS_SOURCES_LIST = []
    print("[CONFIG] รับข่าวจากทุกเว็บข่าว")

# ตัวแปรควบคุมความเข้มงวด
STRICT_FILTERING = os.getenv("STRICT_FILTERING", "0").strip().lower() in ["1", "true", "yes", "y"]
ALLOW_MARKET_NEWS = os.getenv("ALLOW_MARKET_NEWS", "1").strip().lower() in ["1", "true", "yes", "y"]

# Sent links tracking
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

# =============================================================================
# PROJECT DATABASE
# =============================================================================
PROJECTS_BY_COUNTRY = {
    "Thailand": [
        "โครงการจี 1/61", "โครงการจี 2/61", "โครงการอาทิตย์", "Arthit",
        "โครงการเอส 1", "S1", "โครงการสัมปทาน 4", "Contract 4",
        "โครงการพีทีทีอีพี 1", "PTTEP 1", "โครงการบี 6/27",
        "โครงการแอล 22/43", "โครงการอี 5", "E5",
        "โครงการจี 4/43", "โครงการสินภูฮ่อม", "Sinphuhorm",
        "โครงการบี 8/32", "B8/32", "9A", "9เอ",
        "โครงการจี 4/48", "โครงการจี 12/48",
        "โครงการจี 1/65", "โครงการจี 3/65",
        "โครงการแอล 53/43", "โครงการแอล 54/43"
    ],
    "Myanmar": ["โครงการซอติก้า", "Zawtika", "โครงการยาดานา", "Yadana", "โครงการเมียนมา เอ็ม 3", "Myanmar M3"],
    "Malaysia": ["Malaysia SK309", "SK309", "Malaysia SK311", "SK311", "Malaysia Block H", "Block H"],
    "Vietnam": ["โครงการเวียดนาม 16-1", "Vietnam 16-1", "16-1", "Block B", "48/95"],
    "Indonesia": ["โครงการนาทูน่า ซี เอ", "Natuna Sea A"],
    "Kazakhstan": ["โครงการดุงกา", "Dunga"],
    "Oman": ["Oman Block 61", "Block 61", "Oman Block 6", "PDO"],
    "UAE": ["Abu Dhabi Offshore 1", "Abu Dhabi Offshore 2", "Abu Dhabi Offshore 3"],
}

# =============================================================================
# ENHANCED KEYWORD FILTER (แก้ไขปัญหาข่าวซ้ำและข่าวไม่เกี่ยวข้อง)
# =============================================================================
class EnhancedKeywordFilter:
    # คำหลักที่เกี่ยวข้องกับพลังงาน (ธุรกิจ)
    ENERGY_KEYWORDS = [
        'พลังงาน', 'ไฟฟ้า', 'ค่าไฟ', 'ค่าไฟฟ้า', 'อัตราค่าไฟฟ้า',
        'ก๊าซ', 'LNG', 'น้ำมัน', 'เชื้อเพลิง', 'พลังงานทดแทน',
        'โรงไฟฟ้า', 'โรงงานไฟฟ้า', 'พลังงานแสงอาทิตย์', 'โซลาร์', 'พลังงานลม',
        'พลังงานชีวมวล', 'พลังงานน้ำ', 'พลังงานความร้อน',
        'พลังงานนิวเคลียร์', 'ถ่านหิน', 'พลังงานฟอสซิล',
        'โครงการพลังงาน', 'นโยบายพลังงาน', 'แผนพลังงาน', 'ยุทธศาสตร์พลังงาน',
        'สัมปทาน', 'สัมปทานพลังงาน', 'สัมปทานก๊าซ', 'สัมปทานน้ำมัน',
        'แหล่งก๊าซ', 'แหล่งน้ำมัน', 'แหล่งพลังงาน',
        'ราคาพลังงาน', 'ราคาน้ำมัน', 'ราคาก๊าซ', 'ราคาไฟฟ้า',
        'ลงทุนพลังงาน', 'การลงทุนพลังงาน',
        'energy', 'electricity', 'power', 'gas', 'oil', 'fuel',
        'power plant', 'renewable', 'solar', 'wind', 'biomass',
        'energy policy', 'energy project', 'energy investment'
    ]
    
    # เพิ่มคำสำคัญสำหรับตลาดพลังงานและราคา
    ENERGY_MARKET_KEYWORDS = [
        'ราคา', 'ราคาน้ำมัน', 'ราคาก๊าซ', 'ราคาไฟฟ้า', 'ค่าไฟ',
        'ตลาด', 'ตลาดพลังงาน', 'ตลาดน้ำมัน', 'ตลาดก๊าซ',
        'โลก', 'โลกาว', 'ต่างประเทศ', 'สหรัฐ', 'เวเนซุเอลา',
        'ร่วง', 'ปรับขึ้น', 'ปรับลด', 'ผันผวน', 'ตก', 'เพิ่ม',
        'ดอลลาร์', 'บาร์เรล', 'ตลาดหุ้น', 'ตลาดโลก',
        'price', 'market', 'global', 'crude', 'brent', 'wti',
        'increase', 'decrease', 'drop', 'rise', 'fall'
    ]
    
    # คำที่บ่งบอกถึงธุรกิจ/โครงการ
    BUSINESS_KEYWORDS = [
        'โครงการ', 'ลงทุน', 'สัญญา', 'สัมปทาน', 'มูลค่า',
        'ล้าน', 'พันล้าน', 'ดอลลาร์', 'บาท', 'เหรียญ',
        'พบ', 'สำรวจ', 'ขุดเจาะ', 'ผลิต', 'ส่งออก', 'นำเข้า',
        'ประกาศ', 'แถลง', 'รายงาน', 'ผลประกอบการ', 'รายได้',
        'ขยาย', 'พัฒนา', 'สร้าง', 'ก่อสร้าง', 'ติดตั้ง',
        # เพิ่มคำสำคัญสำหรับตลาด
        'ตลาด', 'ซื้อ', 'ขาย', 'ซื้อขาย', 'ซื้อขายล่วงหน้า',
        'หุ้น', 'ตลาดหุ้น', 'ตลาดหลักทรัพย์', 'ตลาดโลก',
        'เพิ่ม', 'ลด', 'ปรับ', 'เปลี่ยนแปลง', 'วิกฤต', 'โอกาส',
        'project', 'investment', 'contract', 'agreement', 'deal',
        'discovery', 'exploration', 'drilling', 'production', 'export',
        'announce', 'report', 'financial', 'revenue', 'expand',
        'development', 'construction', 'installation',
        'market', 'trading', 'stock', 'exchange', 'global'
    ]
    
    # คำที่ต้องหลีกเลี่ยง (ข่าวสังคม)
    EXCLUDE_KEYWORDS = [
        'ตลาดรถยนต์', 'รถยนต์', 'รถ', 'รถใหม่', 'รถยนต์ใหม่',
        'ยานยนต์', 'อุตสาหกรรมยานยนต์',
        'ดารา', 'ศิลปิน', 'นักแสดง', 'นักร้อง', 'คนดัง',
        'ร่วมบุญ', 'การกุศล', 'จิตอาสา', 'มอบ', 'ให้', 'ช่วยเหลือ',
        'หู', 'สะเด็ด', 'จา พนม', 'ชวน', 'มอบพลังงาน', 'ให้คนไทย',
        'celebrity', 'actor', 'singer', 'donation', 'charity', 'philanthropy',
        'car', 'automotive', 'vehicle', 'automobile'
    ]
    
    # คำหลักสำหรับตรวจสอบข่าวซ้ำ
    MAIN_KEYWORDS_FOR_GROUPING = [
        'murphy', 'shell', 'แบร็ค อิลส์', 'black hills',
        'appraisal', 'oil field', 'LNG', 'terminal', 'supplier',
        'ไฟฟ้า', 'ลงทุน', 'investment', 'discovery', 'found'
    ]
    
    @classmethod
    def check_valid_energy_news(cls, text: str) -> tuple:
        """ตรวจสอบว่าเป็นข่าวพลังงานที่เกี่ยวข้องกับธุรกิจหรือไม่ และระบุเหตุผล"""
        text_lower = text.lower()
        reasons = []
        
        # 1. ตรวจสอบว่าเป็นข่าวสังคมหรือไม่
        for exclude in cls.EXCLUDE_KEYWORDS:
            if exclude.lower() in text_lower:
                reasons.append(f"มีคำต้องห้าม: '{exclude}'")
                return False, "ข่าวสังคม", reasons
        
        # 2. ตรวจสอบว่ามีคำที่เกี่ยวข้องกับพลังงาน
        found_energy_keywords = []
        for keyword in cls.ENERGY_KEYWORDS:
            if keyword.lower() in text_lower:
                found_energy_keywords.append(keyword)
        
        # ตรวจสอบคำสำคัญตลาดพลังงานด้วย
        found_market_keywords = []
        for keyword in cls.ENERGY_MARKET_KEYWORDS:
            if keyword.lower() in text_lower:
                found_market_keywords.append(keyword)
        
        # ถ้าไม่มีคำพลังงานเลย
        if not found_energy_keywords and not found_market_keywords:
            reasons.append("ไม่มีคำที่เกี่ยวข้องกับพลังงานหรือตลาดพลังงาน")
            return False, "ไม่เกี่ยวข้องกับพลังงาน", reasons
        
        reasons.append(f"พบคำพลังงาน: {', '.join(found_energy_keywords[:3])}")
        if found_market_keywords:
            reasons.append(f"พบคำตลาดพลังงาน: {', '.join(found_market_keywords[:3])}")
        
        # 3. ตรวจสอบว่ามีคำที่บ่งบอกถึงธุรกิจ/โครงการ/ตลาด
        found_business_keywords = []
        for keyword in cls.BUSINESS_KEYWORDS:
            if keyword.lower() in text_lower:
                found_business_keywords.append(keyword)
        
        # ✅ **แก้ไข: ถ้าเป็นข่าวราคาพลังงานหรือตลาดพลังงาน ให้ผ่อนคลายเงื่อนไข**
        is_market_news = any(word in text_lower for word in ['ราคา', 'ตลาด', 'price', 'market'])
        has_energy_keywords = bool(found_energy_keywords)
        
        # เงื่อนไขใหม่:
        # 1. ถ้าเป็นข่าวราคาพลังงาน/ตลาดพลังงาน และมีคำพลังงาน → อนุญาต
        # 2. ถ้ามีคำธุรกิจ → อนุญาต
        # 3. ถ้าเป็นข่าวทั่วไปเกี่ยวกับพลังงาน → ต้องมีคำธุรกิจด้วย
        
        if is_market_news and has_energy_keywords:
            # ข่าวราคาพลังงาน/ตลาดพลังงานที่มีคำพลังงานสำคัญ
            reasons.append("เป็นข่าวราคา/ตลาดพลังงานที่สำคัญ")
            return True, "ผ่าน", reasons
        elif found_business_keywords:
            # มีคำธุรกิจ/โครงการ
            reasons.append(f"พบคำธุรกิจ: {', '.join(found_business_keywords[:3])}")
            return True, "ผ่าน", reasons
        elif has_energy_keywords and any(word in text_lower for word in ['สำคัญ', 'ใหญ่', 'หลัก', 'โลก', 'global']):
            # ข่าวพลังงานสำคัญระดับโลก
            reasons.append("เป็นข่าวพลังงานสำคัญระดับโลก")
            return True, "ผ่าน", reasons
        else:
            reasons.append("ไม่มีคำบ่งบอกธุรกิจ/โครงการ/ตลาดที่สำคัญ")
            return False, "ไม่ใช่ข่าวธุรกิจ/ตลาดพลังงาน", reasons
    
    @classmethod
    def detect_country(cls, text: str) -> str:
        """Detect country from text"""
        text_lower = text.lower()
        
        country_patterns = {
            "Thailand": ['ไทย', 'ประเทศไทย', 'thailand', 'bangkok'],
            "Myanmar": ['เมียนมา', 'myanmar', 'ย่างกุ้ง', 'yangon'],
            "Malaysia": ['มาเลเซีย', 'malaysia', 'กัวลาลัมเปอร์', 'kuala lumpur'],
            "Vietnam": ['เวียดนาม', 'vietnam', 'ฮานอย', 'hanoi'],
            "Indonesia": ['อินโดนีเซีย', 'indonesia', 'จาการ์ตา', 'jakarta'],
            "Kazakhstan": ['คาซัคสถาน', 'kazakhstan', 'astana'],
            "Oman": ['โอมาน', 'oman', 'muscat'],
            "UAE": ['ยูเออี', 'uae', 'ดูไบ', 'dubai', 'อาบูดาบี', 'abu dhabi']
        }
        
        for country, patterns in country_patterns.items():
            if any(pattern in text_lower for pattern in patterns):
                return country
        
        return ""

# =============================================================================
# FEEDS - เพิ่มเว็บตรง
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

FEEDS = [
    ("GoogleNewsTH", "thai", gnews_rss(
        '(พลังงาน OR "ค่าไฟ" OR ก๊าซ OR LNG OR น้ำมัน OR ไฟฟ้า OR "โรงไฟฟ้า" OR "พลังงานทดแทน" OR "สัมปทาน") -"รถยนต์" -"ตลาดรถ" -"ดารา" -"นักแสดง"',
        hl="th", gl="TH", ceid="TH:th"
    )),
    ("GoogleNewsEN", "international", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas OR "power plant" OR "energy project") AND (Thailand OR Vietnam OR Malaysia OR Indonesia) -car -automotive -celebrity',
        hl="en", gl="US", ceid="US:en"
    )),
    ("EnergyNewsCenter", "direct", "https://www.energynewscenter.com/feed/"),
]

# =============================================================================
# UTILITIES
# =============================================================================
def now_tz() -> datetime:
    return datetime.now(TZ)

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    try:
        u = urlparse(url)
        return u._replace(fragment="").geturl()
    except Exception:
        return url

def extract_domain(url: str) -> str:
    """Extract domain name from URL"""
    url = normalize_url(url)
    if not url:
        return ""
    try:
        domain = urlparse(url).netloc.lower()
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""

def is_allowed_source(url: str) -> bool:
    """ตรวจสอบว่า URL นี้มาจากเว็บข่าวที่เราอนุญาตหรือไม่"""
    if not ALLOWED_NEWS_SOURCES_LIST:  # ถ้าไม่ได้กำหนด allowed sources = ยอมรับทั้งหมด
        return True
    
    domain = extract_domain(url)
    if not domain:
        return False
    
    # ตรวจสอบว่า domain อยู่ในรายการที่อนุญาต
    for allowed_source in ALLOWED_NEWS_SOURCES_LIST:
        if allowed_source in domain:  # ใช้ partial match เช่น "reuters" จะ match "reuters.com"
            return True
    
    return False

def shorten_google_news_url(url: str) -> str:
    """Extract actual URL from Google News redirect"""
    url = normalize_url(url)
    if not url:
        return url
    try:
        u = urlparse(url)
        if "news.google.com" in u.netloc:
            qs = parse_qs(u.query)
            if "url" in qs and qs["url"]:
                return normalize_url(unquote(qs["url"][0]))
    except Exception:
        pass
    return url

def read_sent_links() -> set:
    sent = set()
    for fn in os.listdir(SENT_DIR):
        if not fn.endswith(".txt"):
            continue
        fp = os.path.join(SENT_DIR, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sent.add(line)
        except Exception:
            continue
    return sent

def append_sent_link(url: str):
    url = normalize_url(url)
    if not url:
        return
    fn = os.path.join(SENT_DIR, now_tz().strftime("%Y-%m-%d") + ".txt")
    with open(fn, "a", encoding="utf-8") as f:
        f.write(url + "\n")

def in_time_window(published_dt: datetime, hours: int) -> bool:
    if not published_dt:
        return False
    return published_dt >= (now_tz() - timedelta(hours=hours))

def cut(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"

def create_simple_summary(text: str, max_length: int = 150) -> str:
    """Create a simple summary from text if LLM is not available"""
    text = (text or "").strip()
    if not text:
        return ""
    
    # Remove extra whitespace and newlines
    text = ' '.join(text.split())
    
    # Find first sentence or truncate
    sentences = re.split(r'[.!?]', text)
    if sentences and len(sentences[0]) > 10:
        summary = sentences[0].strip()
        if len(summary) > max_length:
            summary = summary[:max_length-1] + "…"
        return summary + "."
    
    # Fallback: simple truncation
    if len(text) > max_length:
        return text[:max_length-1] + "…"
    return text

# =============================================================================
# RSS PARSING
# =============================================================================
def fetch_feed_with_retry(name: str, url: str, retries: int = 3):
    """ดึง feed พร้อมระบบ retry"""
    for attempt in range(retries):
        try:
            print(f"[FEED] ดึงข้อมูลจาก {name} (ครั้งที่ {attempt+1}/{retries})...")
            d = feedparser.parse(url)
            entries = d.entries or []
            print(f"[FEED] {name}: พบ {len(entries)} entries")
            return entries
        except Exception as e:
            print(f"[FEED] {name}: เกิดข้อผิดพลาด - {str(e)}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                return []

def parse_entry(e, feed_name: str, section: str):
    title = (getattr(e, "title", "") or "").strip()
    link = (getattr(e, "link", "") or "").strip()
    summary = (getattr(e, "summary", "") or "").strip()
    published = getattr(e, "published", None) or getattr(e, "updated", None)

    # สำหรับเว็บโดยตรงอาจใช้ published_parsed
    if not published and hasattr(e, 'published_parsed'):
        try:
            import time as time_module
            published = time_module.strftime('%Y-%m-%dT%H:%M:%SZ', e.published_parsed)
        except:
            pass

    try:
        published_dt = dateutil_parser.parse(published) if published else None
        if published_dt and published_dt.tzinfo is None:
            published_dt = TZ.localize(published_dt)
        if published_dt:
            published_dt = published_dt.astimezone(TZ)
    except Exception:
        published_dt = None

    canon = shorten_google_news_url(link)

    return {
        "title": title,
        "url": normalize_url(link),
        "canon_url": normalize_url(canon),
        "summary": summary,
        "published_dt": published_dt,
        "feed": feed_name,
        "section": section,
    }

# =============================================================================
# LLM ANALYZER (เรียบง่ายขึ้น)
# =============================================================================
class LLMAnalyzer:
    def __init__(self, api_key: str, model: str, endpoint: str):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
    
    def analyze_news(self, title: str, summary: str) -> dict:
        """Analyze news using LLM"""
        if not self.api_key:
            return self._get_default_analysis(title, summary)
        
        system_prompt = """คุณเป็นผู้ช่วยสรุปข่าวพลังงาน
        ตอบกลับเป็น JSON เท่านั้นตามรูปแบบนี้:
        {
            "relevant": true/false,
            "country": "ชื่อประเทศหรือค่าว่าง",
            "summary_th": "สรุปภาษาไทยสั้นๆ 1 ประโยค",
            "topics": ["หัวข้อ1", "หัวข้อ2"]
        }
        
        โปรดสรุปข่าวพลังงานให้กระชับ:"""
        
        user_prompt = f"""ข่าว: {title}
        
        เนื้อหา: {summary[:500]}
        
        โปรดสรุปข่าวนี้เป็นภาษาไทยสั้นๆ 1 ประโยค:"""
        
        try:
            response = requests.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 300
                },
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"[LLM] HTTP Error {response.status_code}")
                return self._get_default_analysis(title, summary)
            
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
                
                # Validate and clean up
                return {
                    "relevant": bool(analysis.get("relevant", True)),
                    "country": str(analysis.get("country", "")).strip(),
                    "summary_th": str(analysis.get("summary_th", "")).strip()[:150],
                    "topics": [str(t).strip() for t in analysis.get("topics", []) if t]
                }
                
        except json.JSONDecodeError:
            print("[LLM] Failed to parse JSON response")
        except Exception as e:
            print(f"[LLM] Error: {str(e)}")
        
        return self._get_default_analysis(title, summary)
    
    def _get_default_analysis(self, title: str, summary: str):
        """สร้างการวิเคราะห์พื้นฐานเมื่อ LLM ไม่ทำงาน"""
        combined = f"{title} {summary}"
        simple_summary = create_simple_summary(combined, 100)
        
        return {
            "relevant": True,
            "country": "",
            "summary_th": simple_summary if simple_summary else "สรุปข้อมูลไม่พร้อมใช้งาน",
            "topics": []
        }

# =============================================================================
# ENHANCED NEWS PROCESSOR (แก้ไขปัญหาข่าวซ้ำ)
# =============================================================================
class EnhancedNewsProcessor:
    def __init__(self):
        self.sent_links = read_sent_links()
        self.llm_analyzer = LLMAnalyzer(GROQ_API_KEY, GROQ_MODEL, GROQ_ENDPOINT) if GROQ_API_KEY else None
        
        # สร้าง dictionary สำหรับเก็บชื่อเว็บข่าว
        self.news_sources = {
            'reuters.com': 'Reuters',
            'bloomberg.com': 'Bloomberg',
            'bangkokpost.com': 'Bangkok Post',
            'thansettakij.com': 'ฐานเศรษฐกิจ',
            'posttoday.com': 'Post Today',
            'prachachat.net': 'ประชาชาติธุรกิจ',
            'mgronline.com': 'ผู้จัดการออนไลน์',
            'komchadluek.net': 'คมชัดลึก',
            'nationthailand.com': 'The Nation Thailand',
            'naewna.com': 'แนวหน้า',
            'dailynews.co.th': 'เดลินิวส์',
            'thairath.co.th': 'ไทยรัฐ',
            'khaosod.co.th': 'ข่าวสด',
            'matichon.co.th': 'มติชน',
            'sanook.com': 'สนุกดอทคอม',
            'kapook.com': 'กะปุก',
            'manager.co.th': 'ผู้จัดการ',
            'energynewscenter.com': 'Energy News Center',
        }
        
        # Cache สำหรับป้องกันข่าวซ้ำใน session เดียวกัน
        self._title_cache = []
        self._processed_items = []
        self._group_cache = set()
        
        # สำหรับเก็บสถิติ
        self.filter_stats = {
            'total_processed': 0,
            'filtered_by': {
                'no_title': 0,
                'no_url': 0,
                'already_sent': 0,
                'out_of_window': 0,
                'not_allowed_source': 0,
                'invalid_energy_news': 0,
                'no_country': 0,
                'duplicate_in_session': 0,
                'similar_news_exists': 0,
                'passed': 0
            }
        }
        
        # สำหรับเก็บข่าวที่ไม่ผ่านการกรอง
        self.filtered_news = []
    
    def get_source_name(self, url: str) -> str:
        """ดึงชื่อเว็บข่าวจาก URL"""
        domain = extract_domain(url)
        if not domain:
            return domain
        
        # ตรวจสอบว่า domain ตรงกับแหล่งข่าวที่เรารู้จักหรือไม่
        for source_domain, source_name in self.news_sources.items():
            if source_domain in domain:
                return source_name
        
        # หากไม่เจอ ให้ใช้ domain เป็นชื่อ
        return domain
    
    def _is_important_energy_news(self, item: dict) -> bool:
        """ตรวจสอบว่าเป็นข่าวพลังงานสำคัญที่ควรอนุญาตโดยเฉพาะ"""
        title = item.get('title', '').lower()
        summary = item.get('summary', '').lower()
        text = f"{title} {summary}"
        
        # รายการคำสำคัญสำหรับข่าวพลังงานสำคัญที่ควรอนุญาต
        important_patterns = [
            # ราคาพลังงานโลก
            (['น้ำมันโลกร่วง', 'น้ำมันโลก', 'ราคาน้ำมันโลก', 'crude oil', 'brent'], 2),
            (['ก๊าซธรรมชาติโลก', 'ราคาก๊าซโลก', 'lng price', 'gas price'], 2),
            (['ค่าไฟปรับ', 'ค่าไฟฟ้า', 'ไฟฟ้าแพง', 'อัตราค่าไฟฟ้า'], 2),
            
            # ตลาดพลังงานโลก
            (['ตลาดน้ำมันโลก', 'ตลาดพลังงานโลก', 'oil market', 'energy market'], 2),
            (['สหรัฐ', 'อเมริกา', 'usa', 'u.s.', 'จีน', 'china'], 1),
            
            # เหตุการณ์สำคัญ
            (['วิกฤต', 'crisis', 'embargo', 'ห้ามส่งออก', 'sanction'], 2),
            (['โอเปก', 'opec', 'องค์การประเทศผู้ส่งออกน้ำมัน'], 2),
            (['พลังงานโลก', 'global energy', 'world energy'], 2),
        ]
        
        score = 0
        for keywords, points in important_patterns:
            if any(keyword in text for keyword in keywords):
                score += points
        
        # ถ้าได้คะแนนสูงพอ ให้ถือว่าสำคัญ
        return score >= 3
    
    def _is_similar_title(self, title1: str, title2: str, threshold: float = 0.8) -> bool:
        """ตรวจสอบความคล้ายคลึงของหัวข้อข่าว"""
        similarity = SequenceMatcher(None, title1, title2).ratio()
        return similarity > threshold
    
    def _create_group_key(self, item: dict) -> str:
        """สร้าง key สำหรับจัดกลุ่มข่าว (ป้องกันข่าวซ้ำ)"""
        title_lower = item.get('title', '').lower()
        country = item.get('country', '')
        
        # หาคีย์เวิร์ดหลักในข่าว
        for keyword in EnhancedKeywordFilter.MAIN_KEYWORDS_FOR_GROUPING:
            if keyword.lower() in title_lower:
                return f"{country}_{keyword}"
        
        # ถ้าไม่เจอคีย์เวิร์ดเฉพาะ ให้ใช้ 3 คำแรกของหัวข้อ
        words = title_lower.split()[:3]
        return f"{country}_{'_'.join(words)}"
    
    def _score_news_item(self, item: dict) -> int:
        """ให้คะแนนข่าวตามคุณภาพ"""
        score = 0
        
        # มี URL จริง (ไม่ใช่ google news)
        canon_url = item.get('canon_url') or ''
        if 'news.google.com' not in canon_url and canon_url:
            score += 10
        
        # มี summary ยาว
        if len(item.get('summary', '')) > 50:
            score += 5
        
        # มีวันที่ชัดเจน
        if item.get('published_dt'):
            score += 3
        
        # มาจากเว็บข่าวที่เชื่อถือได้
        domain = extract_domain(canon_url)
        if domain in ['reuters.com', 'bloomberg.com', 'bangkokpost.com']:
            score += 5
        
        return score
    
    def _select_better_news(self, item1: dict, item2: dict) -> dict:
        """เลือกข่าวที่ดีกว่าจากข่าวที่คล้ายกัน"""
        score1 = self._score_news_item(item1)
        score2 = self._score_news_item(item2)
        
        if DEBUG_FILTERING:
            print(f"  [DEDUP] ข่าว 1: {score1} คะแนน | ข่าว 2: {score2} คะแนน")
        
        # เลือกข่าวที่มีคะแนนสูงกว่า
        return item1 if score1 >= score2 else item2
    
    def fetch_and_filter_news(self):
        """Fetch and filter news from all feeds"""
        all_news = []
        
        # รีเซ็ตสถิติ
        self.filter_stats = {
            'total_processed': 0,
            'filtered_by': {
                'no_title': 0,
                'no_url': 0,
                'already_sent': 0,
                'out_of_window': 0,
                'not_allowed_source': 0,
                'invalid_energy_news': 0,
                'no_country': 0,
                'duplicate_in_session': 0,
                'similar_news_exists': 0,
                'passed': 0
            }
        }
        self.filtered_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name} ({feed_type})...")
            
            try:
                entries = fetch_feed_with_retry(feed_name, feed_url)
                
                # สำหรับเว็บตรง ไม่ต้องกรอง MAX_PER_FEED มากเกินไป
                limit = 20 if feed_type == "direct" else MAX_PER_FEED
                
                for entry in entries[:limit]:
                    self.filter_stats['total_processed'] += 1
                    news_item, filter_reason = self._process_entry_with_debug(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        self.filter_stats['filtered_by']['passed'] += 1
                        print(f"  ✓ {news_item['title'][:50]}...")
                    elif filter_reason:
                        print(f"  ✗ {filter_reason}")
                        
            except Exception as e:
                print(f"  ✗ Error: {str(e)}")
        
        # Step 1.5: Remove group duplicates
        all_news = self._remove_group_duplicates(all_news)
        
        # Sort by date (ใหม่ที่สุดก่อน)
        all_news.sort(key=lambda x: -((x.get('published_dt') or datetime.min).timestamp()))
        
        return all_news
    
    def _process_entry_with_debug(self, entry, feed_name: str, feed_type: str):
        """Process individual news entry with debug info"""
        item = parse_entry(entry, feed_name, feed_type)
        
        # Basic validation
        if not item["title"]:
            self.filter_stats['filtered_by']['no_title'] += 1
            self.filtered_news.append({
                'title': 'ไม่มีหัวข้อข่าว',
                'reason': 'ไม่มีหัวข้อข่าว',
                'details': 'title is empty'
            })
            return None, f"✗ ไม่มีหัวข้อข่าว"
        
        if not item["url"]:
            self.filter_stats['filtered_by']['no_url'] += 1
            self.filtered_news.append({
                'title': item.get('title', 'ไม่มีหัวข้อ')[:50],
                'reason': 'ไม่มี URL',
                'details': 'url is empty'
            })
            return None, f"✗ ไม่มี URL"
        
        # Check if already sent
        if item["canon_url"] in self.sent_links or item["url"] in self.sent_links:
            self.filter_stats['filtered_by']['already_sent'] += 1
            self.filtered_news.append({
                'title': item['title'][:50],
                'reason': 'ส่งแล้วก่อนหน้า',
                'details': f"URL: {item['canon_url'][:50] if item['canon_url'] else item['url'][:50]}"
            })
            return None, f"✗ ส่งแล้วก่อนหน้า: {item['title'][:30]}..."
        
        # Check time window
        if item["published_dt"] and not in_time_window(item["published_dt"], WINDOW_HOURS):
            self.filter_stats['filtered_by']['out_of_window'] += 1
            if item["published_dt"]:
                time_diff = now_tz() - item["published_dt"]
                hours_diff = time_diff.total_seconds() / 3600
                self.filtered_news.append({
                    'title': item['title'][:50],
                    'reason': 'เกินเวลาที่กำหนด',
                    'details': f"เผยแพร่: {item['published_dt'].strftime('%Y-%m-%d %H:%M')} ({hours_diff:.1f} ชม.ที่ผ่านมา)"
                })
            return None, f"✗ เกินเวลาที่กำหนด (WINDOW_HOURS={WINDOW_HOURS} ชั่วโมง)"
        
        # สำหรับเว็บตรง (direct) ไม่ต้องตรวจสอบ ALLOWED_NEWS_SOURCES
        if feed_type != "direct":
            # ตรวจสอบว่า URL นี้มาจากเว็บข่าวที่อนุญาตหรือไม่
            display_url = item["canon_url"] or item["url"]
            if not is_allowed_source(display_url):
                self.filter_stats['filtered_by']['not_allowed_source'] += 1
                domain = extract_domain(display_url)
                self.filtered_news.append({
                    'title': item['title'][:50],
                    'reason': 'แหล่งข่าวไม่อนุญาต',
                    'details': f"โดเมน: {domain}"
                })
                return None, f"✗ แหล่งข่าวไม่อนุญาต: {domain}"
        
        # Combine text for analysis
        full_text = f"{item['title']} {item['summary']}"
        
        # Step 1: Enhanced keyword filtering
        is_valid, reason, details = EnhancedKeywordFilter.check_valid_energy_news(full_text)
        
        # ✅ **เพิ่ม: ตรวจสอบว่าข่าวสำคัญพิเศษหรือไม่**
        is_important = self._is_important_energy_news(item)
        
        if not is_valid and is_important:
            # ข่าวสำคัญที่ควรอนุญาต แม้จะไม่ผ่านการกรองปกติ
            reasons_list = details if details else [reason]
            reasons_list.append("แต่เป็นข่าวพลังงานสำคัญระดับโลก")
            
            if DEBUG_FILTERING:
                print(f"  [IMPORTANT] ข่าวสำคัญผ่านการตรวจสอบ: {item['title'][:50]}...")
            
            # ดำเนินการต่อ แม้จะไม่ผ่านการกรองปกติ
            # (ไม่เพิ่มสถิติ invalid_energy_news และปล่อยให้ผ่าน)
            pass  # ปล่อยให้ผ่านไปตรวจสอบเงื่อนไขอื่นๆ
        elif not is_valid:
            self.filter_stats['filtered_by']['invalid_energy_news'] += 1
            debug_details = f"{reason}"
            if details:
                debug_details += f" ({'; '.join(details)})"
            
            self.filtered_news.append({
                'title': item['title'][:50],
                'reason': reason,
                'details': '; '.join(details) if details else reason
            })
            
            return None, f"✗ {debug_details}"
        
        # Step 2: Detect country
        country = EnhancedKeywordFilter.detect_country(full_text)
        if not country:
            # สำหรับเว็บพลังงานโดยตรง ให้ใช้ Thailand เป็น default
            if feed_type == "direct":
                country = "Thailand"
            else:
                self.filter_stats['filtered_by']['no_country'] += 1
                self.filtered_news.append({
                    'title': item['title'][:50],
                    'reason': 'ไม่พบประเทศที่เกี่ยวข้อง',
                    'details': 'ไม่พบชื่อประเทศในเนื้อหาข่าว'
                })
                return None, f"✗ ไม่พบประเทศที่เกี่ยวข้อง"
        
        # Step 3: ตรวจสอบข่าวซ้ำใน session เดียวกัน
        title_lower = item['title'].lower()
        for existing_title in self._title_cache:
            if self._is_similar_title(title_lower, existing_title, threshold=0.7):
                self.filter_stats['filtered_by']['duplicate_in_session'] += 1
                self.filtered_news.append({
                    'title': item['title'][:50],
                    'reason': 'ข่าวซ้ำใน session',
                    'details': f"คล้ายกับ: {existing_title[:50]}..."
                })
                return None, f"✗ ข่าวซ้ำใน session"
        self._title_cache.append(title_lower)
        
        # Step 4: Check for similar existing news
        existing_item = self._find_similar_news(item, country)
        if existing_item:
            self.filter_stats['filtered_by']['similar_news_exists'] += 1
            selected_item = self._select_better_news(item, existing_item)
            if selected_item == item:
                # แทนที่ข่าวเก่าด้วยข่าวใหม่
                self._processed_items = [it for it in self._processed_items if it != existing_item]
                self.filtered_news.append({
                    'title': existing_item['title'][:50],
                    'reason': 'ถูกแทนที่ด้วยข่าวที่ดีกว่า',
                    'details': f"แทนที่ด้วย: {item['title'][:50]}..."
                })
            else:
                # ข่าวใหม่แย่กว่า ไม่ต้องเพิ่ม
                self.filtered_news.append({
                    'title': item['title'][:50],
                    'reason': 'ถูกแทนที่ด้วยข่าวที่ดีกว่า',
                    'details': f"มีข่าวที่ดีกว่าอยู่แล้ว: {existing_item['title'][:50]}..."
                })
                return None, f"✗ มีข่าวที่ดีกว่าอยู่แล้ว"
        
        # Step 5: LLM analysis (สำหรับสรุปข่าวเท่านั้น)
        llm_summary = ""
        if USE_LLM_SUMMARY and self.llm_analyzer:
            llm_analysis = self.llm_analyzer.analyze_news(item['title'], item['summary'])
            
            # ใช้ LLM country ถ้าตรวจพบ
            if llm_analysis['country'] and llm_analysis['country'] in PROJECTS_BY_COUNTRY:
                country = llm_analysis['country']
            
            # ใช้ summary จาก LLM
            if llm_analysis.get('summary_th'):
                llm_summary = llm_analysis['summary_th']
        
        # Get project hints for this country
        project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
        
        # ดึงชื่อเว็บข่าว
        display_url = item["canon_url"] or item["url"]
        source_name = self.get_source_name(display_url)
        
        # Build final news item
        final_item = {
            'title': item['title'][:100],
            'url': item['url'],
            'canon_url': item['canon_url'],
            'source_name': source_name,
            'domain': extract_domain(display_url),
            'summary': item['summary'][:200],
            'published_dt': item['published_dt'],
            'country': country,
            'project_hints': project_hints,
            'llm_summary': llm_summary,
            'feed': feed_name,
            'feed_type': feed_type,
            'simple_summary': create_simple_summary(full_text, 100)
        }
        
        # เก็บไว้ใน processed items
        self._processed_items.append(final_item)
        
        return final_item, None
    
    def _find_similar_news(self, new_item: dict, country: str):
        """ค้นหาข่าวที่คล้ายกัน"""
        for existing in self._processed_items:
            # ตรวจสอบประเทศเดียวกัน
            if existing.get('country') != country:
                continue
            
            # ตรวจสอบหัวข้อคล้ายกัน
            title_similarity = self._is_similar_title(
                new_item['title'].lower(),
                existing['title'].lower(),
                threshold=0.7
            )
            
            # ถ้าคล้ายกันมาก
            if title_similarity:
                return existing
        
        return None
    
    def _remove_group_duplicates(self, news_items):
        """ลบข่าวซ้ำที่มาจากเหตุการณ์เดียวกัน"""
        unique_items = []
        
        for item in news_items:
            group_key = self._create_group_key(item)
            
            if group_key in self._group_cache:
                if DEBUG_FILTERING:
                    print(f"  ✗ ข่าวกลุ่มซ้ำ: {item['title'][:40]}...")
                continue
            
            self._group_cache.add(group_key)
            unique_items.append(item)
        
        if DEBUG_FILTERING:
            print(f"[DEDUP] หลังจากลบข่าวซ้ำ: {len(news_items)} -> {len(unique_items)} ข่าว")
        return unique_items

# =============================================================================
# ENHANCED LINE MESSAGE BUILDER
# =============================================================================
class EnhancedLineMessageBuilder:
    @staticmethod
    def create_flex_bubble(news_item):
        """Create a LINE Flex Bubble for a news item"""
        title = cut(news_item.get('title', ''), 80)
        
        # Format timestamp
        pub_dt = news_item.get('published_dt')
        time_str = pub_dt.strftime("%d/%m/%Y %H:%M") if pub_dt else ""
        
        # สีตามประเทศ
        colors = {
            "Thailand": "#FF6B6B",
            "Vietnam": "#4ECDC4",
            "Myanmar": "#FFD166",
            "Malaysia": "#06D6A0",
            "Indonesia": "#118AB2",
            "UAE": "#9D4EDD",
            "Oman": "#F15BB5",
            "Kazakhstan": "#00BBF9",
            "International": "#888888"
        }
        
        color = colors.get(news_item.get('country', 'International'), "#888888")
        
        # Build bubble contents
        contents = [
            {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": title,
                        "weight": "bold",
                        "size": "md",
                        "wrap": True,
                        "color": "#FFFFFF"
                    }
                ],
                "backgroundColor": color,
                "paddingAll": "12px",
                "cornerRadius": "8px"
            }
        ]
        
        # Add metadata - เวลาและแหล่งข่าว
        metadata_parts = []
        if time_str:
            metadata_parts.append(time_str)
        if news_item.get('feed'):
            metadata_parts.append(news_item['feed'])
        
        if metadata_parts:
            contents.append({
                "type": "text",
                "text": " | ".join(metadata_parts),
                "size": "xs",
                "color": "#888888",
                "margin": "sm"
            })
        
        # ✅ **เพิ่มชื่อเว็บข่าวในบรรทัดใหม่**
        if news_item.get('source_name'):
            # ใช้ชื่อเว็บข่าวจาก dictionary ของเรา
            contents.append({
                "type": "text",
                "text": f"📰 {news_item['source_name']}",
                "size": "xs",
                "color": "#666666",
                "margin": "sm"
            })
        elif news_item.get('domain'):
            # ถ้าไม่มีชื่อเว็บข่าว ให้ใช้ domain
            contents.append({
                "type": "text",
                "text": f"🌐 {cut(news_item['domain'], 30)}",
                "size": "xs",
                "color": "#666666",
                "margin": "sm"
            })
        
        # Add country
        contents.append({
            "type": "text",
            "text": f"ประเทศ: {news_item.get('country', 'N/A')}",
            "size": "sm",
            "margin": "xs",
            "color": color,
            "weight": "bold"
        })
        
        # Add project hints
        if news_item.get('project_hints'):
            hints_text = ", ".join(news_item['project_hints'][:2])
            contents.append({
                "type": "text",
                "text": f"โครงการที่เกี่ยวข้อง: {hints_text}",
                "size": "sm",
                "color": "#2E7D32",
                "wrap": True,
                "margin": "xs"
            })
        
        # ✅ **เพิ่มสรุปข่าวแบบเรียบง่าย**
        summary_text = ""
        
        # 1. พยายามใช้สรุปจาก LLM ก่อน
        if news_item.get('llm_summary'):
            summary_text = news_item['llm_summary']
        # 2. ถ้าไม่มีจาก LLM ให้ใช้ simple summary
        elif news_item.get('simple_summary'):
            summary_text = news_item['simple_summary']
        # 3. Fallback ใช้ summary จาก RSS
        elif news_item.get('summary'):
            summary_text = create_simple_summary(news_item['summary'], 120)
        
        # ถ้ายังไม่มีสรุป ให้สร้างจาก title
        if not summary_text or len(summary_text.strip()) < 10:
            summary_text = f"{news_item.get('title', 'ข่าวพลังงาน')[:60]}..."
        
        # เพิ่มบล็อกสรุป (แบบเรียบง่าย)
        if summary_text:
            contents.append({
                "type": "text",
                "text": cut(summary_text, 120),
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#424242"
            })
        
        # Create bubble
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": contents,
                "paddingAll": "12px",
                "spacing": "sm"
            }
        }
        
        # Add button if URL exists
        url = news_item.get('canon_url') or news_item.get('url')
        if url and len(url) < 1000:
            bubble["footer"] = {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "action": {
                            "type": "uri",
                            "label": "อ่านข่าวเต็ม",
                            "uri": url
                        }
                    }
                ]
            }
        
        return bubble
    
    @staticmethod
    def create_carousel_message(news_items):
        """Create LINE carousel message from news items"""
        bubbles = []
        
        for item in news_items[:BUBBLES_PER_CAROUSEL]:
            bubble = EnhancedLineMessageBuilder.create_flex_bubble(item)
            if bubble:
                bubbles.append(bubble)
        
        if not bubbles:
            return None
        
        return {
            "type": "flex",
            "altText": f"สรุปข่าวพลังงาน {datetime.now(TZ).strftime('%d/%m/%Y')} ({len(bubbles)} ข่าว)",
            "contents": {
                "type": "carousel",
                "contents": bubbles
            }
        }

# =============================================================================
# LINE SENDER
# =============================================================================
class LineSender:
    def __init__(self, access_token):
        self.access_token = access_token
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
    
    def send_message(self, message_obj):
        """Send message to LINE"""
        if DRY_RUN:
            print("\n" + "="*60)
            print("DRY RUN - Would send the following news:")
            print("="*60)
            
            contents = message_obj.get('contents', {}).get('contents', [])
            for i, bubble in enumerate(contents):
                body_contents = bubble.get('body', {}).get('contents', [])
                title = ""
                source = ""
                country = ""
                
                for content in body_contents:
                    if content.get('type') == 'text':
                        text = content.get('text', '')
                        if len(text) > 10 and not title:
                            title = text[:60]
                        elif '📰' in text or '🌐' in text:
                            source = text
                        elif text.startswith("ประเทศ:"):
                            country = text.replace("ประเทศ: ", "")
                
                print(f"{i+1}. {title}")
                if country:
                    print(f"   ประเทศ: {country}")
                if source:
                    print(f"   แหล่งข่าว: {source}")
                print()
            
            print(f"Total: {len(contents)} news items")
            return True
        
        url = "https://api.line.me/v2/bot/message/broadcast"
        
        try:
            response = requests.post(
                url,
                headers=self.headers,
                json={"messages": [message_obj]},
                timeout=30
            )
            
            if response.status_code == 200:
                print("[LINE] Message sent successfully!")
                return True
            else:
                print(f"[LINE] Error {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"[LINE] Exception: {str(e)}")
            return False

# =============================================================================
# MAIN FUNCTION
# =============================================================================
def main():
    print("="*60)
    print("ระบบติดตามข่าวพลังงาน - แก้ไขปัญหาข่าวซ้ำและข่าวไม่เกี่ยวข้อง")
    print("="*60)
    
    # Configuration check
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    if USE_LLM_SUMMARY and not GROQ_API_KEY:
        print("[WARNING] LLM summary enabled but no GROQ_API_KEY provided")
        print("[INFO] Will use simple summary for all news")
    
    print(f"\n[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No (simple summary)'}")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] Debug filtering: {'Yes' if DEBUG_FILTERING else 'No'}")
    print(f"[CONFIG] Allowed news sources: {ALLOWED_NEWS_SOURCES_LIST if ALLOWED_NEWS_SOURCES_LIST else 'All sources'}")
    print(f"[CONFIG] Strict filtering: {'Yes' if STRICT_FILTERING else 'No'}")
    print(f"[CONFIG] Allow market news: {'Yes' if ALLOW_MARKET_NEWS else 'No'}")
    print(f"[CONFIG] จำนวน feed ทั้งหมด: {len(FEEDS)}")
    print(f"[CONFIG] Feed รายการ: {[f[0] for f in FEEDS]}")
    
    # Initialize components
    processor = EnhancedNewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # Step 1: Fetch and filter news
    print("\n[1] กำลังดึงและกรองข่าว...")
    news_items = processor.fetch_and_filter_news()
    
    # Print filtering statistics
    print(f"\n[FILTER STATISTICS]")
    print(f"  รวมข่าวที่ประมวลผล: {processor.filter_stats['total_processed']}")
    print(f"  ผ่านการกรอง: {processor.filter_stats['filtered_by']['passed']}")
    print(f"  ไม่ผ่านการกรอง: {processor.filter_stats['total_processed'] - processor.filter_stats['filtered_by']['passed']}")
    
    if processor.filter_stats['total_processed'] - processor.filter_stats['filtered_by']['passed'] > 0:
        print(f"\n  รายละเอียดการกรอง:")
        for reason, count in processor.filter_stats['filtered_by'].items():
            if reason != 'passed' and count > 0:
                print(f"    - {reason}: {count} ข่าว")
        
        # แสดงตัวอย่างข่าวที่ไม่ผ่านการกรอง
        if len(processor.filtered_news) > 0:
            print(f"\n  ตัวอย่างข่าวที่ไม่ผ่านการกรอง (แสดง {min(10, len(processor.filtered_news))} อันดับแรก):")
            for i, filtered in enumerate(processor.filtered_news[:10]):
                print(f"    {i+1}. {filtered['title']}")
                print(f"       เหตุผล: {filtered['reason']}")
                if filtered.get('details'):
                    print(f"       รายละเอียด: {filtered['details']}")
                print()
    
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่ที่เกี่ยวข้อง")
        return
    
    print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
    
    # Count statistics
    llm_summary_count = sum(1 for item in news_items if item.get('llm_summary'))
    direct_count = sum(1 for item in news_items if item.get('feed_type') == 'direct')
    
    # นับจำนวนข่าวแยกตามแหล่งข่าว
    source_counts = {}
    country_counts = {}
    for item in news_items:
        source = item.get('source_name') or item.get('domain', 'Unknown')
        source_counts[source] = source_counts.get(source, 0) + 1
        
        country = item.get('country', 'Unknown')
        country_counts[country] = country_counts.get(country, 0) + 1
    
    print(f"   - สรุปด้วย AI: {llm_summary_count} ข่าว")
    print(f"   - ข่าวจากเว็บตรง: {direct_count} ข่าว")
    print(f"   - แหล่งข่าวที่พบ:")
    for source, count in sorted(source_counts.items()):
        print(f"     • {source}: {count} ข่าว")
    print(f"   - แบ่งตามประเทศ:")
    for country, count in sorted(country_counts.items()):
        print(f"     • {country}: {count} ข่าว")
    
    # Step 2: Create LINE message
    print("\n[3] กำลังสร้างข้อความ LINE...")
    line_message = EnhancedLineMessageBuilder.create_carousel_message(news_items)
    
    if not line_message:
        print("[ERROR] ไม่สามารถสร้างข้อความได้")
        return
    
    # Step 3: Send message
    print("\n[4] กำลังส่งข้อความ...")
    success = line_sender.send_message(line_message)
    
    # Step 4: Mark as sent if successful
    if success and not DRY_RUN:
        for item in news_items:
            append_sent_link(item.get('canon_url') or item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    print("\n" + "="*60)
    print("ดำเนินการเสร็จสิ้น")
    print("="*60)

if __name__ == "__main__":
    main()
