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
        "Advertisement", "Promoted", "Sponsored", "โฆษณา",
        "Click here to read more", "Read full story", "Continue reading",
        "อ่านรายละเอียดเพิ่มเติม", "คลิกที่นี่", "ดูเพิ่มเติม"
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
    def extract_meaningful_summary(text: str, max_length: int = 200) -> str:
        """ดึงส่วนที่มีความหมายของข้อความ"""
        text = TextCleaner.clean_text(text)
        if not text:
            return ""
        
        # แยกประโยค
        sentences = re.split(r'[.!?]+\s*', text)
        
        # หาประโยคที่มีความหมาย (มีความยาวพอสมควร)
        meaningful_sentences = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence.split()) >= 5:  # อย่างน้อย 5 คำ
                meaningful_sentences.append(sentence)
        
        # ถ้าไม่มีประโยคที่มีความหมาย ให้ใช้ข้อความที่ตัดแล้ว
        if not meaningful_sentences:
            return text[:max_length]
        
        # รวมประโยคที่มีความหมาย
        summary = ' '.join(meaningful_sentences[:2])  # ใช้ 2 ประโยคแรก
        return summary[:max_length]

# =============================================================================
# URL VALIDATOR
# =============================================================================
class URLValidator:
    """ตรวจสอบและแก้ไขลิงก์ URL"""
    
    @staticmethod
    def is_valid_url(url: str) -> bool:
        """ตรวจสอบว่า URL ถูกต้องและใช้งานได้"""
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        if not url:
            return False
        
        # ตรวจสอบความยาว (LINE จำกัด 1000 ตัวอักษร)
        if len(url) > 1000:
            return False
        
        # ตรวจสอบรูปแบบ URL
        try:
            result = urlparse(url)
            if not all([result.scheme, result.netloc]):
                return False
            
            # ตรวจสอบ scheme
            if result.scheme not in ['http', 'https']:
                return False
            
            # ตรวจสอบ domain
            if len(result.netloc) < 3:
                return False
                
            return True
        except:
            return False
    
    @staticmethod
    def extract_actual_url(google_news_url: str) -> str:
        """ดึง URL จริงจาก Google News URL"""
        if not google_news_url:
            return ""
        
        try:
            # ถ้าเป็น Google News URL พยายามดึง URL จริง
            if "news.google.com" in google_news_url:
                # วิธีที่ 1: ดึงจาก query parameter
                parsed = urlparse(google_news_url)
                query_params = parse_qs(parsed.query)
                
                if 'url' in query_params:
                    actual_url = unquote(query_params['url'][0])
                    if URLValidator.is_valid_url(actual_url):
                        return actual_url
                
                # วิธีที่ 2: ตาม redirect 1 ระดับ
                try:
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Accept': 'text/html,application/xhtml+xml',
                    }
                    response = requests.get(
                        google_news_url, 
                        headers=headers, 
                        timeout=5, 
                        allow_redirects=False
                    )
                    
                    if response.status_code in [301, 302, 303, 307, 308]:
                        location = response.headers.get('Location')
                        if location and URLValidator.is_valid_url(location):
                            return location
                except:
                    pass
        except:
            pass
        
        return google_news_url

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
# COUNTRY DETECTION - ดัดแปลงให้ตรวจจับเฉพาะประเทศใน PROJECTS_BY_COUNTRY
# =============================================================================
class CountryDetector:
    """ตรวจจับประเทศจากเนื้อหาข่าว - เฉพาะประเทศที่มีโครงการ"""
    
    # เก็บเฉพาะประเทศที่มีใน PROJECTS_BY_COUNTRY
    COUNTRY_PATTERNS = {}
    
    # สร้าง patterns จากประเทศใน PROJECTS_BY_COUNTRY
    @classmethod
    def initialize_patterns(cls):
        """เตรียม patterns จากประเทศที่มีโครงการ"""
        if cls.COUNTRY_PATTERNS:
            return
            
        # Patterns สำหรับแต่ละประเทศ
        country_patterns_base = {
            "Thailand": [
                r'\bประเทศไทย\b', r'\bไทย\b', r'\bthailand\b', r'\bbangkok\b',
                r'\bกระทรวงพลังงาน\b', r'\bกฟผ\b', r'\bกกพ\b', r'\bพีทีที\b',
                r'\bกรุงเทพ\b', r'\bchiang mai\b', r'\bสปป\b'
            ],
            "Myanmar": [
                r'\bเมียนมา\b', r'\bmyanmar\b', r'\byangon\b', r'\bย่างกุ้ง\b',
                r'\bnaypyidaw\b', r'\bmoge\b'
            ],
            "Malaysia": [
                r'\bมาเลเซีย\b', r'\bmalaysia\b', r'\bkuala lumpur\b',
                r'\bpetronas\b', r'\bsabah\b', r'\bsarawak\b'
            ],
            "Vietnam": [
                r'\bเวียดนาม\b', r'\bvietnam\b', r'\bhanoi\b', r'\bho chi minh\b',
                r'\bpetrovietnam\b', r'\bda nang\b'
            ],
            "Indonesia": [
                r'\bอินโดนีเซีย\b', r'\bindonesia\b', r'\bjakarta\b',
                r'\bpertamina\b', r'\bbali\b', r'\bsumatra\b'
            ],
            "Kazakhstan": [
                r'\bคาซัคสถาน\b', r'\bkazakhstan\b', r'\bastana\b',
                r'\bkazmunaigas\b'
            ],
            "Oman": [
                r'\bโอมาน\b', r'\boman\b', r'\bmuscat\b', r'\bpdo\b',
                r'\boq\b'
            ],
            "UAE": [
                r'\bสหรัฐอาหรับเอมิเรตส์\b', r'\buae\b', r'\babu dhabi\b',
                r'\bdubai\b', r'\badnoc\b'
            ],
        }
        
        # ใช้เฉพาะประเทศที่มีใน PROJECTS_BY_COUNTRY
        for country in PROJECTS_BY_COUNTRY.keys():
            if country in country_patterns_base:
                cls.COUNTRY_PATTERNS[country] = country_patterns_base[country]
    
    @classmethod
    def detect_country(cls, text: str) -> str:
        """ตรวจจับประเทศจากข้อความ - เฉพาะประเทศที่มีโครงการ"""
        if not text:
            return ""
        
        # เรียก initialize patterns
        if not cls.COUNTRY_PATTERNS:
            cls.initialize_patterns()
        
        text_lower = text.lower()
        
        for country, patterns in cls.COUNTRY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower, re.IGNORECASE):
                    return country
        
        return ""  # คืนค่าว่างถ้าไม่ใช่ประเทศที่ต้องการ

