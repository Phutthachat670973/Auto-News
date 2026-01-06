# -*- coding: utf-8 -*-

import os
import re
import json
import time
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, unquote

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
MAX_MESSAGES_PER_RUN = int(os.getenv("MAX_MESSAGES_PER_RUN", "10"))
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))

# Sent links tracking
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

# =============================================================================
# PROJECT DATABASE (Enhanced)
# =============================================================================
PROJECTS_BY_COUNTRY = {
    "Thailand": [
        {
            "patterns": ["โครงการจี 1/61", "G 1/61", "G1/61", "จี 1/61"],
            "official_name": "โครงการ G 1/61",
            "category": "ก๊าซธรรมชาติ",
            "priority": 1
        },
        {
            "patterns": ["โครงการจี 2/61", "G 2/61", "G2/61", "จี 2/61"],
            "official_name": "โครงการ G 2/61",
            "category": "ก๊าซธรรมชาติ",
            "priority": 1
        },
        {
            "patterns": ["โครงการอาทิตย์", "Arthit", "อาทิตย์"],
            "official_name": "โครงการอาทิตย์",
            "category": "ก๊าซธรรมชาติ",
            "priority": 2
        },
        {
            "patterns": ["โครงการเอส 1", "S1", "S 1"],
            "official_name": "โครงการ S1",
            "category": "ก๊าซธรรมชาติ",
            "priority": 2
        },
        {
            "patterns": ["โครงการสัมปทาน 4", "Contract 4", "สัมปทาน 4"],
            "official_name": "โครงการสัมปทาน 4",
            "category": "ปิโตรเลียม",
            "priority": 2
        },
        {
            "patterns": ["โครงการพีทีทีอีพี 1", "PTTEP 1", "PTTEP1"],
            "official_name": "โครงการ PTTEP 1",
            "category": "ปิโตรเลียม",
            "priority": 1
        }
    ],
    "Vietnam": [
        {
            "patterns": ["โครงการเวียดนาม 16-1", "Vietnam 16-1", "16-1", "Block 16-1"],
            "official_name": "โครงการเวียดนาม 16-1",
            "category": "ก๊าซธรรมชาติ",
            "priority": 1
        },
        {
            "patterns": ["Block B", "บล็อก B"],
            "official_name": "Block B",
            "category": "ก๊าซธรรมชาติ",
            "priority": 2
        }
    ],
    "Myanmar": [
        {
            "patterns": ["โครงการซอติก้า", "Zawtika", "ซอติก้า"],
            "official_name": "โครงการซอติก้า",
            "category": "ก๊าซธรรมชาติ",
            "priority": 1
        },
        {
            "patterns": ["โครงการยาดานา", "Yadana", "ยาดานา"],
            "official_name": "โครงการยาดานา",
            "category": "ก๊าซธรรมชาติ",
            "priority": 1
        }
    ]
}

# =============================================================================
# LANGUAGE DETECTOR
# =============================================================================
class LanguageDetector:
    """ตรวจจับและจัดการภาษาข้อความ"""
    
    @staticmethod
    def detect_language(text: str) -> str:
        """ตรวจจับภาษาของข้อความ"""
        if not text:
            return "unknown"
        
        # นับตัวอักษรภาษาไทย
        thai_pattern = re.compile(r'[ก-ฮะ-์]')
        thai_count = len(thai_pattern.findall(text))
        total_chars = len(re.findall(r'\w', text))
        
        if total_chars == 0:
            return "unknown"
        
        if thai_count / total_chars > 0.3:
            return "th"
        else:
            return "en"
    
    @staticmethod
    def normalize_thai_text(text: str) -> str:
        """ทำความสะอาดข้อความภาษาไทย"""
        if not text:
            return text
        
        # ลบช่องว่างเกินและบรรทัดใหม่
        text = re.sub(r'\s+', ' ', text)
        
        # ลบเครื่องหมายพิเศษที่อาจทำให้รูปแบบเสีย
        text = re.sub(r'[•▪▶►●]', '', text)
        
        # ตรวจจับและแยกส่วนที่ผสมภาษาอังกฤษ
        lines = text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # ถ้าบรรทัดมีทั้งไทยและอังกฤษ ให้แยก
            if LanguageDetector.detect_language(line) == "th":
                # ลบส่วนภาษาอังกฤษที่อาจอยู่ในวงเล็บ
                line = re.sub(r'\([^)]*[A-Za-z]+[^)]*\)', '', line)
                line = re.sub(r'\[[^\]]*[A-Za-z]+[^\]]*\]', '', line)
            
            if line:
                cleaned_lines.append(line)
        
        return ' '.join(cleaned_lines)

