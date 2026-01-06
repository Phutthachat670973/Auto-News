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

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "72"))  # เพิ่มเป็น 72 ชั่วโมง
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "25"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]
MAX_MESSAGES_PER_RUN = int(os.getenv("MAX_MESSAGES_PER_RUN", "10"))
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))

# Sent links tracking
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

# =============================================================================
# TEXT CLEANER
# =============================================================================
class TextCleaner:
    """ทำความสะอาดและตรวจสอบคุณภาพข้อความ"""
    
    BAD_PHRASES = [
        "สายบำรณีม", "อ่านบำรณีม", "อินโดสันซิปไตยเสียแบบปลายดังกล่าวให้ฟ้า",
        "อ่านข่าวเต็ม", "อ่านต่อ", "คลิกเพื่ออ่านต่อ", "อ่านเพิ่มเติม",
        "ข่าวที่เกี่ยวข้อง", "แนะนำข่าว", "ติดตามข่าว", "แชร์ข่าวนี้",
        "Advertisement", "Promoted", "Sponsored", "โฆษณา"
    ]
    
    @staticmethod
    def clean_text(text: str) -> str:
        """ทำความสะอาดข้อความ"""
        if not text:
            return ""
        
        # ลบข้อความที่ไม่ต้องการ
        for phrase in TextCleaner.BAD_PHRASES:
            text = text.replace(phrase, "")
        
        # ลบ HTML tags และ entities
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'&[a-z]+;', '', text)
        
        # ลบ URL
        text = re.sub(r'https?://\S+', '', text)
        
        # ลบอักขระพิเศษที่มากเกินไป
        text = re.sub(r'[.,!?;:]{3,}', '...', text)
        
        # ลบช่องว่างซ้ำและขึ้นบรรทัดใหม่
        text = re.sub(r'\s+', ' ', text)
        text = text.replace('\n', ' ').replace('\r', ' ')
        
        return text.strip()
    
    @staticmethod
    def is_meaningful_text(text: str, min_words: int = 5) -> bool:
        """ตรวจสอบว่าข้อความมีความหมายหรือไม่"""
        if not text:
            return False
        
        text = TextCleaner.clean_text(text)
        if not text:
            return False
        
        words = text.split()
        if len(words) < min_words:
            return False
        
        # ตรวจสอบว่ามี keyword ที่มีความหมาย
        meaningful_keywords = [
            'พลังงาน', 'ไฟฟ้า', 'ก๊าซ', 'น้ำมัน', 'โครงการ',
            'energy', 'electricity', 'power', 'gas', 'oil', 'project',
            'โรงไฟฟ้า', 'พลังงาน', 'เชื้อเพลิง', 'LNG', 'พลังงานทดแทน'
        ]
        
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in meaningful_keywords)