# =============================================================================
# KEYWORD FILTERS
# =============================================================================
class KeywordFilter:
    OFFICIAL_KEYWORDS = [
        'กระทรวงพลังงาน', 'กรมธุรกิจพลังงาน', 'กฟผ', 'การไฟฟ้า',
        'คณะกรรมการกำกับกิจการพลังงาน', 'กกพ', 'สำนักงานนโยบายและแผนพลังงาน',
        'รัฐมนตรีพลังงาน', 'ประกาศ', 'มติคณะรัฐมนตรี', 'ครม.', 'ราชกิจจานุเบกษา',
        'minister', 'ministry', 'regulation', 'policy', 'tariff', 'approval',
        'แถลงการณ์', 'ข้อกำหนด', 'กฎระเบียบ'
    ]
    
    ENERGY_KEYWORDS = [
        'พลังงาน', 'ไฟฟ้า', 'ค่าไฟ', 'ก๊าซ', 'LNG', 'น้ำมัน', 'เชื้อเพลิง',
        'โรงไฟฟ้า', 'พลังงานทดแทน', 'โซลาร์', 'พลังงานลม', 'พลังงานชีวมวล',
        'energy', 'electricity', 'power', 'gas', 'oil', 'fuel',
        'power plant', 'renewable', 'solar', 'wind', 'biomass',
        'ไฟฟ้าส่องสว่าง', 'ไฟฟ้าชุมชน', 'สายส่งไฟฟ้า'
    ]
    
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
    
    @classmethod
    def is_target_country_news(cls, text: str) -> bool:
        """ตรวจสอบว่าเป็นข่าวประเทศที่เราต้องการหรือไม่"""
        if not text:
            return False
        
        text_lower = text.lower()
        
        # รายการคำที่บ่งชี้เป็นข่าวนานาชาติที่เราไม่ต้องการ
        international_indicators = [
            'global', 'world', 'international', 'united nations', 'un ',
            'european union', 'eu ', 'climate summit', 'cop ',
            'g20', 'g7', 'world bank', 'imf', 'opec+', 'international'
        ]
        
        # ถ้ามีคำบ่งชี้ข่าวนานาชาติ ให้ข้าม
        for indicator in international_indicators:
            if indicator in text_lower:
                return False
        
        return True