# =============================================================================
# NEWS QUALITY SCORER
# =============================================================================
class NewsQualityScorer:
    """ประเมินคุณภาพข่าว"""
    
    QUALITY_TIERS = {
        "high": [
            "reuters.com", "bloomberg.com", "energy.go.th", 
            "ratchakitcha.soc.go.th", "egat.co.th", "pptplc.com"
        ],
        "medium": [
            "thansettakij.com", "prachachat.net", "bangkokbiznews.com",
            "komchadluek.net", "matichon.co.th", "dailynews.co.th"
        ],
        "low": ["google.com", "news.google.com"]
    }
    
    @classmethod
    def get_source_tier(cls, url: str) -> str:
        """ได้ระดับของแหล่งข่าว"""
        domain = urlparse(url).netloc.lower()
        
        for tier, domains in cls.QUALITY_TIERS.items():
            if any(d in domain for d in domains):
                return tier
        return "unknown"
    
    @classmethod
    def score_news(cls, news_item: dict) -> float:
        """คำนวณคะแนนคุณภาพข่าว (0-1)"""
        url = news_item.get('url', '')
        
        # คะแนนพื้นฐาน
        score = 0.5
        
        # แหล่งข่าว
        source_tier = cls.get_source_tier(url)
        if source_tier == "high":
            score += 0.3
        elif source_tier == "medium":
            score += 0.1
        
        # ข่าวทางการ
        if news_item.get('is_official'):
            score += 0.2
        
        # มีการอ้างอิงโครงการ
        if news_item.get('has_project_ref'):
            score += 0.15
        
        # มีการวิเคราะห์ด้วย LLM
        if news_item.get('llm_analysis'):
            score += 0.1
        
        # มีวันที่เผยแพร่ชัดเจน
        if news_item.get('published_dt'):
            score += 0.05
        
        return min(1.0, max(0.0, score))

# =============================================================================
# PROJECT NORMALIZER
# =============================================================================
class ProjectNormalizer:
    """มาตรฐานการตั้งชื่อโครงการ"""
    
    @staticmethod
    def normalize_project_name(name: str) -> str:
        """แปลงชื่อโครงการให้เป็นรูปแบบมาตรฐาน"""
        if not name or not isinstance(name, str):
            return ""
        
        name = name.strip()
        
        # ลบช่องว่างเกิน
        name = re.sub(r'\s+', ' ', name)
        
        # มาตรฐานรูปแบบตัวเลข/ตัวอักษร
        # แปลง G1/61 -> G 1/61
        name = re.sub(r'([A-Z])(\d)', r'\1 \2', name)
        
        # แปลง จี1/61 -> จี 1/61
        name = re.sub(r'([ก-ฮ])(\d)', r'\1 \2', name)
        
        # มาตรฐานเครื่องหมาย slash
        name = re.sub(r'(\d)\s*/\s*(\d)', r'\1/\2', name)
        
        # เพิ่มคำนำหน้า "โครงการ" ถ้าจำเป็น
        if not name.startswith('โครงการ') and LanguageDetector.detect_language(name) == "th":
            # ตรวจสอบว่ามีรูปแบบที่ควรมีคำนำหน้า
            pattern = re.compile(r'^(จี|เอส|เอ|บี|ซี|ดี)\s+\d')
            if pattern.match(name):
                name = f'โครงการ {name}'
        
        return name
    
    @staticmethod
    def find_matching_projects(text: str, country: str) -> list:
        """ค้นหาโครงการที่ตรงกับข้อความ"""
        if country not in PROJECTS_BY_COUNTRY:
            return []
        
        matches = []
        text_lower = text.lower()
        
        for project in PROJECTS_BY_COUNTRY[country]:
            for pattern in project["patterns"]:
                pattern_lower = pattern.lower()
                
                # ตรวจสอบรูปแบบในข้อความ
                if pattern_lower in text_lower:
                    matches.append({
                        "official_name": project["official_name"],
                        "category": project.get("category", ""),
                        "priority": project.get("priority", 3),
                        "matched_pattern": pattern
                    })
                    break  # หยุดเมื่อเจอ pattern แรกที่ตรง
        
        # เรียงลำดับตาม priority
        matches.sort(key=lambda x: x["priority"])
        return matches[:3]  # ส่งคืนสูงสุด 3 โครงการ

# =============================================================================
# TIME FORMATTER
# =============================================================================
class TimeFormatter:
    """จัดการการจัดรูปแบบเวลา"""
    
    @staticmethod
    def format_publish_time(published_dt: datetime) -> str:
        """จัดรูปแบบเวลาเผยแพร่ให้เป็นมิตร"""
        if not published_dt:
            return "ไม่ระบุเวลา"
        
        now = datetime.now(TZ)
        diff = now - published_dt
        
        # ใช้เวลาตามโซนเวลาไทย
        published_dt = published_dt.astimezone(TZ)
        
        if diff.days > 30:
            return f"เผยแพร่ {published_dt.strftime('%d/%m/%Y')}"
        elif diff.days > 0:
            return f"{diff.days} วันที่แล้ว"
        elif diff.seconds > 3600:
            hours = diff.seconds // 3600
            return f"{hours} ชั่วโมงที่แล้ว"
        elif diff.seconds > 60:
            minutes = diff.seconds // 60
            return f"{minutes} นาทีที่แล้ว"
        else:
            return "เมื่อสักครู่"
    
    @staticmethod
    def get_time_emoji(published_dt: datetime) -> str:
        """ได้อิโมจิตามเวลา"""
        if not published_dt:
            return "🕐"
        
        hour = published_dt.hour
        
        if 5 <= hour < 12:
            return "🌅"  # เช้า
        elif 12 <= hour < 17:
            return "☀️"   # บ่าย
        elif 17 <= hour < 21:
            return "🌇"   # เย็น
        else:
            return "🌙"   # กลางคืน