# =============================================================================
# PROJECT DATABASE (อัปเดตให้ครบถ้วน)
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
    "Myanmar": [
        "โครงการซอติก้า", "Zawtika", "โครงการยาดานา", "Yadana", 
        "โครงการเมียนมา เอ็ม 3", "Myanmar M3", "โครงการ Yetagun", "Yetagun"
    ],
    "Malaysia": [
        "Malaysia SK309", "SK309", "Malaysia SK311", "SK311", 
        "Malaysia Block H", "Block H", "Malaysia SK410B", "SK410B",
        "Malaysia SK417", "SK417", "Malaysia SK405B", "SK405B",
        "Malaysia SK438", "SK438", "Malaysia SK314A", "SK314A",
        "Malaysia SK325", "SK325", "Malaysia SB412", "SB412",
        "Malaysia Block K", "Block K", "Gumusut-Kakap", "Malaysia LNG"
    ],
    "Vietnam": [
        "โครงการเวียดนาม 16-1", "Vietnam 16-1", "16-1", "Block B", 
        "48/95", "52/97", "9-2", "โครงการ Sao Vang-Dai Nguyet", "Sao Vang-Dai Nguyet"
    ],
    "Indonesia": [
        "โครงการนาทูน่า ซี เอ", "Natuna Sea A", "โครงการ Tangguh LNG", "Tangguh LNG",
        "โครงการ Masela", "Masela", "โครงการ Indonesia Deepwater Development", "IDD"
    ],
    "Kazakhstan": ["โครงการดุงกา", "Dunga", "โครงการ Tengiz", "Tengiz"],
    "Oman": [
        "Oman Block 61", "Block 61", "Oman Block 6", "PDO",
        "Oman Block 53", "Block 53", "Onshore Block 12", "Oman LNG", "Oman LNG Project"
    ],
    "UAE": [
        "Abu Dhabi Offshore 1", "Abu Dhabi Offshore 2", "Abu Dhabi Offshore 3", 
        "Ghasha", "Ghasha Concession", "ADNOC Gas Processing", "AGP"
    ],
    "Algeria": ["433a", "416b", "Hassi Bir Rekaiz", "โครงการ In Salah", "In Salah"],
    "Mozambique": ["Mozambique Area 1", "Mozambique LNG", "Area 1", "โครงการ Coral FLNG", "Coral FLNG"],
    "Australia": ["PTTEP Australasia", "Australasia", "โครงการ Ichthys LNG", "Ichthys LNG"],
    "Mexico": ["Mexico Block 12", "Block 12", "Mexico Block 29", "Block 29"],
    "Brunei": ["Brunei LNG", "โครงการ Champion", "Champion"],
    "Qatar": ["Qatar LNG", "Qatar North Field", "North Field Expansion"],
    "Russia": ["โครงการ Sakhalin", "Sakhalin", "Arctic LNG 2"],
}

# =============================================================================
# COMPREHENSIVE ENERGY NEWS FEEDS
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    """Generate Google News RSS URL"""
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