# =============================================================================
# SIMPLIFIED GOOGLE NEWS RSS FEEDS - ใช้เฉพาะประเทศที่มีโครงการ
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    """Generate Google News RSS URL"""
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

# ใช้เฉพาะ feed ที่เกี่ยวข้องกับประเทศที่มีโครงการ
FEEDS = [
    # ==================== ข่าวพลังงานไทย ====================
    ("Thai_Energy_General", "thai_energy", gnews_rss(
        '(พลังงาน OR ไฟฟ้า OR ก๊าซ OR LNG OR น้ำมัน OR โรงไฟฟ้า OR พลังงานทดแทน) Thailand',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    ("Thai_Energy_Policy", "thai_official", gnews_rss(
        '(กระทรวงพลังงาน OR กรมธุรกิจพลังงาน OR กฟผ OR กกพ OR ค่าไฟ OR Direct PPA) Thailand',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    ("Thai_Business_Energy", "thai_business", gnews_rss(
        '(พลังงาน OR ไฟฟ้า OR ก๊าซ) (ประเทศไทย OR ไทย)',
        hl="th", gl="TH", ceid="TH:th"
    )),
    
    # ==================== ข่าวพลังงานเวียดนาม ====================
    ("Vietnam_Energy", "vietnam_energy", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas OR LNG) Vietnam',
        hl="en", gl="VN", ceid="VN:en"
    )),
    
    # ==================== ข่าวพลังงานมาเลเซีย ====================
    ("Malaysia_Energy", "malaysia_energy", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas OR Petronas) Malaysia',
        hl="en", gl="MY", ceid="MY:en"
    )),
    
    # ==================== ข่าวพลังงานอินโดนีเซีย ====================
    ("Indonesia_Energy", "indonesia_energy", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas OR Pertamina) Indonesia',
        hl="en", gl="ID", ceid="ID:en"
    )),
    
    # ==================== ข่าวพลังงานเมียนมา ====================
    ("Myanmar_Energy", "myanmar_energy", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas) Myanmar',
        hl="en", gl="MM", ceid="MM:en"
    )),
    
    # ==================== ข่าวพลังงานตะวันออกกลาง ====================
    ("Oman_Energy", "oman_energy", gnews_rss(
        '(energy OR oil OR gas) Oman',
        hl="en", gl="OM", ceid="OM:en"
    )),
    
    ("UAE_Energy", "uae_energy", gnews_rss(
        '(energy OR oil OR gas) (UAE OR United Arab Emirates OR Abu Dhabi)',
        hl="en", gl="AE", ceid="AE:en"
    )),
    
    # ==================== ข่าวพลังงานคาซัคสถาน ====================
    ("Kazakhstan_Energy", "kazakhstan_energy", gnews_rss(
        '(energy OR oil OR gas) Kazakhstan',
        hl="en", gl="KZ", ceid="KZ:en"
    )),
]