# =============================================================================
# KEYWORD FILTERS (Enhanced)
# =============================================================================
class KeywordFilter:
    # แหล่งข่าวทางการและคำสำคัญ
    OFFICIAL_SOURCES = [
        'ratchakitcha.soc.go.th', 'energy.go.th', 'egat.co.th', 
        'pptplc.com', 'pttep.com', 'reuters.com', 'bloomberg.com',
        'bangchak.co.th', 'bangkokbiznews.com', 'thansettakij.com',
        'prachachat.net', 'posttoday.com'
    ]
    
    OFFICIAL_KEYWORDS = [
        'กระทรวงพลังงาน', 'กรมธุรกิจพลังงาน', 'กฟผ', 'การไฟฟ้า',
        'คณะกรรมการกำกับกิจการพลังงาน', 'กกพ', 'สำนักงานนโยบายและแผนพลังงาน',
        'รัฐมนตรีพลังงาน', 'ประกาศ', 'มติคณะรัฐมนตรี', 'ครม.', 'ราชกิจจานุเบกษา',
        'minister', 'ministry', 'regulation', 'policy', 'tariff', 'approval',
        'อนุมัติ', 'อนุญาต', 'ใบอนุญาต', 'สัมปทาน', 'สัญญา'
    ]
    
    ENERGY_KEYWORDS = [
        'พลังงาน', 'ไฟฟ้า', 'ค่าไฟ', 'ก๊าซ', 'LNG', 'น้ำมัน', 'เชื้อเพลิง',
        'โรงไฟฟ้า', 'พลังงานทดแทน', 'โซลาร์', 'พลังงานลม', 'พลังงานชีวมวล',
        'พลังงานแสงอาทิตย์', 'พลังงานน้ำ', 'พลังงานความร้อน',
        'energy', 'electricity', 'power', 'gas', 'oil', 'fuel',
        'power plant', 'renewable', 'solar', 'wind', 'biomass'
    ]
    
    PROJECT_KEYWORDS = [
        'โครงการ', 'สัมปทาน', 'บล็อก', 'block', 'สัญญา', 'อนุมัติ',
        'ก่อสร้าง', 'ดำเนินการ', 'พัฒนา', 'สำรวจ', 'ขุดเจาะ', 'แหล่ง',
        'project', 'concession', 'contract', 'approval', 'construction'
    ]
    
    @classmethod
    def is_official_source(cls, url: str) -> bool:
        """ตรวจสอบว่า URL มาจากแหล่งข่าวทางการหรือไม่"""
        domain = urlparse(url).netloc.lower()
        return any(official in domain for official in cls.OFFICIAL_SOURCES)
    
    @classmethod
    def contains_official_keywords(cls, text: str) -> bool:
        """ตรวจสอบว่าข้อความมีคำสำคัญทางการหรือไม่"""
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in cls.OFFICIAL_KEYWORDS)
    
    @classmethod
    def is_energy_related(cls, text: str) -> bool:
        """ตรวจสอบว่าข้อความเกี่ยวข้องกับพลังงานหรือไม่"""
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in cls.ENERGY_KEYWORDS)
    
    @classmethod
    def contains_project_reference(cls, text: str) -> bool:
        """ตรวจสอบว่าข้อความมีการอ้างอิงโครงการหรือไม่"""
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in cls.PROJECT_KEYWORDS)
    
    @classmethod
    def detect_country(cls, text: str) -> str:
        """ตรวจจับประเทศจากข้อความ"""
        text_lower = text.lower()
        
        country_patterns = {
            "Thailand": ['ไทย', 'ประเทศไทย', 'thailand', 'bangkok', 'กรุงเทพ'],
            "Myanmar": ['เมียนมา', 'myanmar', 'ย่างกุ้ง', 'yangon', 'พม่า'],
            "Malaysia": ['มาเลเซีย', 'malaysia', 'กัวลาลัมเปอร์', 'kuala lumpur'],
            "Vietnam": ['เวียดนาม', 'vietnam', 'ฮานอย', 'hanoi', 'เวียด'],
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
# FEEDS
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

FEEDS = [
    ("GoogleNewsTH", "thai", gnews_rss(
        '(พลังงาน OR "ค่าไฟ" OR ก๊าซ OR LNG OR น้ำมัน OR ไฟฟ้า OR "โรงไฟฟ้า" OR "พลังงานทดแทน" OR "โซลาร์")',
        hl="th", gl="TH", ceid="TH:th"
    )),
    ("GoogleNewsEN", "international", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas OR renewable OR solar) AND (Thailand OR Vietnam OR Malaysia OR Indonesia OR Myanmar)',
        hl="en", gl="US", ceid="US:en"
    )),
]

# =============================================================================
# UTILITIES (Enhanced)
# =============================================================================
def now_tz() -> datetime:
    return datetime.now(TZ)

def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    try:
        u = urlparse(url)
        # ลบ fragment และ query string ที่ไม่จำเป็น
        query_params = parse_qs(u.query)
        # รักษาเฉพาะ query ที่สำคัญ
        keep_params = ['p', 'id', 'article', 'news']
        filtered_query = {k: v for k, v in query_params.items() if k in keep_params}
        
        if filtered_query:
            from urllib.parse import urlencode
            new_query = urlencode(filtered_query, doseq=True)
            u = u._replace(query=new_query, fragment="")
        else:
            u = u._replace(query="", fragment="")
        
        return u.geturl()
    except Exception:
        return url

