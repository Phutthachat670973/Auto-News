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
USE_LLM_SUMMARY = os.getenv("USE_LLM_SUMMARY", "0").strip().lower() in ["1", "true", "yes", "y"]

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "72"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "50"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))

# ใช้เฉพาะ Energy News Center
USE_ENERGY_NEWS_CENTER_ONLY = os.getenv("USE_ENERGY_NEWS_CENTER_ONLY", "1").strip().lower() in ["1", "true", "yes", "y"]

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
# RSS FEED FOR ENERGY NEWS CENTER
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    """Google News RSS function (เก็บไว้สำหรับใช้ในกรณีจำเป็น)"""
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

FEEDS = [
    # ✅ ใช้เฉพาะ Energy News Center เท่านั้น
    ("EnergyNewsCenter", "direct", "https://www.energynewscenter.com/feed/"),
    
    # ❌ ปิด Google News ชั่วคราว (comment ไว้)
    # ("GoogleNewsTH", "thai", gnews_rss(
    #     '(พลังงาน OR "ค่าไฟ" OR ก๊าซ OR LNG OR น้ำมัน OR ไฟฟ้า OR "โรงไฟฟ้า" OR "พลังงานทดแทน" OR "สัมปทาน") -"รถยนต์" -"ตลาดรถ"',
    #     hl="th", gl="TH", ceid="TH:th"
    # )),
    # ("GoogleNewsEN", "international", gnews_rss(
    #     '(energy OR electricity OR power OR oil OR gas OR "power plant" OR "energy project") AND (Thailand OR Vietnam OR Malaysia OR Indonesia) -car -automotive',
    #     hl="en", gl="US", ceid="US:en"
    # )),
]

def fetch_energynewscenter_feed():
    """ดึงข้อมูลจาก Energy News Center โดยเฉพาะ"""
    print(f"[FEED] ดึงข้อมูลจาก Energy News Center...")
    
    # URLs ที่อาจจะใช้ได้จาก Energy News Center
    possible_urls = [
        "https://www.energynewscenter.com/feed/",
        "https://www.energynewscenter.com/rss/",
        "https://www.energynewscenter.com/feed/rss/",
        "https://www.energynewscenter.com/feed/atom/",
    ]
    
    all_entries = []
    
    for url in possible_urls:
        try:
            print(f"[FEED] ลองดึงจาก: {url}")
            
            # เพิ่ม headers เพื่อป้องกัน blocking
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/rss+xml, application/xml, text/xml, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://www.energynewscenter.com/',
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                d = feedparser.parse(response.content)
                entries = d.entries or []
                
                print(f"[FEED] พบ {len(entries)} entries จาก {url}")
                
                if entries:
                    # แสดงตัวอย่าง
                    for i, entry in enumerate(entries[:3]):
                        print(f"  {i+1}. {entry.title[:80]}...")
                    
                    all_entries.extend(entries)
                    break  # หยุดเมื่อเจอ feed ที่ใช้งานได้
                else:
                    print(f"[FEED] ไม่พบ entries ใน {url}")
            else:
                print(f"[FEED] HTTP {response.status_code} จาก {url}")
                
        except requests.exceptions.Timeout:
            print(f"[FEED] Timeout ในการดึง {url}")
        except Exception as e:
            print(f"[FEED] Error จาก {url}: {str(e)}")
    
    # หากไม่เจอจาก RSS feed URLs โดยตรง ให้ลองใช้ alternative
    if not all_entries:
        print("[FEED] ลองใช้วิธี alternative: Google News RSS สำหรับ energynewscenter.com")
        google_rss_url = gnews_rss(
            'site:energynewscenter.com (energy OR power OR electricity OR gas OR oil)',
            hl="en", gl="US", ceid="US:en"
        )
        try:
            d = feedparser.parse(google_rss_url)
            entries = d.entries or []
            all_entries.extend(entries)
            print(f"[FEED] ได้ {len(entries)} entries จาก Google News RSS")
        except Exception as e:
            print(f"[FEED] Error จาก Google News RSS: {str(e)}")
    
    return all_entries

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

