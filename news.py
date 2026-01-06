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

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "72"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "20"))  # ลดลงเพื่อคุณภาพ
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]
MAX_MESSAGES_PER_RUN = int(os.getenv("MAX_MESSAGES_PER_RUN", "10"))
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))

# Sent links tracking
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

# =============================================================================
# URL VALIDATOR - เพิ่มคลาสตรวจสอบลิงก์
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
            if len(result.netloc) < 3:  # เช่น a.co ควรมีอย่างน้อย 3 ตัว
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
    
    @staticmethod
    def shorten_url_if_needed(url: str) -> str:
        """ย่อ URL ถ้ายาวเกินไป"""
        if not url:
            return ""
        
        # ถ้ายาวเกิน 800 ตัวอักษร ให้พยายามย่อ
        if len(url) > 800:
            try:
                # พยายามดึงเฉพาะ path ที่สำคัญ
                parsed = urlparse(url)
                
                # ถ้าเป็น Google News ให้ใช้วิธี extract
                if "news.google.com" in parsed.netloc:
                    actual_url = URLValidator.extract_actual_url(url)
                    if actual_url and len(actual_url) < len(url):
                        return actual_url
                
                # ลดความยาวของ query parameters
                if parsed.query:
                    # เก็บเฉพาะพารามิเตอร์ที่สำคัญ
                    params = parse_qs(parsed.query)
                    important_params = {}
                    
                    for key in ['id', 'p', 'article', 'story', 'url']:
                        if key in params:
                            important_params[key] = params[key][0]
                    
                    if important_params:
                        # สร้าง query string ใหม่
                        new_query = '&'.join([f"{k}={v}" for k, v in important_params.items()])
                        new_url = parsed._replace(query=new_query, fragment="").geturl()
                        
                        if len(new_url) < len(url):
                            return new_url
            except:
                pass
        
        return url

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
        "Click here to read more", "Read full story", "Continue reading"
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
    # ... ประเทศอื่นๆ (เหมือนเดิม)
}

# =============================================================================
# ENHANCED RSS FEEDS - เพิ่มแหล่งข่าวที่มีลิงก์คุณภาพ
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    """Generate Google News RSS URL"""
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

# เลือกเฉพาะแหล่งข่าวที่มีคุณภาพและมีลิงก์ที่ใช้งานได้
QUALITY_FEEDS = [
    # ==================== แหล่งข่าวภาษาไทยคุณภาพสูง ====================
    ("BangkokBizNews_Energy", "thai_business", 
     "https://www.bangkokbiznews.com/tag/พลังงาน/rss"),
    
    ("PostToday_Energy", "thai_business",
     "https://www.posttoday.com/rss/src/พลังงาน"),
    
    ("Prachachat_Energy", "thai_business",
     "https://www.prachachat.net/feed/tag/พลังงาน"),
    
    ("Thansettakij_Energy", "thai_business",
     "https://www.thansettakij.com/rss/tag/พลังงาน"),
    
    ("Manager_Energy", "thai_business",
     "https://mgronline.com/rss/rssfeeds/พลังงาน.aspx"),
    
    # ==================== แหล่งข่าวภาษาอังกฤษคุณภาพสูง ====================
    ("Reuters_Energy", "international",
     "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best&sector=energy-environment"),
    
    ("Bloomberg_Energy", "international",
     "https://www.bloomberg.com/energy/feed"),
    
    ("OilPrice_Top", "energy_international",
     "https://oilprice.com/feed/op-top-stories.xml"),
    
    ("S&P_Global_Energy", "energy_international",
     "https://www.spglobal.com/_assets/platts/rss-feed/platts-oil.xml"),
    
    # ==================== แหล่งข่าวทางการ ====================
    ("กรมธุรกิจพลังงาน", "thai_official",
     "https://www.doeb.go.th/2014/th/rss"),
    
    ("EGAT_News", "thai_official",
     "https://www.egat.co.th/home/rss-news/"),
]