def shorten_google_news_url(url: str) -> str:
    """ดึง URL จริงจาก Google News redirect"""
    url = normalize_url(url)
    if not url:
        return url
    try:
        u = urlparse(url)
        if "news.google.com" in u.netloc:
            qs = parse_qs(u.query)
            if "url" in qs and qs["url"]:
                actual_url = unquote(qs["url"][0])
                # ลบ tracking parameters
                tracking_params = ['utm_source', 'utm_medium', 'utm_campaign', 'fbclid']
                parsed = urlparse(actual_url)
                query_params = parse_qs(parsed.query)
                
                # ลบ tracking parameters
                for param in tracking_params:
                    query_params.pop(param, None)
                
                if query_params:
                    from urllib.parse import urlencode
                    new_query = urlencode(query_params, doseq=True)
                    parsed = parsed._replace(query=new_query)
                else:
                    parsed = parsed._replace(query="")
                
                return parsed.geturl()
    except Exception:
        pass
    return url

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

def read_sent_links() -> set:
    sent = set()
    today_file = os.path.join(SENT_DIR, now_tz().strftime("%Y-%m-%d") + ".txt")
    
    # อ่านไฟล์วันนี้
    if os.path.exists(today_file):
        try:
            with open(today_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sent.add(line)
        except Exception:
            pass
    
    # อ่านไฟล์เมื่อวาน (สำหรับป้องกันการส่งซ้ำ)
    yesterday = now_tz() - timedelta(days=1)
    yesterday_file = os.path.join(SENT_DIR, yesterday.strftime("%Y-%m-%d") + ".txt")
    if os.path.exists(yesterday_file):
        try:
            with open(yesterday_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sent.add(line)
        except Exception:
            pass
    
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
    if len(s) <= n:
        return s
    # ตัดคำภาษาไทยให้เหมาะสม
    if LanguageDetector.detect_language(s) == "th":
        # พยายามตัดที่ช่องว่าง
        if len(s) > n:
            # หาช่องว่างใกล้จุดตัด
            space_pos = s[:n].rfind(' ')
            if space_pos > n * 0.7:  # ถ้ามีช่องว่างใกล้จุดตัดพอสมควร
                return s[:space_pos] + "…"
    return s[: n - 1].rstrip() + "…"

# =============================================================================
# SAFE FEED FETCHER
# =============================================================================
def safe_fetch_feed(name: str, section: str, url: str, retries: int = 3):
    """ดึงข้อมูล feed อย่างปลอดภัย พร้อมระบบลองใหม่"""
    for attempt in range(retries):
        try:
            print(f"[FEED] {name}: ลองดึงข้อมูลครั้งที่ {attempt + 1}")
            d = feedparser.parse(url)
            entries = d.entries or []
            print(f"[FEED] {name}: พบ {len(entries)} ข่าว")
            return entries
        except Exception as e:
            print(f"[ERROR] ล้มเหลวในการดึง {name} (ครั้งที่ {attempt + 1}/{retries}): {str(e)}")
            if attempt < retries - 1:
                wait_time = 2 ** attempt  # exponential backoff
                print(f"[WAIT] รอ {wait_time} วินาที...")
                time.sleep(wait_time)
    return []

# =============================================================================
# RSS PARSING (Enhanced)
# =============================================================================
def parse_entry(e, feed_name: str, section: str):
    title = (getattr(e, "title", "") or "").strip()
    link = (getattr(e, "link", "") or "").strip()
    summary = (getattr(e, "summary", "") or "").strip()
    published = getattr(e, "published", None) or getattr(e, "updated", None)
    
    # ทำความสะอาดข้อความ
    title = LanguageDetector.normalize_thai_text(title)
    summary = LanguageDetector.normalize_thai_text(summary)
    
    # ตรวจจับภาษา
    language = LanguageDetector.detect_language(title + " " + summary)
    
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
        "language": language,
        "original_published": published
    }

# =============================================================================
# LLM ANALYZER (Enhanced for Thai)
# =============================================================================
class LLMAnalyzer:
    def __init__(self, api_key: str, model: str, endpoint: str):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
    
    def analyze_news(self, title: str, summary: str, language: str = "th") -> dict:
        """วิเคราะห์ข่าวด้วย LLM"""
        if not self.api_key:
            return self._get_default_analysis()
        
        # ปรับ system prompt สำหรับภาษาไทย
        if language == "th":
            system_prompt = """คุณเป็นผู้ช่วยวิเคราะห์ข่าวพลังงาน
            ตอบกลับเป็น JSON เท่านั้นตามรูปแบบนี้:
            {
                "relevant": true/false,
                "country": "ชื่อประเทศหรือค่าว่าง",
                "official": true/false,
                "summary_th": "สรุปภาษาไทยอย่างสั้น 1-2 ประโยค",
                "topics": ["หัวข้อ1", "หัวข้อ2"],
                "impact_level": "สูง/กลาง/ต่ำ",
                "project_mentioned": true/false
            }
            
            เกณฑ์:
            - relevant: เกี่ยวข้องกับพลังงาน โครงการพลังงาน นโยบายพลังงาน
            - country: ระบุประเทศจากเนื้อหา
            - official: เป็นข่าวทางการ ประกาศราชการ มติคณะรัฐมนตรี
            - summary_th: สรุปสั้นๆ เป็นภาษาไทย
            - topics: หัวข้อ เช่น พลังงาน, ไฟฟ้า, ก๊าซ, นโยบาย, โครงการ
            - impact_level: ระดับผลกระทบ (สูง=มีผลต่อนโยบาย/ราคา, กลาง=อัปเดตความคืบหน้า, ต่ำ=ข่าวทั่วไป)
            - project_mentioned: มีการกล่าวถึงโครงการพลังงานเฉพาะเจาะจงหรือไม่"""
        else:
            system_prompt = """You are a news analyzer for energy news.
            Respond only in JSON format:
            {
                "relevant": true/false,
                "country": "country name or empty",
                "official": true/false,
                "summary_th": "summary in Thai (1-2 sentences)",
                "topics": ["topic1", "topic2"],
                "impact_level": "high/medium/low",
                "project_mentioned": true/false
            }
            
            Criteria:
            - relevant: related to energy, energy projects, energy policies
            - country: identify country from content
            - official: official news, government announcements, cabinet resolutions
            - summary_th: short summary in Thai language
            - topics: topics like energy, electricity, gas, policy, project
            - impact_level: impact level (high=affects policy/prices, medium=progress update, low=general news)
            - project_mentioned: mentions specific energy projects or not"""
        
        user_prompt = f"""ข่าว: {title[:200]}
        
        เนื้อหา: {summary[:500]}
        
        ภาษา: {language}
        
        โปรดวิเคราะห์ข่าวนี้ตามเกณฑ์ที่กำหนด:"""
        
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
                    "max_tokens": 500,
                    "response_format": {"type": "json_object"}
                },
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"[LLM] HTTP Error {response.status_code}: {response.text[:200]}")
                return self._get_default_analysis()
            
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            
            try:
                analysis = json.loads(content)
                
                # ตรวจสอบและทำความสะอาดข้อมูล
                return {
                    "relevant": bool(analysis.get("relevant", False)),
                    "country": str(analysis.get("country", "")).strip(),
                    "official": bool(analysis.get("official", False)),
                    "summary_th": str(analysis.get("summary_th", "")).strip()[:200],
                    "topics": [str(t).strip() for t in analysis.get("topics", []) if t and len(str(t).strip()) > 0],
                    "impact_level": str(analysis.get("impact_level", "ต่ำ")).strip(),
                    "project_mentioned": bool(analysis.get("project_mentioned", False))
                }
                
            except json.JSONDecodeError as je:
                print(f"[LLM] JSON Parse Error: {je}")
                print(f"[LLM] Response: {content[:200]}")
                
        except requests.exceptions.Timeout:
            print("[LLM] Request timeout")
        except Exception as e:
            print(f"[LLM] Error: {str(e)}")
        
        return self._get_default_analysis()
    
    def _get_default_analysis(self):
        return {
            "relevant": False,
            "country": "",
            "official": False,
            "summary_th": "",
            "topics": [],
            "impact_level": "ต่ำ",
            "project_mentioned": False
        }