def create_test_news_items():
    """สร้างข่าวตัวอย่างสำหรับการทดสอบ"""
    return [
        {
            'title': 'พลังงานทดแทนในประเทศไทยเติบโตอย่างต่อเนื่อง',
            'url': 'https://www.energynewscenter.com/thai-renewable-energy-growth',
            'canon_url': 'https://www.energynewscenter.com/thai-renewable-energy-growth',
            'source_name': 'Energy News Center',
            'domain': 'energynewscenter.com',
            'summary': 'ประเทศไทยกำลังขยายการใช้พลังงานทดแทน โดยเฉพาะโซลาร์และพลังงานลม เพื่อลดการพึ่งพาก๊าซธรรมชาติ',
            'published_dt': now_tz(),
            'country': 'Thailand',
            'project_hints': ['โครงการจี 1/61', 'โครงการอาทิตย์'],
            'llm_summary': 'ข่าวเกี่ยวกับการเติบโตของพลังงานทดแทนในประเทศไทย',
            'feed': 'EnergyNewsCenter',
            'feed_type': 'direct',
            'simple_summary': 'ประเทศไทยกำลังขยายการใช้พลังงานทดแทนเพื่อลดการพึ่งพาก๊าซธรรมชาติ'
        },
        {
            'title': 'New Solar Power Plant Opens in Vietnam',
            'url': 'https://www.energynewscenter.com/vietnam-solar-plant',
            'canon_url': 'https://www.energynewscenter.com/vietnam-solar-plant',
            'source_name': 'Energy News Center',
            'domain': 'energynewscenter.com',
            'summary': 'A new 100 MW solar power plant has commenced operations in southern Vietnam, contributing to the country\'s renewable energy targets.',
            'published_dt': now_tz() - timedelta(hours=2),
            'country': 'Vietnam',
            'project_hints': ['โครงการเวียดนาม 16-1', 'Block B'],
            'llm_summary': 'เวียดนามเปิดโรงไฟฟ้าพลังงานแสงอาทิตย์ใหม่ขนาด 100 เมกะวัตต์',
            'feed': 'EnergyNewsCenter',
            'feed_type': 'direct',
            'simple_summary': 'เวียดนามเปิดโรงไฟฟ้าพลังงานแสงอาทิตย์ใหม่ขนาด 100 เมกะวัตต์'
        },
        {
            'title': 'ราคาก๊าซธรรมชาติในตลาดโลกมีแนวโน้มลดลง',
            'url': 'https://www.energynewscenter.com/global-gas-price-trend',
            'canon_url': 'https://www.energynewscenter.com/global-gas-price-trend',
            'source_name': 'Energy News Center',
            'domain': 'energynewscenter.com',
            'summary': 'ราคาก๊าซธรรมชาติในตลาดโลกมีแนวโน้มลดลงจากปัจจัยอุปสงค์ที่ลดลงและสต็อกที่เพิ่มขึ้น',
            'published_dt': now_tz() - timedelta(hours=5),
            'country': 'Thailand',
            'project_hints': ['โครงการจี 2/61', 'โครงการสัมปทาน 4'],
            'llm_summary': 'ราคาก๊าซธรรมชาติในตลาดโลกมีแนวโน้มลดลง',
            'feed': 'EnergyNewsCenter',
            'feed_type': 'direct',
            'simple_summary': 'ราคาก๊าซธรรมชาติในตลาดโลกมีแนวโน้มลดลงจากปัจจัยอุปสงค์ที่ลดลง'
        }
    ]

# =============================================================================
# RSS PARSING
# =============================================================================
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

    return {
        "title": title,
        "url": normalize_url(link),
        "canon_url": normalize_url(link),
        "summary": summary,
        "published_dt": published_dt,
        "feed": feed_name,
        "section": section,
    }