# =============================================================================
# ENHANCED RSS PARSER
# =============================================================================
class EnhancedRSSParser:
    """Parser RSS ที่ดีขึ้น"""
    
    @staticmethod
    def fetch_feed_with_fallback(feed_name: str, feed_url: str):
        """ดึงข้อมูล RSS พร้อม fallback หาก URL ไม่ทำงาน"""
        try:
            print(f"[RSS] Fetching {feed_name}...")
            
            # ตั้งค่า headers เพื่อป้องกันการบล็อก
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml',
                'Accept-Language': 'en-US,en;q=0.9,th;q=0.8',
            }
            
            # ดึงข้อมูล RSS
            response = requests.get(feed_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            # Parse RSS
            feed = feedparser.parse(response.content)
            
            if feed.bozo:  # มีปัญหาในการ parse
                print(f"[RSS WARNING] {feed_name}: Parse issues")
            
            entries = feed.entries or []
            print(f"[RSS] {feed_name}: Found {len(entries)} entries")
            return entries
            
        except requests.exceptions.Timeout:
            print(f"[RSS ERROR] {feed_name}: Timeout")
            return []
        except requests.exceptions.RequestException as e:
            print(f"[RSS ERROR] {feed_name}: {str(e)}")
            # ลองใช้ Google News RSS แทน
            return EnhancedRSSParser._fallback_to_google_news(feed_name)
        except Exception as e:
            print(f"[RSS ERROR] {feed_name}: Unexpected error - {str(e)}")
            return []
    
    @staticmethod
    def _fallback_to_google_news(feed_name: str):
        """Fallback ไปใช้ Google News RSS"""
        google_news_map = {
            "BangkokBizNews_Energy": gnews_rss("พลังงาน site:bangkokbiznews.com", hl="th", gl="TH"),
            "PostToday_Energy": gnews_rss("พลังงาน site:posttoday.com", hl="th", gl="TH"),
            "Reuters_Energy": gnews_rss("energy OR oil OR gas site:reuters.com", hl="en", gl="US"),
            "Bloomberg_Energy": gnews_rss("energy OR oil OR gas site:bloomberg.com", hl="en", gl="US"),
        }
        
        if feed_name in google_news_map:
            print(f"[RSS] Using Google News fallback for {feed_name}")
            try:
                feed = feedparser.parse(google_news_map[feed_name])
                return feed.entries or []
            except:
                return []
        
        return []
    
    @staticmethod
    def parse_entry_with_enhancement(entry, feed_name: str, feed_type: str):
        """Parse entry พร้อมเพิ่มคุณภาพ"""
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
        
        # แก้ไขและตรวจสอบ URL
        original_url = link
        actual_url = URLValidator.extract_actual_url(link)
        
        # ถ้าได้ URL จริง ให้ใช้มัน
        if URLValidator.is_valid_url(actual_url):
            final_url = actual_url
        elif URLValidator.is_valid_url(original_url):
            final_url = original_url
        else:
            # ถ้าไม่มี URL ที่ใช้งานได้ ให้พยายามสร้างจาก feed
            final_url = EnhancedRSSParser._generate_fallback_url(feed_name, entry)
        
        # ถ้าไม่มี URL เลย ให้ข้ามข่าวนี้
        if not final_url or not URLValidator.is_valid_url(final_url):
            print(f"[RSS] Skipping {title[:30]}... - No valid URL")
            return None
        
        # ย่อ URL ถ้าจำเป็น
        final_url = URLValidator.shorten_url_if_needed(final_url)
        
        # สร้าง summary ที่ดีขึ้น
        enhanced_summary = TextCleaner.extract_meaningful_summary(summary)
        if not enhanced_summary and hasattr(entry, 'content'):
            # ลองดึงจาก content
            content_text = ""
            for content in entry.get('content', []):
                if hasattr(content, 'value'):
                    content_text += content.value + " "
            enhanced_summary = TextCleaner.extract_meaningful_summary(content_text)
        
        return {
            "title": title[:120],
            "url": final_url,
            "original_url": original_url,
            "summary": enhanced_summary[:250],
            "published_dt": published_dt,
            "feed": feed_name,
            "section": feed_type,
            "has_valid_url": URLValidator.is_valid_url(final_url),
            "url_length": len(final_url),
        }
    
    @staticmethod
    def _generate_fallback_url(feed_name: str, entry):
        """สร้าง URL fallback จากข้อมูลที่มี"""
        # สำหรับบาง feed ที่มี guid ที่เป็น URL
        guid = getattr(entry, "guid", "")
        if guid and URLValidator.is_valid_url(guid):
            return guid
        
        # สำหรับบาง feed ที่มี link ใน content
        if hasattr(entry, 'links'):
            for link in entry.links:
                if hasattr(link, 'href') and URLValidator.is_valid_url(link.href):
                    return link.href
        
        # สำหรับบาง feed ที่มี ID ที่สามารถสร้าง URL ได้
        if hasattr(entry, 'id'):
            entry_id = entry.id
            feed_url_map = {
                "BangkokBizNews_Energy": f"https://www.bangkokbiznews.com/news/{entry_id}",
                "PostToday_Energy": f"https://www.posttoday.com/{entry_id}",
            }
            
            if feed_name in feed_url_map:
                return feed_url_map[feed_name]
        
        return ""

# =============================================================================
# MAIN NEWS PROCESSOR (ปรับปรุง)
# =============================================================================
class EnhancedNewsProcessor:
    def __init__(self):
        self.sent_links = read_sent_links()
        self.llm_analyzer = None  # จะสร้างเมื่อต้องการ
        self.rss_parser = EnhancedRSSParser()
    
    def fetch_and_filter_news(self):
        """ดึงและกรองข่าวจาก feeds"""
        all_news = []
        
        for feed_name, feed_type, feed_url in QUALITY_FEEDS:
            print(f"\n[Fetching] {feed_name}...")
            
            try:
                entries = self.rss_parser.fetch_feed_with_fallback(feed_name, feed_url)
                processed_count = 0
                
                for entry in entries[:MAX_PER_FEED]:
                    news_item = self._process_entry(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        processed_count += 1
                        
                        if processed_count <= 3:  # แสดง 3 ข่าวแรก
                            print(f"  ✓ {news_item['title'][:50]}...")
                
                print(f"  Total processed: {processed_count} items")
                        
            except Exception as e:
                print(f"  ✗ Error in {feed_name}: {str(e)}")
        
        # กรองข่าวที่ไม่มี URL ที่ใช้งานได้
        all_news = [item for item in all_news if item.get('has_valid_url', False)]
        
        # Sort by importance
        all_news.sort(key=lambda x: (
            -x.get('is_official', 0),
            -(x.get('published_dt') or datetime.min).timestamp()
        ))
        
        return all_news
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """ประมวลผลแต่ละข่าว"""
        item = self.rss_parser.parse_entry_with_enhancement(entry, feed_name, feed_type)
        if not item:
            return None
        
        # ตรวจสอบว่าเคยส่งแล้วหรือไม่
        if item["url"] in self.sent_links:
            return None
        
        # ตรวจสอบเวลา
        if item["published_dt"] and not in_time_window(item["published_dt"], WINDOW_HOURS):
            return None
        
        # ตรวจสอบว่าเป็นข่าวพลังงานหรือไม่
        full_text = f"{item['title']} {item['summary']}".lower()
        energy_keywords = [
            'พลังงาน', 'ไฟฟ้า', 'ค่าไฟ', 'ก๊าซ', 'lng', 'น้ำมัน',
            'energy', 'electricity', 'power', 'gas', 'oil',
            'โรงไฟฟ้า', 'power plant', 'พลังงานทดแทน', 'renewable'
        ]
        
        if not any(keyword in full_text for keyword in energy_keywords):
            return None
        
        # ตรวจจับประเทศ
        country = self._detect_country(full_text, feed_name)
        if not country:
            country = "Thailand" if feed_type in ['thai_business', 'thai_official'] else "International"
        
        # ตรวจสอบว่าเป็นข่าวทางการ
        is_official = self._is_official_news(item, feed_type)
        
        # ดึงโครงการที่เกี่ยวข้อง
        project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
        
        # ใช้ LLM ถ้าต้องการ
        llm_analysis = None
        if USE_LLM_SUMMARY and GROQ_API_KEY:
            if not self.llm_analyzer:
                from .llm_analyzer import LLMAnalyzer  # Import when needed
                self.llm_analyzer = LLMAnalyzer(GROQ_API_KEY, GROQ_MODEL, GROQ_ENDPOINT)
            
            if self.llm_analyzer:
                llm_analysis = self.llm_analyzer.analyze_news(item['title'], item['summary'])
        
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
            'feed_type': feed_type,
            'has_valid_url': item.get('has_valid_url', True)
        }
    
    def _detect_country(self, text: str, feed_name: str) -> str:
        """ตรวจจับประเทศ"""
        text_lower = text.lower()
        
        country_patterns = {
            "Thailand": ['ไทย', 'ประเทศไทย', 'thailand', 'bangkok'],
            "Vietnam": ['เวียดนาม', 'vietnam', 'hanoi'],
            "Malaysia": ['มาเลเซีย', 'malaysia', 'kuala lumpur'],
            "Indonesia": ['อินโดนีเซีย', 'indonesia', 'jakarta'],
            "Myanmar": ['เมียนมา', 'myanmar', 'yangon'],
        }
        
        for country, patterns in country_patterns.items():
            if any(pattern in text_lower for pattern in patterns):
                return country
        
        # ถ้าไม่เจอจากเนื้อหา ให้ดูจาก feed name
        for country in country_patterns.keys():
            if country.lower() in feed_name.lower():
                return country
        
        return ""
    
    def _is_official_news(self, item, feed_type: str) -> bool:
        """ตรวจสอบว่าเป็นข่าวทางการ"""
        # ตรวจสอบจาก feed type
        if feed_type == 'thai_official':
            return True
        
        # ตรวจสอบจาก URL
        url = item.get('url', '')
        official_domains = ['.go.th', '.gov', 'egat.co.th', 'doeb.go.th']
        if any(domain in url for domain in official_domains):
            return True
        
        # ตรวจสอบจากเนื้อหา
        text = f"{item['title']} {item['summary']}".lower()
        official_keywords = [
            'กระทรวง', 'กรม', 'คณะกรรมการ', 'ประกาศ', 'ราชกิจจานุเบกษา',
            'minister', 'ministry', 'regulation', 'official'
        ]
        
        return any(keyword in text for keyword in official_keywords)

# =============================================================================
# ENHANCED LINE MESSAGE BUILDER
# =============================================================================
class EnhancedLineMessageBuilder:
    @staticmethod
    def create_flex_bubble(news_item):
        """สร้าง LINE Flex Bubble"""
        title = cut(news_item.get('title', ''), 100)
        
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
        if news_item.get('feed'):
            # ย่อชื่อ feed ถ้ายาวเกิน
            feed_name = news_item['feed']
            if len(feed_name) > 15:
                feed_name = feed_name.split('_')[0]
            metadata.append(feed_name)
        
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
        elif news_item.get('summary'):
            # ถ้าไม่มี LLM summary ให้ใช้ summary ดั้งเดิม
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
        
        # เพิ่มปุ่มอ่านข่าว ถ้ามี URL ที่ถูกต้อง
        url = news_item.get('url')
        if url and URLValidator.is_valid_url(url):
            # ตรวจสอบว่า URL ไม่ยาวเกินไป
            if len(url) <= 1000:
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
            else:
                # ถ้า URL ยาวเกินไป ให้แสดงข้อความแจ้งแทน
                contents.append({
                    "type": "text",
                    "text": "⚠️ ลิงก์ยาวเกินไป ไม่สามารถแสดงได้",
                    "size": "xs",
                    "color": "#F44336",
                    "margin": "sm"
                })
        else:
            # ถ้าไม่มี URL ที่ใช้งานได้
            contents.append({
                "type": "text",
                "text": "ℹ️ ไม่มีลิงก์อ่านต่อ",
                "size": "xs",
                "color": "#9E9E9E",
                "margin": "sm"
            })
        
        return bubble
    
    @staticmethod
    def create_carousel_message(news_items):
        """สร้าง carousel message"""
        if not news_items:
            return None
        
        bubbles = []
        valid_news_count = 0
        
        for item in news_items:
            # ข้ามข่าวที่ไม่มี URL
            if not item.get('has_valid_url', True):
                continue
                
            bubble = EnhancedLineMessageBuilder.create_flex_bubble(item)
            if bubble:
                bubbles.append(bubble)
                valid_news_count += 1
                
                if valid_news_count >= BUBBLES_PER_CAROUSEL:
                    break
        
        if not bubbles:
            return None
        
        # แจ้งจำนวนข่าวที่ไม่มีลิงก์
        no_link_count = len(news_items) - valid_news_count
        if no_link_count > 0:
            print(f"[INFO] Skipped {no_link_count} news items without valid URLs")
        
        return {
            "type": "flex",
            "altText": f"สรุปข่าวพลังงาน {datetime.now(TZ).strftime('%d/%m/%Y')} ({len(bubbles)} ข่าว)",
            "contents": {
                "type": "carousel",
                "contents": bubbles
            }
        }

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
# MAIN FUNCTION
# =============================================================================
def main():
    print("="*60)
    print("ระบบติดตามข่าวพลังงาน - Enhanced with URL Fix")
    print("="*60)
    
    # Configuration check
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    print(f"\n[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No'}")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] Feeds: {len(QUALITY_FEEDS)} quality sources")
    
    # Initialize components
    processor = EnhancedNewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # Step 1: Fetch and filter news
    print("\n[1] กำลังดึงและกรองข่าวจากแหล่งคุณภาพ...")
    news_items = processor.fetch_and_filter_news()
    
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่ที่เกี่ยวข้อง")
        return
    
    print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
    
    # นับข่าวที่มี URL ใช้งานได้
    valid_url_count = sum(1 for item in news_items if item.get('has_valid_url', False))
    print(f"   - ข่าวที่มีลิงก์อ่านต่อ: {valid_url_count} ข่าว")
    print(f"   - ข่าวที่ไม่มัลิงก์: {len(news_items) - valid_url_count} ข่าว")
    
    # Step 3: Create LINE message (เฉพาะข่าวที่มี URL)
    print("\n[3] กำลังสร้างข้อความ LINE (เฉพาะข่าวที่มีลิงก์)...")
    line_message = EnhancedLineMessageBuilder.create_carousel_message(news_items)
    
    if not line_message:
        print("[ERROR] ไม่สามารถสร้างข้อความได้ (ไม่มีข่าวที่มีลิงก์ที่ใช้งานได้)")
        return
    
    # Step 4: Send message
    print("\n[4] กำลังส่งข้อความ...")
    success = line_sender.send_message(line_message)
    
    # Step 5: Mark as sent if successful
    if success and not DRY_RUN:
        for item in news_items:
            if item.get('has_valid_url', False):
                append_sent_link(item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    print("\n" + "="*60)
    print("ดำเนินการเสร็จสิ้น")
    print("="*60)

# =============================================================================
# LINE SENDER CLASS
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

if __name__ == "__main__":
    main()
