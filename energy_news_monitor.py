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
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))

# สร้างตัวแปรสำหรับเลือกเว็บข่าวที่ต้องการ
# รูปแบบ: "reuters.com,bloomberg.com,bangkokpost.com,thansettakij.com"
ALLOWED_NEWS_SOURCES = os.getenv("ALLOWED_NEWS_SOURCES", "").strip()
if ALLOWED_NEWS_SOURCES:
    ALLOWED_NEWS_SOURCES_LIST = [s.strip().lower() for s in ALLOWED_NEWS_SOURCES.split(",") if s.strip()]
    print(f"[CONFIG] เลือกเฉพาะเว็บข่าว: {ALLOWED_NEWS_SOURCES_LIST}")
else:
    ALLOWED_NEWS_SOURCES_LIST = []
    print("[CONFIG] รับข่าวจากทุกเว็บข่าว")

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
# KEYWORD FILTERS (เรียบง่ายขึ้น)
# =============================================================================
class KeywordFilter:
    # คำหลักที่เกี่ยวข้องกับพลังงาน
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
    
    # คำที่ต้องหลีกเลี่ยง
    EXCLUDE_KEYWORDS = [
        'ตลาดรถยนต์', 'รถยนต์', 'รถ', 'รถใหม่', 'รถยนต์ใหม่',
        'ยานยนต์', 'อุตสาหกรรมยานยนต์',
        'car', 'automotive', 'vehicle', 'automobile'
    ]
    
    @classmethod
    def is_energy_related(cls, text: str) -> bool:
        """Check if text is energy related"""
        text_lower = text.lower()
        
        # ตรวจสอบว่าไม่มีคำที่ต้องหลีกเลี่ยง
        for exclude in cls.EXCLUDE_KEYWORDS:
            if exclude.lower() in text_lower:
                # ถ้ามีคำที่ต้องหลีกเลี่ยง ตรวจสอบว่ามีคำพลังงานร่วมด้วยหรือไม่
                has_energy = any(keyword.lower() in text_lower for keyword in cls.ENERGY_KEYWORDS)
                if not has_energy:
                    return False
        
        # ตรวจสอบว่ามีคำที่เกี่ยวข้องกับพลังงาน
        return any(keyword.lower() in text_lower for keyword in cls.ENERGY_KEYWORDS)
    
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
        '(พลังงาน OR "ค่าไฟ" OR ก๊าซ OR LNG OR น้ำมัน OR ไฟฟ้า OR "โรงไฟฟ้า" OR "พลังงานทดแทน" OR "สัมปทาน") -"รถยนต์" -"ตลาดรถ"',
        hl="th", gl="TH", ceid="TH:th"
    )),
    ("GoogleNewsEN", "international", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas OR "power plant" OR "energy project") AND (Thailand OR Vietnam OR Malaysia OR Indonesia) -car -automotive',
        hl="en", gl="US", ceid="US:en"
    )),
    # ✅ เพิ่ม feed จากเว็บโดยตรง
    ("EnergyNewsCenter", "direct", "https://www.energynewscenter.com/feed/"),
    # ลองเพิ่ม RSS feed URLs อื่นๆ ที่เป็นไปได้
    ("EnergyNewsCenter RSS2", "direct", "https://www.energynewscenter.com/rss/"),
    ("EnergyNewsCenter RSS3", "direct", "https://www.energynewscenter.com/feed/rss/"),
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
def fetch_feed(name: str, section: str, url: str):
    """ดึง RSS feed จาก URL"""
    print(f"[FEED] ดึงข้อมูลจาก {name} ({url})...")
    try:
        d = feedparser.parse(url)
        entries = d.entries or []
        print(f"[FEED] {name}: พบ {len(entries)} entries")
        
        # แสดงตัวอย่าง entries (สำหรับ debug)
        if entries and len(entries) > 0:
            print(f"[FEED] ตัวอย่างข่าวแรก: {entries[0].title[:50]}...")
        
        return entries
    except Exception as e:
        print(f"[FEED] {name}: เกิดข้อผิดพลาด - {str(e)}")
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
# NEWS PROCESSOR
# =============================================================================
class NewsProcessor:
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
            # ✅ เพิ่มแหล่งข่าวพลังงานโดยเฉพาะ
            'energynewscenter.com': 'Energy News Center',  # เพิ่มเว็บตรง
            # เพิ่มแหล่งข่าวอื่นๆ ตามต้องการ
        }
    
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
    
    def fetch_and_filter_news(self):
        """Fetch and filter news from all feeds"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name} ({feed_type})...")
            
            try:
                entries = fetch_feed(feed_name, feed_type, feed_url)
                
                # สำหรับเว็บตรง ไม่ต้องกรอง MAX_PER_FEED มากเกินไป
                limit = 20 if feed_type == "direct" else MAX_PER_FEED
                
                for entry in entries[:limit]:
                    news_item = self._process_entry(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        print(f"  ✓ {news_item['title'][:50]}...")
                        
            except Exception as e:
                print(f"  ✗ Error: {str(e)}")
        
        # Sort by date (ใหม่ที่สุดก่อน)
        all_news.sort(key=lambda x: -((x.get('published_dt') or datetime.min).timestamp()))
        
        return all_news
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """Process individual news entry"""
        item = parse_entry(entry, feed_name, feed_type)
        
        # Basic validation
        if not item["title"] or not item["url"]:
            return None
        
        # Check if already sent
        if item["canon_url"] in self.sent_links or item["url"] in self.sent_links:
            return None
        
        # Check time window
        if item["published_dt"] and not in_time_window(item["published_dt"], WINDOW_HOURS):
            return None
        
        # สำหรับเว็บตรง (direct) ไม่ต้องตรวจสอบ ALLOWED_NEWS_SOURCES
        # เพราะเราต้องการข่าวจากเว็บตรงทุกข่าว
        if feed_type != "direct":
            # ตรวจสอบว่า URL นี้มาจากเว็บข่าวที่อนุญาตหรือไม่
            display_url = item["canon_url"] or item["url"]
            if not is_allowed_source(display_url):
                # print(f"  ✗ ข้ามข่าวจาก {extract_domain(display_url)} (ไม่อยู่ในรายการที่อนุญาต)")
                return None
        
        # Combine text for analysis
        full_text = f"{item['title']} {item['summary']}"
        
        # Step 1: Keyword filtering
        # สำหรับเว็บพลังงานโดยตรง อาจไม่ต้องกรองเข้มงวด
        if not KeywordFilter.is_energy_related(full_text):
            # ถ้าเป็นเว็บพลังงานโดยตรง ให้ผ่อนปรนการกรอง
            if feed_type != "direct":
                return None
        
        # Step 2: Detect country (แต่เว็บพลังงานอาจไม่จำเป็นต้องมีประเทศเสมอ)
        country = KeywordFilter.detect_country(full_text)
        if not country:
            # สำหรับเว็บพลังงานโดยตรง ให้ใช้ Thailand เป็น default
            if feed_type == "direct":
                country = "Thailand"
            else:
                return None
        
        # Step 3: LLM analysis (สำหรับสรุปข่าวเท่านั้น)
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
        return {
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

# =============================================================================
# LINE MESSAGE BUILDER
# =============================================================================
class LineMessageBuilder:
    @staticmethod
    def create_flex_bubble(news_item):
        """Create a LINE Flex Bubble for a news item"""
        title = cut(news_item.get('title', ''), 80)
        
        # Format timestamp
        pub_dt = news_item.get('published_dt')
        time_str = pub_dt.strftime("%d/%m/%Y %H:%M") if pub_dt else ""
        
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
            "color": "#666666"
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
                "paddingAll": "12px"
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
                body_contents = bubble.get('body', {}).get('contents', [])
                title = ""
                source = ""
                
                for content in body_contents:
                    if content.get('type') == 'text':
                        text = content.get('text', '')
                        if len(text) > 10 and not title:
                            title = text[:60]
                        elif '📰' in text or '🌐' in text:
                            source = text
                            break
                
                print(f"{i+1}. {title}")
                if source:
                    print(f"   Source: {source}")
            
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
    print("ระบบติดตามข่าวพลังงาน")
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
    print(f"[CONFIG] Allowed news sources: {ALLOWED_NEWS_SOURCES_LIST if ALLOWED_NEWS_SOURCES_LIST else 'All sources'}")
    print(f"[CONFIG] จำนวน feed ทั้งหมด: {len(FEEDS)}")
    print(f"[CONFIG] Feed รายการ: {[f[0] for f in FEEDS]}")
    
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
    
    # Count statistics
    llm_summary_count = sum(1 for item in news_items if item.get('llm_summary'))
    direct_count = sum(1 for item in news_items if item.get('feed_type') == 'direct')
    
    # นับจำนวนข่าวแยกตามแหล่งข่าว
    source_counts = {}
    for item in news_items:
        source = item.get('source_name') or item.get('domain', 'Unknown')
        source_counts[source] = source_counts.get(source, 0) + 1
    
    print(f"   - สรุปด้วย AI: {llm_summary_count} ข่าว")
    print(f"   - ข่าวจากเว็บตรง: {direct_count} ข่าว")
    print(f"   - แหล่งข่าวที่พบ:")
    for source, count in sorted(source_counts.items()):
        print(f"     • {source}: {count} ข่าว")
    
    # Step 2: Create LINE message
    print("\n[3] กำลังสร้างข้อความ LINE...")
    line_message = LineMessageBuilder.create_carousel_message(news_items)
    
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