# =============================================================================
# ENHANCED NEWS FEEDS - เพิ่มแหล่งข่าวพลังงานแบบครบวงจร
# =============================================================================
FEEDS = [
    # ==================== แหล่งข่าวภาษาไทย ====================
    # 1. ข่าวพลังงานไทยจากสื่อหลัก
    ("GoogleNews_TH_Energy", "thai_energy", gnews_rss(
        '(พลังงาน OR ไฟฟ้า OR "ค่าไฟ" OR ก๊าซ OR LNG OR น้ำมัน OR "โรงไฟฟ้า" OR "พลังงานทดแทน") site:.th',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # 2. ข่าวธุรกิจพลังงาน (แหล่งเฉพาะทาง)
    ("BangkokBizNews_Energy", "thai_business", gnews_rss(
        '(พลังงาน OR ไฟฟ้า OR ก๊าซ) site:bangkokbiznews.com',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # 3. ประชาชาติธุรกิจ - พลังงาน
    ("Prachachat_Energy", "thai_business", gnews_rss(
        '(พลังงาน OR ไฟฟ้า) site:prachachat.net',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # 4. ฐานเศรษฐกิจ - พลังงาน
    ("Thansettakij_Energy", "thai_business", gnews_rss(
        '(พลังงาน OR ไฟฟ้า) site:thansettakij.com',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # 5. Post Today - พลังงาน
    ("PostToday_Energy", "thai_business", gnews_rss(
        '(พลังงาน OR ไฟฟ้า) site:posttoday.com',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # 6. สำนักข่าวกรมธุรกิจพลังงาน (หากมี RSS)
    ("กรมธุรกิจพลังงาน", "thai_official", gnews_rss(
        'กรมธุรกิจพลังงาน OR "สำนักงานนโยบายและแผนพลังงาน"',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # 7. การไฟฟ้าฝ่ายผลิตแห่งประเทศไทย
    ("EGAT_News", "thai_official", gnews_rss(
        'กฟผ OR "การไฟฟ้าฝ่ายผลิต" site:egat.co.th',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # 8. PTT และ PTTEP
    ("PTT_Group", "thai_corporate", gnews_rss(
        'PTT OR PTTEP OR "บริษัท ปตท." site:pttplc.com OR site:pttep.com',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # ==================== แหล่งข่าวภาษาอังกฤษ ====================
    # 9. Reuters Energy
    ("Reuters_Energy", "international", "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best&sector=energy-environment"),
    
    # 10. Bloomberg Energy
    ("Bloomberg_Energy", "international", gnews_rss(
        '(energy OR oil OR gas OR power) site:bloomberg.com',
        hl="en", gl="US", ceid="US:en"
    )),
    
    # 11. Financial Times Energy
    ("FT_Energy", "international", gnews_rss(
        '(energy OR oil OR gas) site:ft.com',
        hl="en", gl="US", ceid="US:en"
    )),
    
    # 12. Energy-specific international sources
    ("OilPrice_News", "energy_international", "https://oilprice.com/rss/main"),
    
    ("S&P_Global_Platts", "energy_international", "https://www.spglobal.com/platts/en/rss-feeds/oil"),
    
    ("Argus_Media", "energy_international", "https://www.argusmedia.com/en/rss-feeds"),
    
    # 13. International Energy Agency (IEA)
    ("IEA_News", "international_official", "https://www.iea.org/news-and-events/rss"),
    
    # 14. World Bank Energy
    ("WorldBank_Energy", "international_official", gnews_rss(
        'energy site:worldbank.org',
        hl="en", gl="US", ceid="US:en"
    )),
    
    # ==================== แหล่งข่าวตามประเทศ ====================
    # 15. Vietnam Energy News
    ("Vietnam_Energy", "vietnam", gnews_rss(
        '(energy OR electricity OR power) Vietnam site:vietnamnews.vn OR site:vneconomy.vn',
        hl="en", gl="VN", ceid="VN:en"
    )),
    
    # 16. Malaysia Energy News
    ("Malaysia_Energy", "malaysia", gnews_rss(
        '(energy OR Petronas OR oil OR gas) Malaysia',
        hl="en", gl="MY", ceid="MY:en"
    )),
    
    # 17. Indonesia Energy News
    ("Indonesia_Energy", "indonesia", gnews_rss(
        '(energy OR oil OR gas) Indonesia site:jakartaglobe.id OR site:thejakartapost.com',
        hl="en", gl="ID", ceid="ID:en"
    )),
    
    # 18. Middle East Energy
    ("MiddleEast_Energy", "middle_east", gnews_rss(
        '(energy OR oil OR gas) (UAE OR Saudi OR Qatar OR Oman)',
        hl="en", gl="AE", ceid="AE:en"
    )),
    
    # 19. Australia Energy
    ("Australia_Energy", "australia", gnews_rss(
        '(energy OR LNG OR gas) Australia',
        hl="en", gl="AU", ceid="AU:en"
    )),
]

# =============================================================================
# COUNTRY DETECTION (ปรับปรุงให้ครอบคลุม)
# =============================================================================
class CountryDetector:
    """ตรวจจับประเทศจากเนื้อหาข่าว"""
    
    COUNTRY_PATTERNS = {
        "Thailand": [
            r'\bประเทศไทย\b', r'\bไทย\b', r'\bthailand\b', r'\bbangkok\b',
            r'\bกระทรวงพลังงาน\b', r'\bกฟผ\b', r'\bกกพ\b', r'\bพีทีที\b',
            r'\bpattaya\b', r'\bchiang mai\b', r'\bกรุงเทพ\b'
        ],
        "Myanmar": [
            r'\bเมียนมา\b', r'\bmyanmar\b', r'\byangon\b', r'\bย่างกุ้ง\b',
            r'\bnaypyidaw\b', r'\bmoge\b', r'\bmyanmar oil\b'
        ],
        "Malaysia": [
            r'\bมาเลเซีย\b', r'\bmalaysia\b', r'\bkuala lumpur\b',
            r'\bpetronas\b', r'\bsabah\b', r'\bsarawak\b', r'\bklcc\b'
        ],
        "Vietnam": [
            r'\bเวียดนาม\b', r'\bvietnam\b', r'\bhanoi\b', r'\bho chi minh\b',
            r'\bpetrovietnam\b', r'\bpv oil\b', r'\bda nang\b'
        ],
        "Indonesia": [
            r'\bอินโดนีเซีย\b', r'\bindonesia\b', r'\bjakarta\b',
            r'\bpertamina\b', r'\bbali\b', r'\bsumatra\b', r'\bjava\b'
        ],
        "Kazakhstan": [
            r'\bคาซัคสถาน\b', r'\bkazakhstan\b', r'\bastana\b',
            r'\bkazmunaigas\b', r'\balmaty\b'
        ],
        "Oman": [
            r'\bโอมาน\b', r'\boman\b', r'\bmuscat\b', r'\bpdo\b',
            r'\boq\b', r'\boman lng\b'
        ],
        "UAE": [
            r'\bสหรัฐอาหรับเอมิเรตส์\b', r'\buae\b', r'\babu dhabi\b',
            r'\bdubai\b', r'\badnoc\b', r'\bemirates\b'
        ],
        "Australia": [
            r'\bออสเตรเลีย\b', r'\baustralia\b', r'\bsydney\b',
            r'\bmelbourne\b', r'\bperth\b', r'\bbrisbane\b'
        ],
        "Mexico": [
            r'\bเม็กซิโก\b', r'\bmexico\b', r'\bmexico city\b',
            r'\bpemex\b', r'\bguadalajara\b'
        ],
    }
    
    @classmethod
    def detect_country(cls, text: str) -> str:
        """ตรวจจับประเทศจากข้อความ"""
        if not text:
            return ""
        
        text_lower = text.lower()
        
        for country, patterns in cls.COUNTRY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    return country
        
        return ""

# =============================================================================
# KEYWORD FILTERS (ปรับปรุง)
# =============================================================================
class KeywordFilter:
    OFFICIAL_SOURCES = [
        'ratchakitcha.soc.go.th', 'energy.go.th', 'egat.co.th', 
        'pptplc.com', 'pttep.com', 'reuters.com', 'bloomberg.com',
        'ft.com', 'iea.org', 'worldbank.org', 'spglobal.com',
        '.go.th', '.gov', '.ac.th', '.org'
    ]
    
    OFFICIAL_KEYWORDS = [
        'กระทรวงพลังงาน', 'กรมธุรกิจพลังงาน', 'กฟผ', 'การไฟฟ้า',
        'คณะกรรมการกำกับกิจการพลังงาน', 'กกพ', 'สำนักงานนโยบายและแผนพลังงาน',
        'รัฐมนตรีพลังงาน', 'ประกาศ', 'มติคณะรัฐมนตรี', 'ครม.', 'ราชกิจจานุเบกษา',
        'minister', 'ministry', 'regulation', 'policy', 'tariff', 'approval',
        'แถลงการณ์', 'ข้อกำหนด', 'กฎระเบียบ', 'regulation', 'directive',
        'official statement', 'government announcement'
    ]
    
    ENERGY_KEYWORDS = [
        'พลังงาน', 'ไฟฟ้า', 'ค่าไฟ', 'ก๊าซ', 'LNG', 'น้ำมัน', 'เชื้อเพลิง',
        'โรงไฟฟ้า', 'พลังงานทดแทน', 'โซลาร์', 'พลังงานลม', 'พลังงานชีวมวล',
        'พลังงานความร้อน', 'พลังงานนิวเคลียร์', 'ถ่านหิน', 'พลังงานแสงอาทิตย์',
        'energy', 'electricity', 'power', 'gas', 'oil', 'fuel', 'petroleum',
        'power plant', 'renewable', 'solar', 'wind', 'biomass', 'nuclear',
        'coal', 'hydroelectric', 'geothermal', 'natural gas', 'crude oil',
        'refinery', 'exploration', 'drilling', 'offshore', 'pipeline'
    ]
    
    @classmethod
    def is_official_source(cls, url: str) -> bool:
        """Check if URL is from official source"""
        if not url:
            return False
        
        domain = urlparse(url).netloc.lower()
        return any(official in domain for official in cls.OFFICIAL_SOURCES)
    
    @classmethod
    def contains_official_keywords(cls, text: str) -> bool:
        """Check if text contains official keywords"""
        if not text:
            return False
        
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in cls.OFFICIAL_KEYWORDS)
    
    @classmethod
    def is_energy_related(cls, text: str) -> bool:
        """Check if text is energy related"""
        if not text:
            return False
        
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in cls.ENERGY_KEYWORDS)

# =============================================================================
# UTILITY FUNCTIONS
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

def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()

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

# =============================================================================
# RSS PARSING (ปรับปรุง)
# =============================================================================
def fetch_feed(name: str, section: str, url: str):
    try:
        d = feedparser.parse(url)
        entries = d.entries or []
        print(f"[FEED] {name}: {len(entries)} entries")
        return entries
    except Exception as e:
        print(f"[FEED ERROR] {name}: {str(e)}")
        return []

def parse_entry(e, feed_name: str, section: str):
    title = TextCleaner.clean_text(getattr(e, "title", "") or "")
    link = (getattr(e, "link", "") or "").strip()
    summary = TextCleaner.clean_text(getattr(e, "summary", "") or "")
    published = getattr(e, "published", None) or getattr(e, "updated", None)

    # ถ้าข้อความไม่มีความหมาย ให้ข้าม
    combined_text = f"{title} {summary}"
    if not TextCleaner.is_meaningful_text(combined_text):
        return None

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
        "title": title[:150],
        "url": normalize_url(link),
        "canon_url": normalize_url(canon),
        "summary": summary[:300],
        "published_dt": published_dt,
        "feed": feed_name,
        "section": section,
    }

# =============================================================================
# ENHANCED LLM ANALYZER
# =============================================================================
class EnhancedLLMAnalyzer:
    def __init__(self, api_key: str, model: str, endpoint: str):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
    
    def analyze_news(self, title: str, summary: str, url: str = "") -> dict:
        """Analyze news using LLM with better error handling"""
        if not self.api_key:
            return self._get_default_analysis()
        
        # ตรวจสอบว่าเนื้อหามีความหมายหรือไม่
        combined_text = f"{title} {summary}"
        if not TextCleaner.is_meaningful_text(combined_text, min_words=3):
            return self._get_default_analysis()
        
        system_prompt = """คุณเป็นผู้เชี่ยวชาญวิเคราะห์ข่าวพลังงานสำหรับบริษัทพลังงานชั้นนำ
        ตอบกลับเป็น JSON เท่านั้นตามรูปแบบนี้:
        {
            "relevant": true/false,
            "country": "ชื่อประเทศหรือค่าว่าง",
            "official": true/false,
            "summary_th": "สรุปภาษาไทย 1-2 ประโยค",
            "topics": ["หัวข้อ1", "หัวข้อ2"],
            "impact": "ต่ำ/ปานกลาง/สูง",
            "project_impact": ["โครงการที่เกี่ยวข้อง1", "โครงการที่เกี่ยวข้อง2"]
        }
        
        เกณฑ์:
        - relevant: เกี่ยวข้องกับพลังงาน โครงการพลังงาน นโยบายพลังงาน
        - country: ระบุประเทศจากเนื้อหา (ถ้ามี)
        - official: เป็นข่าวทางการจากหน่วยงานรัฐ
        - summary_th: สรุปสั้นๆ เป็นภาษาไทย
        - topics: หัวข้อสำคัญ
        - impact: ผลกระทบต่อธุรกิจพลังงาน
        - project_impact: โครงการพลังงานที่อาจได้รับผลกระทบ"""
        
        user_prompt = f"""โปรดวิเคราะห์ข่าวต่อไปนี้:
        
        หัวข้อ: {title}
        
        เนื้อหา: {summary}
        
        โปรดวิเคราะห์และตอบเป็น JSON เท่านั้น:"""
        
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
                    "max_tokens": 500
                },
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"[LLM] HTTP Error {response.status_code}")
                return self._get_default_analysis()
            
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    analysis = json.loads(json_match.group())
                    
                    # Validate and clean up
                    return {
                        "relevant": bool(analysis.get("relevant", True)),
                        "country": str(analysis.get("country", "")).strip(),
                        "official": bool(analysis.get("official", False)),
                        "summary_th": TextCleaner.clean_text(str(analysis.get("summary_th", "")))[:200],
                        "topics": [str(t).strip() for t in analysis.get("topics", []) if t],
                        "impact": str(analysis.get("impact", "ต่ำ")).strip(),
                        "project_impact": [str(p).strip() for p in analysis.get("project_impact", []) if p]
                    }
                except json.JSONDecodeError:
                    print(f"[LLM] JSON decode error in response")
                    
        except requests.exceptions.Timeout:
            print("[LLM] Request timeout")
        except Exception as e:
            print(f"[LLM] Error: {str(e)}")
        
        return self._get_default_analysis()
    
    def _get_default_analysis(self):
        """Default analysis when LLM fails"""
        return {
            "relevant": True,
            "country": "",
            "official": False,
            "summary_th": "ข่าวพลังงานที่เกี่ยวข้องกับธุรกิจ",
            "topics": ["พลังงาน"],
            "impact": "ต่ำ",
            "project_impact": []
        }

# =============================================================================
# MAIN NEWS PROCESSOR
# =============================================================================
class EnhancedNewsProcessor:
    def __init__(self):
        self.sent_links = read_sent_links()
        self.llm_analyzer = EnhancedLLMAnalyzer(GROQ_API_KEY, GROQ_MODEL, GROQ_ENDPOINT) if GROQ_API_KEY else None
    
    def fetch_and_filter_news(self):
        """Fetch and filter news from all feeds"""
        all_news = []
        feed_stats = {}
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name} ({feed_type})...")
            
            try:
                entries = fetch_feed(feed_name, feed_type, feed_url)
                processed_count = 0
                
                for entry in entries[:MAX_PER_FEED]:
                    news_item = self._process_entry(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        processed_count += 1
                
                feed_stats[feed_name] = processed_count
                print(f"  ✓ Processed: {processed_count} items")
                        
            except Exception as e:
                print(f"  ✗ Error in {feed_name}: {str(e)}")
                feed_stats[feed_name] = 0
        
        # Print summary
        print("\n" + "="*60)
        print("FEED SUMMARY:")
        print("="*60)
        total_news = 0
        for feed, count in feed_stats.items():
            print(f"{feed}: {count} news")
            total_news += count
        print(f"\nTotal news collected: {total_news}")
        
        # Sort by importance (official first, then impact, then date)
        all_news.sort(key=lambda x: (
            -x.get('is_official', 0),
            -self._impact_score(x.get('llm_analysis', {}).get('impact', 'ต่ำ')),
            -(x.get('published_dt') or datetime.min).timestamp()
        ))
        
        return all_news[:MAX_MESSAGES_PER_RUN * BUBBLES_PER_CAROUSEL]
    
    def _impact_score(self, impact_str: str) -> int:
        """Convert impact string to score"""
        impact_map = {
            "สูง": 3,
            "high": 3,
            "ปานกลาง": 2,
            "medium": 2,
            "ต่ำ": 1,
            "low": 1
        }
        return impact_map.get(impact_str.lower(), 1)
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """Process individual news entry"""
        item = parse_entry(entry, feed_name, feed_type)
        if not item:
            return None
        
        # Check if already sent
        if item["canon_url"] in self.sent_links or item["url"] in self.sent_links:
            return None
        
        # Check time window
        if item["published_dt"] and not in_time_window(item["published_dt"], WINDOW_HOURS):
            return None
        
        # Combine text for analysis
        full_text = f"{item['title']} {item['summary']}"
        
        # Step 1: Energy related check
        if not KeywordFilter.is_energy_related(full_text):
            return None
        
        # Step 2: Detect country
        country = CountryDetector.detect_country(full_text)
        if not country and feed_type in ['thai_energy', 'thai_official', 'thai_business']:
            country = "Thailand"  # Default for Thai energy feeds
        
        if not country:
            # Try to detect from feed name
            for c in PROJECTS_BY_COUNTRY.keys():
                if c.lower() in feed_name.lower():
                    country = c
                    break
        
        if not country:
            return None  # Skip if no country detected
        
        # Step 3: Check if official
        is_official = (
            KeywordFilter.is_official_source(item['url']) or 
            KeywordFilter.contains_official_keywords(full_text) or
            feed_type in ['thai_official', 'international_official']
        )
        
        # Step 4: Get project hints for this country
        project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:3]
        
        # Step 5: LLM analysis (if enabled)
        llm_analysis = None
        if USE_LLM_SUMMARY and self.llm_analyzer:
            llm_analysis = self.llm_analyzer.analyze_news(item['title'], item['summary'], item['url'])
            
            # Use LLM country if detected and valid
            if llm_analysis['country'] and llm_analysis['country'] in PROJECTS_BY_COUNTRY:
                country = llm_analysis['country']
                project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:3]
            
            # Update official status from LLM
            if llm_analysis['official']:
                is_official = True
            
            # Use project impact from LLM if available
            if llm_analysis['project_impact']:
                project_hints = llm_analysis['project_impact'][:3]
        
        # Build final news item
        return {
            'title': item['title'],
            'url': item['url'],
            'canon_url': item['canon_url'],
            'summary': item['summary'],
            'published_dt': item['published_dt'],
            'country': country,
            'project_hints': project_hints,
            'is_official': is_official,
            'llm_analysis': llm_analysis,
            'feed': feed_name,
            'feed_type': feed_type
        }

# =============================================================================
# LINE MESSAGE BUILDER (ปรับปรุง)
# =============================================================================
class LineMessageBuilder:
    @staticmethod
    def create_flex_bubble(news_item):
        """Create a LINE Flex Bubble for a news item"""
        title = cut(news_item.get('title', ''), 100)
        
        # Format timestamp
        pub_dt = news_item.get('published_dt')
        time_str = pub_dt.strftime("%d/%m/%Y %H:%M") if pub_dt else ""
        
        # Determine bubble color and badge
        if news_item.get('is_official'):
            color = "#4CAF50"  # Green for official news
            badge = "📢 ข่าวทางการ"
        elif news_item.get('llm_analysis'):
            color = "#2196F3"  # Blue for LLM-analyzed news
            badge = "🤖 วิเคราะห์ด้วย AI"
        else:
            color = "#FF9800"  # Orange for regular news
            badge = "📰 ข่าวพลังงาน"
        
        # Build bubble contents
        contents = [
            {
                "type": "text",
                "text": title,
                "weight": "bold",
                "size": "md",
                "wrap": True,
                "margin": "md"
            }
        ]
        
        # Add metadata (time and feed)
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
        
        # Add country
        contents.append({
            "type": "text",
            "text": f"ประเทศ: {news_item.get('country', 'N/A')}",
            "size": "sm",
            "margin": "xs"
        })
        
        # Add project hints
        if news_item.get('project_hints'):
            hints_text = ", ".join(news_item['project_hints'][:3])
            contents.append({
                "type": "text",
                "text": f"โครงการที่เกี่ยวข้อง: {hints_text}",
                "size": "sm",
                "color": "#2E7D32",
                "wrap": True,
                "margin": "xs"
            })
        
        # Add LLM summary if available
        if news_item.get('llm_analysis') and news_item['llm_analysis'].get('summary_th'):
            contents.append({
                "type": "text",
                "text": news_item['llm_analysis']['summary_th'],
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#424242"
            })
        
        # Add impact level if available
        if news_item.get('llm_analysis') and news_item['llm_analysis'].get('impact'):
            impact = news_item['llm_analysis']['impact']
            impact_color = "#4CAF50" if impact == "ต่ำ" else "#FF9800" if impact == "ปานกลาง" else "#F44336"
            contents.append({
                "type": "box",
                "layout": "baseline",
                "margin": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": "ระดับผลกระทบ:",
                        "size": "sm",
                        "color": "#666666",
                        "flex": 2
                    },
                    {
                        "type": "text",
                        "text": impact,
                        "size": "sm",
                        "color": impact_color,
                        "weight": "bold",
                        "flex": 1,
                        "align": "end"
                    }
                ]
            })
        
        # Add badge
        contents.append({
            "type": "text",
            "text": badge,
            "size": "xs",
            "color": color,
            "margin": "sm"
        })
        
        # Create bubble
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": contents,
                "paddingAll": "12px"
            }
        }
        
        # Add button if URL exists and is valid
        url = news_item.get('canon_url') or news_item.get('url')
        if url and len(url) < 1000 and url.startswith(('http://', 'https://')):
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
        if not news_items:
            return None
        
        bubbles = []
        for item in news_items[:BUBBLES_PER_CAROUSEL]:
            bubble = LineMessageBuilder.create_flex_bubble(item)
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
                title = bubble.get('body', {}).get('contents', [{}])[0].get('text', 'No title')
                country = ""
                for content in bubble.get('body', {}).get('contents', []):
                    if content.get('text', '').startswith('ประเทศ:'):
                        country = content['text']
                        break
                print(f"{i+1}. {title[:60]}... {country}")
            
            print(f"\nTotal: {len(contents)} news items")
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
    print("ระบบติดตามข่าวพลังงาน - Enhanced Version")
    print("="*60)
    
    # Configuration check
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    if USE_LLM_SUMMARY and not GROQ_API_KEY:
        print("[WARNING] LLM summary enabled but no GROQ_API_KEY provided")
        print("[INFO] Will use keyword-based filtering only")
    
    print(f"\n[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No'}")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] Feeds: {len(FEEDS)} sources")
    
    # Initialize components
    processor = EnhancedNewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # Step 1: Fetch and filter news
    print("\n[1] กำลังดึงและกรองข่าวจากแหล่งต่างๆ...")
    news_items = processor.fetch_and_filter_news()
    
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่ที่เกี่ยวข้อง")
        return
    
    print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
    
    # Count statistics
    official_count = sum(1 for item in news_items if item.get('is_official'))
    llm_count = sum(1 for item in news_items if item.get('llm_analysis'))
    
    print(f"   - ข่าวทางการ: {official_count} ข่าว")
    print(f"   - วิเคราะห์ด้วย AI: {llm_count} ข่าว")
    
    # Group by country for statistics
    country_stats = {}
    for item in news_items:
        country = item.get('country', 'Unknown')
        country_stats[country] = country_stats.get(country, 0) + 1
    
    print(f"\n[3] สถิติตามประเทศ:")
    for country, count in sorted(country_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"   - {country}: {count} ข่าว")
    
    # Step 4: Create LINE message
    print("\n[4] กำลังสร้างข้อความ LINE...")
    line_message = LineMessageBuilder.create_carousel_message(news_items)
    
    if not line_message:
        print("[ERROR] ไม่สามารถสร้างข้อความได้")
        return
    
    # Step 5: Send message
    print("\n[5] กำลังส่งข้อความ...")
    success = line_sender.send_message(line_message)
    
    # Step 6: Mark as sent if successful
    if success and not DRY_RUN:
        for item in news_items:
            append_sent_link(item.get('canon_url') or item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    print("\n" + "="*60)
    print("ดำเนินการเสร็จสิ้น")
    print("="*60)

if __name__ == "__main__":
    main()
