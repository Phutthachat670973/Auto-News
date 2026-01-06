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
# KEYWORD FILTERS
# =============================================================================
class KeywordFilter:
    # Official sources and keywords
    OFFICIAL_SOURCES = [
        'ratchakitcha.soc.go.th', 'energy.go.th', 'egat.co.th', 
        'pptplc.com', 'pttep.com', 'reuters.com', 'bloomberg.com'
    ]
    
    OFFICIAL_KEYWORDS = [
        'กระทรวงพลังงาน', 'กรมธุรกิจพลังงาน', 'กฟผ', 'การไฟฟ้า',
        'คณะกรรมการกำกับกิจการพลังงาน', 'กกพ', 'สำนักงานนโยบายและแผนพลังงาน',
        'รัฐมนตรีพลังงาน', 'ประกาศ', 'มติคณะรัฐมนตรี', 'ครม.', 'ราชกิจจานุเบกษา',
        'minister', 'ministry', 'regulation', 'policy', 'tariff', 'approval'
    ]
    
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
    
    PROJECT_KEYWORDS = [
        'โครงการ', 'สัมปทาน', 'บล็อก', 'block', 'สัญญา', 'อนุมัติ',
        'ก่อสร้าง', 'ดำเนินการ', 'พัฒนา', 'สำรวจ', 'ขุดเจาะ',
        'project', 'concession', 'contract', 'approval', 'construction'
    ]
    
    @classmethod
    def is_official_source(cls, url: str) -> bool:
        """Check if URL is from official source"""
        domain = urlparse(url).netloc.lower()
        return any(official in domain for official in cls.OFFICIAL_SOURCES)
    
    @classmethod
    def contains_official_keywords(cls, text: str) -> bool:
        """Check if text contains official keywords"""
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in cls.OFFICIAL_KEYWORDS)
    
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
    def contains_project_reference(cls, text: str) -> bool:
        """Check if text contains project references"""
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in cls.PROJECT_KEYWORDS)
    
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
# FEEDS
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
    d = feedparser.parse(url)
    entries = d.entries or []
    print(f"[FEED] {name}: {len(entries)} entries")
    return entries

def parse_entry(e, feed_name: str, section: str):
    title = (getattr(e, "title", "") or "").strip()
    link = (getattr(e, "link", "") or "").strip()
    summary = (getattr(e, "summary", "") or "").strip()
    published = getattr(e, "published", None) or getattr(e, "updated", None)

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
    
    def fetch_and_filter_news(self):
        """Fetch and filter news from all feeds"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name}...")
            
            try:
                entries = fetch_feed(feed_name, feed_type, feed_url)
                
                for entry in entries[:MAX_PER_FEED]:
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
        
        # Combine text for analysis
        full_text = f"{item['title']} {item['summary']}"
        
        # Step 1: Keyword filtering
        if not KeywordFilter.is_energy_related(full_text):
            return None
        
        # Step 2: Detect country
        country = KeywordFilter.detect_country(full_text)
        if not country:
            return None
        
        # Step 3: Check if official (แต่ไม่แสดง badge)
        is_official = (
            KeywordFilter.is_official_source(item['url']) or 
            KeywordFilter.contains_official_keywords(full_text)
        )
        
        # Step 4: Check project references
        has_project_ref = KeywordFilter.contains_project_reference(full_text)
        
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
        
        # Build final news item
        return {
            'title': item['title'][:100],
            'url': item['url'],
            'canon_url': item['canon_url'],
            'summary': item['summary'][:200],
            'published_dt': item['published_dt'],
            'country': country,
            'project_hints': project_hints,
            'is_official': is_official,  # เก็บข้อมูลแต่ไม่แสดง badge
            'has_project_ref': has_project_ref,
            'llm_summary': llm_summary,  # เก็บเฉพาะ summary
            'feed': feed_name,
            'simple_summary': create_simple_summary(full_text, 100)
        }

# =============================================================================
# LINE MESSAGE BUILDER (แบบเรียบง่าย ไม่มี badge)
# =============================================================================
class LineMessageBuilder:
    @staticmethod
    def create_flex_bubble(news_item):
        """Create a LINE Flex Bubble for a news item (แบบเรียบง่าย)"""
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
        
        # Add metadata (เวลาและแหล่งข่าว)
        metadata = []
        if time_str:
            metadata.append(time_str)
        if news_item.get('feed'):
            metadata.append(news_item['feed'])
        
        if metadata:
            contents.append({
                "type": "text",
                "text": " | ".join(metadata),
                "size": "xs",
                "color": "#888888",
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
        
        # ❌ **ไม่เพิ่ม badge อะไรทั้งสิ้น**
        
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
                
                for content in body_contents:
                    if content.get('type') == 'text':
                        text = content.get('text', '')
                        if len(text) > 10 and not title:
                            title = text[:60]
                            break
                
                print(f"{i+1}. {title}")
            
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
    official_count = sum(1 for item in news_items if item.get('is_official'))
    llm_summary_count = sum(1 for item in news_items if item.get('llm_summary'))
    
    print(f"   - ข่าวทางการ: {official_count} ข่าว")
    print(f"   - สรุปด้วย AI: {llm_summary_count} ข่าว")
    
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