# =============================================================================
# NEWS PROCESSOR (Enhanced)
# =============================================================================
class NewsProcessor:
    def __init__(self):
        self.sent_links = read_sent_links()
        self.llm_analyzer = LLMAnalyzer(GROQ_API_KEY, GROQ_MODEL, GROQ_ENDPOINT) if GROQ_API_KEY else None
        print(f"[INIT] โหลดข่าวที่ส่งแล้ว {len(self.sent_links)} ลิงก์")
    
    def fetch_and_filter_news(self):
        """ดึงและกรองข่าวจากแหล่งข้อมูลทั้งหมด"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[ดึงข้อมูล] {feed_name}...")
            
            try:
                entries = safe_fetch_feed(feed_name, feed_type, feed_url)
                
                processed_count = 0
                for entry in entries[:MAX_PER_FEED]:
                    news_item = self._process_entry(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        processed_count += 1
                        print(f"  ✓ {news_item['title'][:50]}...")
                
                print(f"  รวม: {processed_count} ข่าวที่เกี่ยวข้องจาก {len(entries[:MAX_PER_FEED])} ข่าว")
                        
            except Exception as e:
                print(f"  ✗ ข้อผิดพลาด: {str(e)}")
        
        # เรียงลำดับตามความสำคัญ
        all_news.sort(key=lambda x: (
            -x.get('is_official', 0),
            -NewsQualityScorer.score_news(x),
            -(x.get('published_dt') or datetime.min).timestamp()
        ))
        
        # จำกัดจำนวนข่าวที่ส่ง
        if len(all_news) > MAX_MESSAGES_PER_RUN:
            print(f"\n[กรอง] จำกัดข่าวจาก {len(all_news)} เป็น {MAX_MESSAGES_PER_RUN} ข่าว")
            all_news = all_news[:MAX_MESSAGES_PER_RUN]
        
        return all_news
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """ประมวลผลข่าวแต่ละรายการ"""
        item = parse_entry(entry, feed_name, feed_type)
        
        # ตรวจสอบข้อมูลพื้นฐาน
        if not item["title"] or not item["url"]:
            return None
        
        # ตรวจสอบว่าส่งแล้วหรือยัง
        if item["canon_url"] in self.sent_links or item["url"] in self.sent_links:
            return None
        
        # ตรวจสอบกรอบเวลา
        if item["published_dt"] and not in_time_window(item["published_dt"], WINDOW_HOURS):
            return None
        
        # รวมข้อความสำหรับการวิเคราะห์
        full_text = f"{item['title']} {item['summary']}"
        
        # ขั้นตอนที่ 1: กรองตามหัวข้อพลังงาน
        if not KeywordFilter.is_energy_related(full_text):
            return None
        
        # ขั้นตอนที่ 2: ตรวจจับประเทศ
        country = KeywordFilter.detect_country(full_text)
        if not country:
            # ถ้าไม่พบประเทศในข้อความ ลองใช้ภาษาเป็นตัวบอก
            if item["language"] == "th":
                country = "Thailand"
            else:
                # สำหรับข่าวภาษาอังกฤษที่ไม่มีชื่อประเทศ อาจเป็นข่าวนานาชาติ
                # ตรวจสอบ feed name
                if "TH" in feed_name:
                    country = "Thailand"
                else:
                    return None  # ถ้าไม่สามารถระบุประเทศได้ ให้ข้าม
        
        # ขั้นตอนที่ 3: ตรวจสอบว่าเป็นข่าวทางการ
        is_official = (
            KeywordFilter.is_official_source(item['url']) or 
            KeywordFilter.contains_official_keywords(full_text)
        )
        
        # ขั้นตอนที่ 4: ตรวจสอบการอ้างอิงโครงการ
        has_project_ref = KeywordFilter.contains_project_reference(full_text)
        
        # ขั้นตอนที่ 5: ค้นหาโครงการที่เกี่ยวข้อง
        matched_projects = ProjectNormalizer.find_matching_projects(full_text, country)
        
        # ขั้นตอนที่ 6: การวิเคราะห์ด้วย LLM (ถ้าเปิดใช้งาน)
        llm_analysis = None
        if USE_LLM_SUMMARY and self.llm_analyzer and (is_official or has_project_ref or matched_projects):
            llm_analysis = self.llm_analyzer.analyze_news(
                item['title'], 
                item['summary'],
                item["language"]
            )
            
            # ใช้ประเทศจาก LLM ถ้าตรวจพบ
            if llm_analysis['country'] and llm_analysis['country'] in PROJECTS_BY_COUNTRY:
                country = llm_analysis['country']
            
            # อัปเดตสถานะทางการจาก LLM
            if llm_analysis['official']:
                is_official = True
            
            # อัปเดตการอ้างอิงโครงการจาก LLM
            if llm_analysis['project_mentioned']:
                has_project_ref = True
        
        # ขั้นตอนที่ 7: คำนวณคะแนนคุณภาพ
        quality_score = NewsQualityScorer.score_news({
            'url': item['url'],
            'is_official': is_official,
            'has_project_ref': has_project_ref or bool(matched_projects),
            'llm_analysis': llm_analysis,
            'published_dt': item['published_dt']
        })
        
        # ขั้นตอนที่ 8: เตรียมรายการโครงการสำหรับแสดง
        project_hints = []
        if matched_projects:
            project_hints = [p["official_name"] for p in matched_projects[:2]]
        elif has_project_ref:
            # ถ้ามีการอ้างอิงโครงการแต่ไม่ตรงกับฐานข้อมูล
            project_hints = ["โครงการพลังงาน"]
        
        # สร้างรายการข่าว
        return {
            'title': cut(item['title'], 100),
            'url': item['url'],
            'canon_url': item['canon_url'],
            'summary': cut(item['summary'], 200),
            'published_dt': item['published_dt'],
            'country': country,
            'project_hints': project_hints,
            'matched_projects': matched_projects,
            'is_official': is_official,
            'has_project_ref': has_project_ref or bool(matched_projects),
            'quality_score': quality_score,
            'llm_analysis': llm_analysis,
            'feed': feed_name,
            'language': item['language'],
            'source_tier': NewsQualityScorer.get_source_tier(item['url'])
        }

# =============================================================================
# LINE MESSAGE BUILDER (Enhanced)
# =============================================================================
class LineMessageBuilder:
    @staticmethod
    def create_flex_bubble(news_item):
        """สร้าง LINE Flex Bubble สำหรับข่าวแต่ละรายการ"""
        title = cut(news_item.get('title', ''), 80)
        
        # จัดรูปแบบเวลา
        pub_dt = news_item.get('published_dt')
        time_str = TimeFormatter.format_publish_time(pub_dt)
        time_emoji = TimeFormatter.get_time_emoji(pub_dt) if pub_dt else "🕐"
        
        # กำหนดสีตามประเภทข่าว
        if news_item.get('is_official'):
            color = "#4CAF50"  # สีเขียวสำหรับข่าวทางการ
            badge = "📢 ข่าวทางการ"
            emoji = "🏛️"
        elif news_item.get('llm_analysis'):
            color = "#2196F3"  # สีน้ำเงินสำหรับข่าวที่วิเคราะห์ด้วย AI
            badge = "🤖 วิเคราะห์ AI"
            emoji = "🤖"
        elif news_item.get('quality_score', 0) > 0.7:
            color = "#9C27B0"  # สีม่วงสำหรับข่าวคุณภาพสูง
            badge = "⭐ คุณภาพสูง"
            emoji = "⭐"
        else:
            color = "#FF9800"  # สีส้มสำหรับข่าวทั่วไป
            badge = "📰 ข่าวทั่วไป"
            emoji = "📰"
        
        # กำหนดระดับความสำคัญจาก impact_level
        impact_level = "ต่ำ"
        if news_item.get('llm_analysis'):
            impact_level = news_item['llm_analysis'].get('impact_level', 'ต่ำ')
        
        impact_colors = {
            "สูง": "#F44336",  # สีแดง
            "กลาง": "#FF9800",  # สีส้ม
            "ต่ำ": "#4CAF50",   # สีเขียว
            "high": "#F44336",
            "medium": "#FF9800",
            "low": "#4CAF50"
        }
        
        impact_color = impact_colors.get(impact_level, "#4CAF50")
        
        # สร้างเนื้อหา bubble
        contents = [
            {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {
                        "type": "text",
                        "text": emoji,
                        "size": "sm",
                        "flex": 0
                    },
                    {
                        "type": "text",
                        "text": f" {time_emoji} {time_str}",
                        "size": "xs",
                        "color": "#666666",
                        "flex": 1,
                        "margin": "sm"
                    }
                ],
                "margin": "xs"
            },
            {
                "type": "text",
                "text": title,
                "weight": "bold",
                "size": "md",
                "wrap": True,
                "margin": "md"
            }
        ]
        
        # เพิ่มประเทศและแหล่งข่าว
        metadata = []
        country = news_item.get('country', 'N/A')
        feed = news_item.get('feed', '')
        source_tier = news_item.get('source_tier', 'unknown')
        
        country_text = f"🇹🇭 {country}" if country == "Thailand" else f"🌍 {country}"
        metadata.append(country_text)
        
        if feed:
            metadata.append(feed)
        
        if source_tier != "unknown":
            tier_text = {"high": "แหล่งข่าวชั้นนำ", "medium": "แหล่งข่าวทั่วไป", "low": "แหล่งข่าวออนไลน์"}
            metadata.append(tier_text.get(source_tier, ""))
        
        if metadata:
            contents.append({
                "type": "text",
                "text": " | ".join(filter(None, metadata)),
                "size": "xs",
                "color": "#888888",
                "margin": "sm",
                "wrap": True
            })
        
        # เพิ่มโครงการที่เกี่ยวข้อง
        if news_item.get('project_hints'):
            hints_text = ", ".join(news_item['project_hints'])
            contents.append({
                "type": "text",
                "text": f"🔗 โครงการ: {hints_text}",
                "size": "sm",
                "color": "#2E7D32",
                "wrap": True,
                "margin": "xs"
            })
        
        # เพิ่มหัวข้อจาก LLM
        if news_item.get('llm_analysis') and news_item['llm_analysis'].get('topics'):
            topics = news_item['llm_analysis']['topics'][:3]
            topics_text = "🏷️ " + ", ".join(topics)
            contents.append({
                "type": "text",
                "text": topics_text,
                "size": "xs",
                "color": "#757575",
                "wrap": True,
                "margin": "xs"
            })
        
        # เพิ่มสรุปจาก LLM
        if news_item.get('llm_analysis') and news_item['llm_analysis'].get('summary_th'):
            contents.append({
                "type": "text",
                "text": news_item['llm_analysis']['summary_th'],
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#424242"
            })
        
        # เพิ่มระดับผลกระทบ
        contents.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": f"ระดับผลกระทบ: {impact_level}",
                    "size": "xs",
                    "color": impact_color,
                    "weight": "bold",
                    "flex": 1
                },
                {
                    "type": "text",
                    "text": badge,
                    "size": "xs",
                    "color": color,
                    "align": "end"
                }
            ],
            "margin": "sm"
        })
        
        # สร้าง bubble
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
        
        # เพิ่มปุ่มอ่านข่าวเต็ม
        url = news_item.get('canon_url') or news_item.get('url')
        if url and len(url) < 1000:  # จำกัดความยาว URL สำหรับ LINE
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
                            "label": "📖 อ่านข่าวเต็ม",
                            "uri": url
                        },
                        "color": color
                    }
                ]
            }
        
        return bubble
    
    @staticmethod
    def create_carousel_message(news_items):
        """สร้างข้อความ carousel LINE จากรายการข่าว"""
        if not news_items:
            return None
        
        bubbles = []
        
        for item in news_items[:BUBBLES_PER_CAROUSEL]:
            bubble = LineMessageBuilder.create_flex_bubble(item)
            if bubble:
                bubbles.append(bubble)
        
        if not bubbles:
            return None
        
        # สร้าง header สำหรับ carousel
        total_official = sum(1 for item in news_items[:BUBBLES_PER_CAROUSEL] if item.get('is_official'))
        total_projects = sum(1 for item in news_items[:BUBBLES_PER_CAROUSEL] if item.get('has_project_ref'))
        
        # สร้างข้อความสรุป
        summary_text = f"📰 ข่าวพลังงานล่าสุด ({len(bubbles)} ข่าว)"
        if total_official > 0:
            summary_text += f" | 📢 ข่าวทางการ: {total_official}"
        if total_projects > 0:
            summary_text += f" | 🔗 โครงการ: {total_projects}"
        
        return {
            "type": "flex",
            "altText": summary_text,
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
        """ส่งข้อความไปยัง LINE"""
        if DRY_RUN:
            print("\n" + "="*60)
            print("โหมดทดสอบ - จะส่งข่าวต่อไปนี้:")
            print("="*60)
            
            # แสดงข้อมูลข่าวสำหรับการทดสอบ
            contents = message_obj.get('contents', {}).get('contents', [])
            for i, bubble in enumerate(contents):
                title_elements = bubble.get('body', {}).get('contents', [{}])
                title = "ไม่มีหัวข้อ"
                for element in title_elements:
                    if element.get('type') == 'text' and element.get('weight') == 'bold':
                        title = element.get('text', 'ไม่มีหัวข้อ')
                        break
                
                # หาประเทศ
                country = "N/A"
                for element in title_elements:
                    if element.get('type') == 'text' and 'ประเทศ:' in element.get('text', ''):
                        country = element.get('text', 'N/A')
                        break
                
                print(f"{i+1}. {title[:50]}...")
                print(f"   📍 {country}")
            
            print(f"\nรวม: {len(contents)} ข่าว")
            print("="*60)
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
                print("[LINE] ส่งข้อความสำเร็จ!")
                return True
            else:
                print(f"[LINE] ข้อผิดพลาด {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"[LINE] ข้อผิดพลาด: {str(e)}")
            return False

# =============================================================================
# MAIN FUNCTION
# =============================================================================
def main():
    print("="*60)
    print("ระบบติดตามข่าวพลังงาน (Enhanced Version)")
    print("="*60)
    
    # ตรวจสอบการตั้งค่า
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ข้อผิดพลาด] ต้องกำหนด LINE_CHANNEL_ACCESS_TOKEN")
        return
    
    if USE_LLM_SUMMARY and not GROQ_API_KEY:
        print("[คำเตือน] เปิดใช้งานการสรุปด้วย LLM แต่ไม่มี GROQ_API_KEY")
        print("[ข้อมูล] จะใช้การกรองด้วยคีย์เวิร์ดเท่านั้น")
    
    print(f"\n[การตั้งค่า] ใช้ LLM: {'ใช่' if USE_LLM_SUMMARY and GROQ_API_KEY else 'ไม่ใช่'}")
    print(f"[การตั้งค่า] กรอบเวลา: {WINDOW_HOURS} ชั่วโมง")
    print(f"[การตั้งค่า] โหมดทดสอบ: {'ใช่' if DRY_RUN else 'ไม่ใช่'}")
    print(f"[การตั้งค่า] ประเทศที่ติดตาม: {', '.join(PROJECTS_BY_COUNTRY.keys())}")
    
    # เริ่มต้นส่วนประกอบ
    processor = NewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # ขั้นตอนที่ 1: ดึงและกรองข่าว
    print("\n[1] กำลังดึงและกรองข่าว...")
    news_items = processor.fetch_and_filter_news()
    
    if not news_items:
        print("\n[ข้อมูล] ไม่พบข่าวใหม่ที่เกี่ยวข้อง")
        return
    
    print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
    
    # นับสถิติ
    official_count = sum(1 for item in news_items if item.get('is_official'))
    project_count = sum(1 for item in news_items if item.get('has_project_ref'))
    llm_count = sum(1 for item in news_items if item.get('llm_analysis'))
    high_quality = sum(1 for item in news_items if item.get('quality_score', 0) > 0.7)
    
    print(f"   📢 ข่าวทางการ: {official_count} ข่าว")
    print(f"   🔗 มีโครงการ: {project_count} ข่าว")
    print(f"   🤖 วิเคราะห์ด้วย AI: {llm_count} ข่าว")
    print(f"   ⭐ คุณภาพสูง: {high_quality} ข่าว")
    
    # แสดงการกระจายประเทศ
    countries = {}
    for item in news_items:
        country = item.get('country', 'Unknown')
        countries[country] = countries.get(country, 0) + 1
    
    print(f"   🌍 การกระจายประเทศ: {', '.join([f'{c}:{n}' for c, n in countries.items()])}")
    
    # ขั้นตอนที่ 2: สร้างข้อความ LINE
    print("\n[3] กำลังสร้างข้อความ LINE...")
    line_message = LineMessageBuilder.create_carousel_message(news_items)
    
    if not line_message:
        print("[ข้อผิดพลาด] ไม่สามารถสร้างข้อความได้")
        return
    
    # ขั้นตอนที่ 3: ส่งข้อความ
    print("\n[4] กำลังส่งข้อความ...")
    success = line_sender.send_message(line_message)
    
    # ขั้นตอนที่ 4: ทำเครื่องหมายว่าส่งแล้วถ้าสำเร็จ
    if success and not DRY_RUN:
        for item in news_items:
            append_sent_link(item.get('canon_url') or item.get('url'))
        print("\n[สำเร็จ] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    print("\n" + "="*60)
    print("ดำเนินการเสร็จสิ้น")
    print("="*60)

if __name__ == "__main__":
    main()