# =============================================================================
# SIMPLE RSS PARSER
# =============================================================================
class SimpleRSSParser:
    """Parser RSS แบบง่ายๆ"""
    
    @staticmethod
    def fetch_feed(feed_name: str, feed_url: str):
        """ดึงข้อมูล RSS"""
        try:
            print(f"[RSS] Fetching {feed_name}...")
            
            # ตั้งค่า headers
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml',
            }
            
            # ดึงข้อมูล RSS
            response = requests.get(feed_url, headers=headers, timeout=15)
            
            # ตรวจสอบ status code
            if response.status_code != 200:
                print(f"[RSS WARNING] {feed_name}: HTTP {response.status_code}")
                # ลองดึงใหม่โดยไม่ใช้ headers
                response = requests.get(feed_url, timeout=15)
                if response.status_code != 200:
                    print(f"[RSS ERROR] {feed_name}: Failed with HTTP {response.status_code}")
                    return []
            
            # Parse RSS
            feed = feedparser.parse(response.content)
            
            entries = feed.entries or []
            print(f"[RSS] {feed_name}: Found {len(entries)} entries")
            return entries
            
        except requests.exceptions.Timeout:
            print(f"[RSS ERROR] {feed_name}: Timeout")
            return []
        except requests.exceptions.RequestException as e:
            print(f"[RSS ERROR] {feed_name}: Request error - {str(e)}")
            return []
        except Exception as e:
            print(f"[RSS ERROR] {feed_name}: Unexpected error - {str(e)}")
            return []
    
    @staticmethod
    def parse_entry(entry, feed_name: str, feed_type: str):
        """Parse entry"""
        # ดึงข้อมูลพื้นฐาน
        title = TextCleaner.clean_text(getattr(entry, "title", "") or "")
        link = (getattr(entry, "link", "") or "").strip()
        summary = TextCleaner.clean_text(getattr(entry, "summary", "") or "")
        
        # ถ้าไม่มี title หรือ title สั้นเกินไป ให้ข้าม
        if not title or len(title) < 10:
            return None
        
        # ดึงวันที่เผยแพร่
        published = getattr(entry, "published", None) or getattr(entry, "updated", None)
        published_dt = None
        
        try:
            if published:
                published_dt = dateutil_parser.parse(published)
                if published_dt.tzinfo is None:
                    published_dt = TZ.localize(published_dt)
                published_dt = published_dt.astimezone(TZ)
        except:
            published_dt = None
        
        # แก้ไข URL
        actual_url = URLValidator.extract_actual_url(link)
        if URLValidator.is_valid_url(actual_url):
            final_url = actual_url
        else:
            final_url = link
        
        # สร้าง summary
        enhanced_summary = TextCleaner.extract_meaningful_summary(summary)
        
        return {
            "title": title[:120],
            "url": final_url,
            "original_url": link,
            "summary": enhanced_summary[:200],
            "published_dt": published_dt,
            "feed": feed_name,
            "section": feed_type,
            "has_valid_url": URLValidator.is_valid_url(final_url),
        }

# =============================================================================
# SIMPLE LLM ANALYZER
# =============================================================================
class SimpleLLMAnalyzer:
    """LLM Analyzer แบบง่ายๆ"""
    
    def __init__(self, api_key: str, model: str, endpoint: str):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
    
    def analyze_news(self, title: str, summary: str) -> dict:
        """วิเคราะห์ข่าวด้วย LLM"""
        if not self.api_key:
            return self._get_default_analysis()
        
        # ตรวจสอบว่าเนื้อหามีความหมายหรือไม่
        combined_text = f"{title} {summary}"
        if not TextCleaner.clean_text(combined_text):
            return self._get_default_analysis()
        
        # สร้าง prompt ง่ายๆ
        system_prompt = """คุณเป็นผู้ช่วยสรุปข่าวพลังงาน จงตอบเป็น JSON:
        {
            "summary_th": "สรุปภาษาไทย",
            "is_official": true/false,
            "country": "ประเทศ (ต้องเป็นหนึ่งใน: Thailand, Myanmar, Malaysia, Vietnam, Indonesia, Kazakhstan, Oman, UAE เท่านั้น)"
        }"""
        
        user_prompt = f"""ข่าว: {title}
        เนื้อหา: {summary}
        สรุปเป็นภาษาไทย:"""
        
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
                timeout=20
            )
            
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                
                # พยายามแยก JSON
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    try:
                        analysis = json.loads(json_match.group())
                        # ตรวจสอบว่า country อยู่ในประเทศที่เราต้องการหรือไม่
                        country = str(analysis.get("country", "")).strip()
                        if country not in PROJECTS_BY_COUNTRY:
                            country = ""
                        
                        return {
                            "summary_th": TextCleaner.clean_text(str(analysis.get("summary_th", "")))[:150],
                            "is_official": bool(analysis.get("is_official", False)),
                            "country": country,
                        }
                    except:
                        pass
                
                # ถ้าไม่ใช่ JSON ให้ใช้เนื้อหาเป็น summary
                return {
                    "summary_th": TextCleaner.clean_text(content)[:150],
                    "is_official": False,
                    "country": "",
                }
            else:
                print(f"[LLM] HTTP Error {response.status_code}")
                
        except Exception as e:
            print(f"[LLM] Error: {str(e)}")
        
        return self._get_default_analysis()
    
    def _get_default_analysis(self):
        """Default analysis"""
        return {
            "summary_th": "",
            "is_official": False,
            "country": "",
        }