# =============================================================================
# LLM ANALYZER
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
            'energynewscenter.com': 'Energy News Center',
            # แหล่งข่าวอื่นๆ (ปิดใช้งานชั่วคราว)
            # 'reuters.com': 'Reuters',
            # 'bloomberg.com': 'Bloomberg',
            # 'bangkokpost.com': 'Bangkok Post',
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
        """Fetch and filter news from Energy News Center only"""
        all_news = []
        
        # ใช้เฉพาะ Energy News Center
        print("\n[Fetching] Energy News Center (เว็บตรง)...")
        
        try:
            entries = fetch_energynewscenter_feed()
            
            if not entries:
                print("[WARNING] ไม่พบข่าวจาก Energy News Center")
                return all_news
            
            print(f"[INFO] พบ entries ทั้งหมด {len(entries)} รายการ")
            
            # ดึงข้อมูลทั้งหมดที่หาได้ (ไม่จำกัดจำนวนมากเกินไป)
            for entry in entries[:MAX_PER_FEED]:  # จำกัดตาม MAX_PER_FEED
                news_item = self._process_entry_energynewscenter(entry)
                if news_item:
                    all_news.append(news_item)
                    print(f"  ✓ {news_item['title'][:60]}...")
                    
        except Exception as e:
            print(f"  ✗ Error: {str(e)}")
            import traceback
            traceback.print_exc()
        
        # Sort by date (ใหม่ที่สุดก่อน)
        all_news.sort(key=lambda x: -((x.get('published_dt') or datetime.min).timestamp()))
        
        print(f"\n[RESULT] ได้ข่าวที่กรองแล้วทั้งหมด {len(all_news)} ข่าว")
        
        return all_news
    
    def _process_entry_energynewscenter(self, entry):
        """Process entry from Energy News Center specifically"""
        try:
            # Parse entry
            title = (getattr(entry, "title", "") or "").strip()
            link = (getattr(entry, "link", "") or "").strip()
            summary = (getattr(entry, "summary", "") or "").strip()
            
            # ใช้ published หรือ updated
            published = getattr(entry, "published", None) or getattr(entry, "updated", None)
            
            # สำหรับเว็บ Energy News Center โดยตรง
            if not published and hasattr(entry, 'published_parsed'):
                try:
                    import time as time_module
                    published = time_module.strftime('%Y-%m-%dT%H:%M:%SZ', entry.published_parsed)
                except:
                    pass
            
            # Parse datetime
            try:
                published_dt = dateutil_parser.parse(published) if published else None
                if published_dt and published_dt.tzinfo is None:
                    published_dt = TZ.localize(published_dt)
                if published_dt:
                    published_dt = published_dt.astimezone(TZ)
            except Exception:
                published_dt = None
            
            # ตรวจสอบ URL
            if not link:
                print(f"  ✗ ข้าม: ไม่มีลิงก์")
                return None
            
            # ตรวจสอบว่าเป็น Energy News Center หรือไม่ (แต่ไม่ strict มาก)
            if "energynewscenter.com" not in link.lower():
                print(f"  ⚠️  ลิงก์ไม่ใช่ energynewscenter.com: {link[:50]}...")
                # ไม่ต้อง reject ทันที อาจเป็นลิงก์อื่นที่เกี่ยวข้อง
            
            # Basic validation
            if not title:
                print(f"  ✗ ข้าม: ไม่มี title")
                return None
            
            # Check if already sent
            canon_url = normalize_url(link)
            if canon_url in self.sent_links:
                print(f"  ✗ ข้าม: ส่งไปแล้ว ({title[:40]}...)")
                return None
            
            # Check time window (เฉพาะข่าวใหม่)
            if published_dt and not in_time_window(published_dt, WINDOW_HOURS):
                print(f"  ✗ ข้าม: ข่าวเก่าเกินไป ({published_dt})")
                return None
            
            # ตรวจสอบเนื้อหาเกี่ยวกับพลังงาน
            full_text = f"{title} {summary}".lower()
            
            # ตรวจสอบคำหลักเกี่ยวกับพลังงาน (สำหรับ Energy News Center อาจไม่จำเป็นเข้มงวด)
            energy_keywords = ['energy', 'power', 'electricity', 'gas', 'oil', 'renewable', 
                              'พลังงาน', 'ไฟฟ้า', 'ก๊าซ', 'น้ำมัน', 'โรงไฟฟ้า', 'พลังงานทดแทน']
            
            is_energy_related = any(keyword in full_text for keyword in energy_keywords)
            
            if not is_energy_related:
                print(f"  ✗ ข้าม: ไม่เกี่ยวข้องกับพลังงาน ({title[:50]}...)")
                return None
            
            # Detect country
            country = KeywordFilter.detect_country(full_text)
            if not country:
                country = "Thailand"  # Default สำหรับ Energy News Center
            
            # LLM analysis (ถ้าเปิดใช้งาน)
            llm_summary = ""
            if USE_LLM_SUMMARY and self.llm_analyzer:
                try:
                    llm_analysis = self.llm_analyzer.analyze_news(title, summary)
                    
                    # ใช้ LLM country ถ้าตรวจพบ
                    if llm_analysis['country'] and llm_analysis['country'] in PROJECTS_BY_COUNTRY:
                        country = llm_analysis['country']
                    
                    # ใช้ summary จาก LLM
                    if llm_analysis.get('summary_th'):
                        llm_summary = llm_analysis['summary_th']
                        
                except Exception as e:
                    print(f"  ⚠️ LLM analysis error: {str(e)}")
            
            # Get project hints for this country
            project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
            
            # ดึงชื่อเว็บข่าว
            source_name = self.get_source_name(link)
            if not source_name:
                source_name = 'Energy News Center'
            
            # สร้าง news item
            return {
                'title': title[:100],
                'url': link,
                'canon_url': canon_url,
                'source_name': source_name,
                'domain': extract_domain(link) or 'energynewscenter.com',
                'summary': summary[:200],
                'published_dt': published_dt,
                'country': country,
                'project_hints': project_hints,
                'llm_summary': llm_summary,
                'feed': 'EnergyNewsCenter',
                'feed_type': 'direct',
                'simple_summary': create_simple_summary(f"{title} {summary}", 100)
            }
            
        except Exception as e:
            print(f"  ✗ Error processing entry: {str(e)}")
            import traceback
            traceback.print_exc()
            return None

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
    print("ระบบติดตามข่าวพลังงาน - Energy News Center เท่านั้น")
    print("="*60)
    
    # Configuration check
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    print(f"\n[CONFIG] โหมด: Energy News Center เท่านั้น")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No (simple summary)'}")
    
    # Initialize components
    processor = NewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # Step 1: Fetch and filter news
    print("\n[1] กำลังดึงข่าวจาก Energy News Center...")
    news_items = processor.fetch_and_filter_news()
    
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่จาก Energy News Center")
        
        # ถ้าเป็น Dry run อาจลองวิธีอื่น
        if DRY_RUN:
            print("\n[DEBUG] สร้างตัวอย่างข่าวสำหรับทดสอบ...")
            news_items = create_test_news_items()
            print(f"[DEBUG] สร้างตัวอย่างข่าวสำเร็จ: {len(news_items)} ข่าว")
        else:
            return
    
    print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
    
    # แสดงรายละเอียดข่าว
    for i, item in enumerate(news_items[:5]):  # แสดงเฉพาะ 5 ข่าวแรก
        pub_time = item['published_dt'].strftime("%H:%M") if item.get('published_dt') else "N/A"
        print(f"  {i+1}. [{pub_time}] {item['title'][:70]}...")
    
    if len(news_items) > 5:
        print(f"  ... และอีก {len(news_items) - 5} ข่าว")
    
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