# =============================================================================
# NEWS FILTER
# =============================================================================
class NewsFilter:
    """คลาสสำหรับกรองข่าวโดยเฉพาะ"""
    
    @staticmethod
    def filter_by_target_countries(news_items: list) -> list:
        """กรองข่าวเฉพาะประเทศที่มีโครงการ"""
        filtered_news = []
        
        for item in news_items:
            country = item.get('country', '')
            
            # ตรวจสอบว่าเป็นประเทศที่มีโครงการ
            if country in PROJECTS_BY_COUNTRY:
                filtered_news.append(item)
            else:
                print(f"[FILTER] ข้ามข่าว: {item.get('title', '')[:50]}... ประเทศ: {country}")
        
        return filtered_news

# =============================================================================
# MAIN NEWS PROCESSOR
# =============================================================================
class NewsProcessor:
    def __init__(self):
        self.sent_links = self.read_sent_links()
        self.llm_analyzer = None
        if USE_LLM_SUMMARY and GROQ_API_KEY:
            self.llm_analyzer = SimpleLLMAnalyzer(GROQ_API_KEY, GROQ_MODEL, GROQ_ENDPOINT)
        self.rss_parser = SimpleRSSParser()
    
    def read_sent_links(self):
        """อ่านลิงก์ที่ส่งแล้ว"""
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
    
    def append_sent_link(self, url: str):
        """บันทึกลิงก์ที่ส่งแล้ว"""
        url = self.normalize_url(url)
        if not url:
            return
        fn = os.path.join(SENT_DIR, self.now_tz().strftime("%Y-%m-%d") + ".txt")
        with open(fn, "a", encoding="utf-8") as f:
            f.write(url + "\n")
    
    def normalize_url(self, url: str) -> str:
        """ทำให้ URL เป็นมาตรฐาน"""
        url = (url or "").strip()
        if not url:
            return url
        try:
            u = urlparse(url)
            return u._replace(fragment="").geturl()
        except Exception:
            return url
    
    def now_tz(self) -> datetime:
        return datetime.now(TZ)
    
    def in_time_window(self, published_dt: datetime, hours: int) -> bool:
        if not published_dt:
            return False
        return published_dt >= (self.now_tz() - timedelta(hours=hours))
    
    def fetch_and_filter_news(self):
        """ดึงและกรองข่าว"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name}...")
            
            try:
                entries = self.rss_parser.fetch_feed(feed_name, feed_url)
                processed_count = 0
                
                for entry in entries[:MAX_PER_FEED]:
                    news_item = self._process_entry(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        processed_count += 1
                
                print(f"  Processed: {processed_count} items")
                        
            except Exception as e:
                print(f"  Error in {feed_name}: {str(e)}")
        
        # กรองข่าวที่ไม่มี URL ที่ใช้งานได้
        all_news = [item for item in all_news if item.get('has_valid_url', False)]
        
        # เรียงลำดับ
        all_news.sort(key=lambda x: (
            -x.get('is_official', 0),
            -(x.get('published_dt') or datetime.min).timestamp()
        ))
        
        return all_news[:MAX_MESSAGES_PER_RUN * BUBBLES_PER_CAROUSEL]
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """ประมวลผลแต่ละข่าว - กรองเฉพาะประเทศที่มีโครงการ"""
        item = self.rss_parser.parse_entry(entry, feed_name, feed_type)
        if not item:
            return None
        
        # ตรวจสอบว่าเคยส่งแล้วหรือไม่
        normalized_url = self.normalize_url(item["url"])
        if normalized_url in self.sent_links:
            return None
        
        # ตรวจสอบเวลา
        if item["published_dt"] and not self.in_time_window(item["published_dt"], WINDOW_HOURS):
            return None
        
        # ตรวจสอบว่าเป็นข่าวพลังงานหรือไม่
        full_text = f"{item['title']} {item['summary']}"
        if not KeywordFilter.is_energy_related(full_text):
            return None
        
        # ตรวจจับประเทศ (เฉพาะประเทศที่มีโครงการ)
        country = CountryDetector.detect_country(full_text)
        if not country:  # ถ้าไม่ใช่ประเทศที่มีโครงการ ให้ข้าม
            return None
        
        # ตรวจสอบว่าเป็นข่าวทางการ
        is_official = (
            KeywordFilter.contains_official_keywords(full_text) or
            feed_type in ['thai_official', 'energy_policy']
        )
        
        # ดึงโครงการที่เกี่ยวข้อง
        project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
        
        # ใช้ LLM (ถ้ามี)
        llm_analysis = None
        if self.llm_analyzer:
            llm_analysis = self.llm_analyzer.analyze_news(item['title'], item['summary'])
            
            # ถ้า LLM ตรวจจับประเทศได้ ให้ใช้ประเทศนั้น (ต้องเป็นประเทศที่มีโครงการ)
            if llm_analysis.get('country') and llm_analysis['country'] in PROJECTS_BY_COUNTRY:
                country = llm_analysis['country']
                project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
            
            if llm_analysis.get('is_official'):
                is_official = True
        
        # สร้างข่าว
        return {
            'title': item['title'],
            'url': item['url'],
            'summary': item['summary'],
            'published_dt': item['published_dt'],
            'country': country,
            'project_hints': project_hints,
            'is_official': is_official,
            'llm_analysis': llm_analysis,
            'feed': feed_name,
            'has_valid_url': item.get('has_valid_url', True)
        }

# =============================================================================
# LINE MESSAGE BUILDER
# =============================================================================
class LineMessageBuilder:
    @staticmethod
    def create_flex_bubble(news_item):
        """สร้าง LINE Flex Bubble"""
        title = LineMessageBuilder.cut_text(news_item.get('title', ''), 100)
        
        # Format timestamp
        pub_dt = news_item.get('published_dt')
        time_str = pub_dt.strftime("%d/%m/%Y %H:%M") if pub_dt else ""
        
        # กำหนดสีและแบดจ์
        if news_item.get('is_official'):
            color = "#4CAF50"
            badge = "📢 ข่าวทางการ"
        elif news_item.get('llm_analysis'):
            color = "#2196F3"
            badge = "🤖 วิเคราะห์ด้วย AI"
        else:
            color = "#FF9800"
            badge = "📰 ข่าวพลังงาน"
        
        # สร้างเนื้อหา
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
        
        # เพิ่ม metadata
        metadata = []
        if time_str:
            metadata.append(time_str)
        
        if metadata:
            contents.append({
                "type": "text",
                "text": " | ".join(metadata),
                "size": "xs",
                "color": "#888888",
                "margin": "sm"
            })
        
        # เพิ่มประเทศ
        contents.append({
            "type": "text",
            "text": f"ประเทศ: {news_item.get('country', 'N/A')}",
            "size": "sm",
            "margin": "xs"
        })
        
        # เพิ่มโครงการที่เกี่ยวข้อง
        if news_item.get('project_hints'):
            hints_text = ", ".join(news_item['project_hints'][:2])
            contents.append({
                "type": "text",
                "text": f"โครงการ: {hints_text}",
                "size": "sm",
                "color": "#2E7D32",
                "wrap": True,
                "margin": "xs"
            })
        
        # เพิ่มสรุป
        if news_item.get('llm_analysis') and news_item['llm_analysis'].get('summary_th'):
            contents.append({
                "type": "text",
                "text": news_item['llm_analysis']['summary_th'],
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#424242"
            })
        elif news_item.get('summary'):
            contents.append({
                "type": "text",
                "text": news_item['summary'],
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#666666"
            })
        
        # เพิ่มแบดจ์
        contents.append({
            "type": "text",
            "text": badge,
            "size": "xs",
            "color": color,
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
                "paddingAll": "12px"
            }
        }
        
        # เพิ่มปุ่มอ่านข่าว
        url = news_item.get('url')
        if url and URLValidator.is_valid_url(url) and len(url) <= 1000:
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
    def cut_text(s: str, n: int) -> str:
        s = (s or "").strip()
        return s if len(s) <= n else s[: n - 1].rstrip() + "…"
    
    @staticmethod
    def create_carousel_message(news_items):
        """สร้าง carousel message"""
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
                has_button = 'footer' in bubble
                print(f"{i+1}. {title[:60]}... {country} {'[มีลิงก์]' if has_button else '[ไม่มีลิงก์]'}")
            
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
    print("ระบบติดตามข่าวพลังงาน - Google News RSS Version")
    print("="*60)
    
    # Configuration check
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    print(f"\n[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No'}")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] Feeds: {len(FEEDS)} Google News RSS sources")
    
    # Initialize CountryDetector patterns
    CountryDetector.initialize_patterns()
    target_countries = list(PROJECTS_BY_COUNTRY.keys())
    print(f"[CONFIG] Target countries: {', '.join(target_countries)}")
    
    # Initialize components
    processor = NewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # Step 1: Fetch and filter news
    print("\n[1] กำลังดึงและกรองข่าว...")
    news_items = processor.fetch_and_filter_news()
    
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่ที่เกี่ยวข้อง")
        return
    
    print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
    
    # Step 3: กรองเฉพาะประเทศที่มีโครงการ
    print("\n[3] กำลังกรองข่าวเฉพาะประเทศที่มีโครงการ...")
    news_items = NewsFilter.filter_by_target_countries(news_items)
    
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่จากประเทศที่มีโครงการ")
        return
    
    print(f"\n[4] พบข่าวจากประเทศที่มีโครงการทั้งหมด {len(news_items)} ข่าว")
    
    # สถิติ
    valid_url_count = sum(1 for item in news_items if item.get('has_valid_url', False))
    official_count = sum(1 for item in news_items if item.get('is_official'))
    llm_count = sum(1 for item in news_items if item.get('llm_analysis'))
    
    # สถิติตามประเทศ
    country_stats = {}
    for item in news_items:
        country = item.get('country', 'Unknown')
        country_stats[country] = country_stats.get(country, 0) + 1
    
    print(f"   - ข่าวที่มีลิงก์อ่านต่อ: {valid_url_count} ข่าว")
    print(f"   - ข่าวทางการ: {official_count} ข่าว")
    print(f"   - วิเคราะห์ด้วย AI: {llm_count} ข่าว")
    print(f"   - ประเทศที่พบ: {', '.join([f'{k} ({v})' for k, v in country_stats.items()])}")
    
    # Step 5: Create LINE message
    print("\n[5] กำลังสร้างข้อความ LINE...")
    line_message = LineMessageBuilder.create_carousel_message(news_items)
    
    if not line_message:
        print("[ERROR] ไม่สามารถสร้างข้อความได้")
        return
    
    # Step 6: Send message
    print("\n[6] กำลังส่งข้อความ...")
    success = line_sender.send_message(line_message)
    
    # Step 7: Mark as sent if successful
    if success and not DRY_RUN:
        for item in news_items:
            if item.get('has_valid_url', False):
                processor.append_sent_link(item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    print("\n" + "="*60)
    print("ดำเนินการเสร็จสิ้น")
    print("="*60)

if __name__ == "__main__":
    main()
