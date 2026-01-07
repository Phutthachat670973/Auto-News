# =============================================================================
# ENERGY NEWS & WTI OIL PRICE MONITOR
# =============================================================================
# Version: 2.0 - Includes WTI Oil Price Tracking
# =============================================================================

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
# CONFIGURATION (Hardcoded - For GitHub Actions)
# =============================================================================

# LINE Configuration
LINE_CHANNEL_ACCESS_TOKEN = "YOUR_LINE_CHANNEL_ACCESS_TOKEN"  # ใส่ใน GitHub Secrets

# Groq Configuration (Optional)
GROQ_API_KEY = ""  # ใส่ใน GitHub Secrets ถ้าต้องการใช้
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
USE_LLM_SUMMARY = True

# News Configuration
TZ = pytz.timezone("Asia/Bangkok")
WINDOW_HOURS = 48
MAX_PER_FEED = 30
DRY_RUN = False
BUBBLES_PER_CAROUSEL = 10

# News Sources Filter
ALLOWED_NEWS_SOURCES = ""  # รูปแบบ: "reuters.com,bloomberg.com"
ALLOWED_NEWS_SOURCES_LIST = [s.strip().lower() for s in ALLOWED_NEWS_SOURCES.split(",") if s.strip()] if ALLOWED_NEWS_SOURCES else []

# WTI Oil Price Configuration
WTI_API_KEY = "YOUR_ALPHA_VANTAGE_API_KEY"  # ใส่ใน GitHub Secrets
WTI_ENABLED = True
WTI_SEND_DAILY = True  # ส่งรายวันทุกครั้ง
WTI_SEND_THRESHOLD = 2.0  # เปอร์เซ็นต์เปลี่ยนแปลงที่แจ้งเตือน (ถ้าไม่ใช้ daily mode)

# WTI API Config
WTI_CONFIG = {
    "daily_url": "https://www.alphavantage.co/query",
    "function_daily": "TIME_SERIES_DAILY",
    "function_weekly": "TIME_SERIES_WEEKLY",
    "symbol": "CL=F",  # WTI crude oil futures
    "outputsize": "compact",  # compact (100 days)
    "cache_file": "wti_cache.json",
    "history_file": "wti_history.json",
    "cache_duration_hours": 6
}

# Sent links tracking
SENT_DIR = "sent_links"
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
# WTI OIL PRICE TRACKER
# =============================================================================
class WTITracker:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.cache_file = WTI_CONFIG["cache_file"]
        self.history_file = WTI_CONFIG["history_file"]
    
    def get_daily_price(self):
        """ดึงราคาปัจจุบันของ WTI (รายวัน)"""
        print("[WTI] ดึงข้อมูลราคาปัจจุบัน...")
        
        # ตรวจสอบ cache ก่อน
        cached_data = self._read_cache()
        if cached_data and self._is_cache_valid(cached_data):
            print(f"[WTI] ใช้ข้อมูลจาก cache: {cached_data.get('latest_price', {}).get('price', 0)} USD")
            return cached_data
        
        # ดึงข้อมูลใหม่จาก API
        if not self.api_key:
            print("[WTI] ไม่มี API key สำหรับดึงราคาน้ำมัน")
            return None
        
        try:
            params = {
                "function": WTI_CONFIG["function_daily"],
                "symbol": WTI_CONFIG["symbol"],
                "outputsize": WTI_CONFIG["outputsize"],
                "apikey": self.api_key
            }
            
            print(f"[WTI] เรียก API: {WTI_CONFIG['function_daily']}")
            response = requests.get(
                WTI_CONFIG["daily_url"],
                params=params,
                timeout=15
            )
            
            if response.status_code != 200:
                print(f"[WTI] API Error: {response.status_code}")
                return None
            
            data = response.json()
            
            # ตรวจสอบข้อผิดพลาดจาก API
            if "Error Message" in data:
                print(f"[WTI] API Error: {data['Error Message']}")
                return None
            
            if "Note" in data:  # Rate limit note
                print(f"[WTI] Note: {data['Note']}")
            
            # ตรวจสอบโครงข้อมูล
            if "Time Series (Daily)" not in data:
                print("[WTI] ไม่พบข้อมูลราคารายวันใน response")
                return None
            
            time_series = data["Time Series (Daily)"]
            meta_data = data.get("Meta Data", {})
            
            # ประมวลผลข้อมูล
            processed_data = self._process_daily_data(time_series, meta_data)
            
            if processed_data:
                # บันทึก cache
                self._write_cache(processed_data)
                # บันทึกประวัติ
                self._save_to_history(processed_data)
                
                print(f"[WTI] ดึงข้อมูลสำเร็จ: {len(time_series)} วัน")
                print(f"[WTI] ราคาล่าสุด: {processed_data['latest_price']['price']} USD")
                print(f"[WTI] การเปลี่ยนแปลง: {processed_data['latest_price']['change_percent']:.2f}%")
            
            return processed_data
            
        except requests.exceptions.Timeout:
            print("[WTI] API request timeout")
            return None
        except Exception as e:
            print(f"[WTI] Error fetching price: {str(e)}")
            return None
    
    def _process_daily_data(self, time_series, meta_data):
        """ประมวลผลข้อมูลรายวัน"""
        if not time_series:
            return None
        
        # แปลงวันที่เป็น list และเรียงลำดับ
        dates = sorted(time_series.keys(), reverse=True)
        
        # ข้อมูลล่าสุด
        latest_date = dates[0]
        latest_data = time_series[latest_date]
        
        # ข้อมูลเมื่อวาน (ถ้ามี)
        previous_price = None
        if len(dates) > 1:
            previous_date = dates[1]
            previous_data = time_series[previous_date]
            previous_price = float(previous_data["4. close"])
        
        current_price = float(latest_data["4. close"])
        
        # คำนวณการเปลี่ยนแปลง
        change = 0
        change_percent = 0
        if previous_price:
            change = current_price - previous_price
            change_percent = (change / previous_price) * 100
        
        # สร้างข้อมูล 30 วันล่าสุดสำหรับกราฟ
        monthly_data = []
        for date_str in dates[:30]:  # 30 วันล่าสุด
            day_data = time_series[date_str]
            monthly_data.append({
                "date": date_str,
                "open": float(day_data["1. open"]),
                "high": float(day_data["2. high"]),
                "low": float(day_data["3. low"]),
                "close": float(day_data["4. close"]),
                "volume": int(day_data["5. volume"])
            })
        
        # คำนวณสถิติ
        closes_30d = [d["close"] for d in monthly_data]
        min_30d = min(closes_30d) if closes_30d else 0
        max_30d = max(closes_30d) if closes_30d else 0
        avg_30d = sum(closes_30d) / len(closes_30d) if closes_30d else 0
        
        # สร้างข้อมูล response
        result = {
            "meta": {
                "symbol": meta_data.get("2. Symbol", WTI_CONFIG["symbol"]),
                "last_refreshed": meta_data.get("3. Last Refreshed", latest_date),
                "timezone": meta_data.get("5. Time Zone", "US/Eastern")
            },
            "latest_price": {
                "date": latest_date,
                "price": current_price,
                "change": change,
                "change_percent": change_percent,
                "open": float(latest_data["1. open"]),
                "high": float(latest_data["2. high"]),
                "low": float(latest_data["3. low"]),
                "volume": int(latest_data["5. volume"])
            },
            "monthly_data": monthly_data[:30],
            "statistics": {
                "30d_min": min_30d,
                "30d_max": max_30d,
                "30d_avg": avg_30d,
                "30d_change": ((current_price - closes_30d[-1]) / closes_30d[-1] * 100) if closes_30d else 0
            },
            "timestamp": now_tz().isoformat(),
            "data_points": len(time_series)
        }
        
        return result
    
    def _read_cache(self):
        """อ่านข้อมูลจาก cache"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if "latest_price" in data:
                        return data
        except Exception:
            pass
        return None
    
    def _write_cache(self, data):
        """เขียนข้อมูลลง cache"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def _save_to_history(self, data):
        """บันทึกลงประวัติ"""
        try:
            history = self._read_history()
            
            entry = {
                "timestamp": data["timestamp"],
                "price": data["latest_price"]["price"],
                "change_percent": data["latest_price"]["change_percent"],
                "date": data["latest_price"]["date"]
            }
            
            history.append(entry)
            
            # เก็บเฉพาะ 90 วันล่าสุด
            if len(history) > 90:
                history = history[-90:]
            
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
                
        except Exception:
            pass
    
    def _read_history(self):
        """อ่านประวัติ"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return []
    
    def _is_cache_valid(self, cached_data):
        """ตรวจสอบว่า cache ยังใช้ได้หรือไม่"""
        try:
            cache_time = datetime.fromisoformat(cached_data.get("timestamp", ""))
            time_diff = now_tz() - cache_time
            return time_diff.total_seconds() < (WTI_CONFIG["cache_duration_hours"] * 3600)
        except Exception:
            return False
    
    def format_daily_message(self, price_data):
        """จัดรูปแบบข้อความราคาน้ำมันรายวัน"""
        if not price_data:
            return None
        
        latest = price_data["latest_price"]
        stats = price_data["statistics"]
        
        price = latest["price"]
        change = latest["change"]
        change_percent = latest["change_percent"]
        
        # กำหนดสีและอีโมจิ
        if change > 0:
            emoji = "📈"
            color = "#4CAF50"
            trend = "ขึ้น"
            change_text = f"+{change:.2f} USD (+{change_percent:.2f}%)"
        elif change < 0:
            emoji = "📉"
            color = "#F44336"
            trend = "ลง"
            change_text = f"{change:.2f} USD ({change_percent:.2f}%)"
        else:
            emoji = "➡️"
            color = "#9E9E9E"
            trend = "คงที่"
            change_text = "ไม่เปลี่ยนแปลง"
        
        # สร้างข้อความ
        message_lines = [
            f"{emoji} **ราคาน้ำมันดิบ WTI**",
            "",
            f"💰 **ราคาปัจจุบัน:** {price:.2f} USD/บาร์เรล",
            f"📊 **การเปลี่ยนแปลง:** {change_text}",
            f"📅 **วันที่:** {latest['date']}",
            "",
            "📈 **สถิติ 30 วัน:**",
            f"   สูงสุด: {stats['30d_max']:.2f} USD",
            f"   ต่ำสุด: {stats['30d_min']:.2f} USD",
            f"   เฉลี่ย: {stats['30d_avg']:.2f} USD",
            "",
            f"⏰ อัปเดตล่าสุด: {now_tz().strftime('%d/%m/%Y %H:%M')} น."
        ]
        
        message = "\n".join(message_lines)
        
        return {
            "text": message,
            "color": color,
            "emoji": emoji,
            "trend": trend,
            "raw_data": price_data,
            "is_daily": True
        }
    
    def should_send_alert(self, price_data):
        """ตรวจสอบว่าควรส่งการแจ้งเตือนหรือไม่"""
        if not price_data:
            return False
        
        # ถ้าเปิดโหมดส่งรายวัน ให้ส่งทุกครั้งที่มีข้อมูลใหม่
        if WTI_SEND_DAILY:
            return True
        
        # ตรวจสอบ threshold
        change_percent = abs(price_data["latest_price"]["change_percent"])
        return change_percent >= WTI_SEND_THRESHOLD

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
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""

def is_allowed_source(url: str) -> bool:
    """ตรวจสอบว่า URL นี้มาจากเว็บข่าวที่เราอนุญาตหรือไม่"""
    if not ALLOWED_NEWS_SOURCES_LIST:
        return True
    
    domain = extract_domain(url)
    if not domain:
        return False
    
    for allowed_source in ALLOWED_NEWS_SOURCES_LIST:
        if allowed_source in domain:
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
    """Create a simple summary from text"""
    text = (text or "").strip()
    if not text:
        return ""
    
    text = ' '.join(text.split())
    sentences = re.split(r'[.!?]', text)
    if sentences and len(sentences[0]) > 10:
        summary = sentences[0].strip()
        if len(summary) > max_length:
            summary = summary[:max_length-1] + "…"
        return summary + "."
    
    if len(text) > max_length:
        return text[:max_length-1] + "…"
    return text

# =============================================================================
# FEEDS CONFIGURATION
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
    ("EnergyNewsCenter", "direct", "https://www.energynewscenter.com/feed/"),
    ("EnergyNewsCenter RSS2", "direct", "https://www.energynewscenter.com/rss/"),
    ("EnergyNewsCenter RSS3", "direct", "https://www.energynewscenter.com/feed/rss/"),
]

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
        return entries
    except Exception as e:
        print(f"[FEED] {name}: เกิดข้อผิดพลาด - {str(e)}")
        return []

def parse_entry(e, feed_name: str, section: str):
    title = (getattr(e, "title", "") or "").strip()
    link = (getattr(e, "link", "") or "").strip()
    summary = (getattr(e, "summary", "") or "").strip()
    published = getattr(e, "published", None) or getattr(e, "updated", None)

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
            
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
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
        
        # WTI Tracker
        self.wti_tracker = None
        if WTI_ENABLED and WTI_API_KEY:
            self.wti_tracker = WTITracker(WTI_API_KEY)
            print(f"[WTI] เปิดใช้งานติดตามราคาน้ำมัน")
        else:
            print(f"[WTI] ปิดใช้งานติดตามราคาน้ำมัน")
        
        self.wti_cache_file = "last_wti_sent.json"
        
        # News sources dictionary
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
    
    def get_source_name(self, url: str) -> str:
        """ดึงชื่อเว็บข่าวจาก URL"""
        domain = extract_domain(url)
        if not domain:
            return domain
        
        for source_domain, source_name in self.news_sources.items():
            if source_domain in domain:
                return source_name
        
        return domain
    
    def check_wti_price(self):
        """ตรวจสอบราคาน้ำมัน WTI"""
        if not self.wti_tracker:
            print("[WTI] ปิดใช้งานติดตามราคาน้ำมัน")
            return None
        
        print("\n[WTI] กำลังตรวจสอบราคาน้ำมัน WTI...")
        
        price_data = self.wti_tracker.get_daily_price()
        if not price_data:
            print("[WTI] ไม่สามารถดึงข้อมูลราคาได้")
            return None
        
        should_send = self.wti_tracker.should_send_alert(price_data)
        
        if should_send:
            change_percent = price_data["latest_price"]["change_percent"]
            print(f"[WTI] พบข้อมูลใหม่: {change_percent:.2f}%")
            
            formatted_message = self.wti_tracker.format_daily_message(price_data)
            return formatted_message
        else:
            print(f"[WTI] ไม่ส่งแจ้งเตือน (threshold: {WTI_SEND_THRESHOLD}%)")
            return None
    
    def fetch_and_filter_news(self):
        """Fetch and filter news from all feeds"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name} ({feed_type})...")
            
            try:
                entries = fetch_feed(feed_name, feed_type, feed_url)
                limit = 20 if feed_type == "direct" else MAX_PER_FEED
                
                for entry in entries[:limit]:
                    news_item = self._process_entry(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        print(f"  ✓ {news_item['title'][:50]}...")
                        
            except Exception as e:
                print(f"  ✗ Error: {str(e)}")
        
        all_news.sort(key=lambda x: -((x.get('published_dt') or datetime.min).timestamp()))
        return all_news
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """Process individual news entry"""
        item = parse_entry(entry, feed_name, feed_type)
        
        if not item["title"] or not item["url"]:
            return None
        
        if item["canon_url"] in self.sent_links or item["url"] in self.sent_links:
            return None
        
        if item["published_dt"] and not in_time_window(item["published_dt"], WINDOW_HOURS):
            return None
        
        if feed_type != "direct":
            display_url = item["canon_url"] or item["url"]
            if not is_allowed_source(display_url):
                return None
        
        full_text = f"{item['title']} {item['summary']}"
        
        if not KeywordFilter.is_energy_related(full_text):
            if feed_type != "direct":
                return None
        
        country = KeywordFilter.detect_country(full_text)
        if not country:
            if feed_type == "direct":
                country = "Thailand"
            else:
                return None
        
        llm_summary = ""
        if USE_LLM_SUMMARY and self.llm_analyzer:
            llm_analysis = self.llm_analyzer.analyze_news(item['title'], item['summary'])
            
            if llm_analysis['country'] and llm_analysis['country'] in PROJECTS_BY_COUNTRY:
                country = llm_analysis['country']
            
            if llm_analysis.get('summary_th'):
                llm_summary = llm_analysis['summary_th']
        
        project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
        
        display_url = item["canon_url"] or item["url"]
        source_name = self.get_source_name(display_url)
        
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
        
        pub_dt = news_item.get('published_dt')
        time_str = pub_dt.strftime("%d/%m/%Y %H:%M") if pub_dt else ""
        
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
        
        if news_item.get('source_name'):
            contents.append({
                "type": "text",
                "text": f"📰 {news_item['source_name']}",
                "size": "xs",
                "color": "#666666",
                "margin": "sm"
            })
        elif news_item.get('domain'):
            contents.append({
                "type": "text",
                "text": f"🌐 {cut(news_item['domain'], 30)}",
                "size": "xs",
                "color": "#666666",
                "margin": "sm"
            })
        
        contents.append({
            "type": "text",
            "text": f"ประเทศ: {news_item.get('country', 'N/A')}",
            "size": "sm",
            "margin": "xs",
            "color": "#666666"
        })
        
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
        
        summary_text = ""
        
        if news_item.get('llm_summary'):
            summary_text = news_item['llm_summary']
        elif news_item.get('simple_summary'):
            summary_text = news_item['simple_summary']
        elif news_item.get('summary'):
            summary_text = create_simple_summary(news_item['summary'], 120)
        
        if not summary_text or len(summary_text.strip()) < 10:
            summary_text = f"{news_item.get('title', 'ข่าวพลังงาน')[:60]}..."
        
        if summary_text:
            contents.append({
                "type": "text",
                "text": cut(summary_text, 120),
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#424242"
            })
        
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
    def create_wti_bubble(wti_message):
        """Create a LINE Flex Bubble for WTI oil price"""
        if not wti_message:
            return None
        
        lines = wti_message["text"].split("\n")
        
        contents = []
        
        contents.append({
            "type": "text",
            "text": lines[0],
            "weight": "bold",
            "size": "lg",
            "color": wti_message["color"],
            "margin": "md"
        })
        
        current_section = []
        for line in lines[1:]:
            if line.strip():
                if line.startswith("📈") or line.startswith("⏰"):
                    if current_section:
                        contents.append({
                            "type": "text",
                            "text": "\n".join(current_section),
                            "size": "sm",
                            "margin": "md",
                            "wrap": True
                        })
                        current_section = []
                    
                    current_section.append(line)
                else:
                    current_section.append(line)
        
        if current_section:
            contents.append({
                "type": "text",
                "text": "\n".join(current_section),
                "size": "sm",
                "margin": "md",
                "wrap": True
            })
        
        contents.append({
            "type": "text",
            "text": "📊 ข้อมูลจาก Alpha Vantage API",
            "size": "xs",
            "color": "#666666",
            "margin": "md"
        })
        
        bubble = {
            "type": "bubble",
            "size": "mega",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": contents,
                "paddingAll": "12px"
            },
            "footer": {
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
                            "label": "ดูกราฟราคา",
                            "uri": "https://www.tradingview.com/symbols/CL1!/"
                        }
                    },
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                            "type": "uri",
                            "label": "ข้อมูลเพิ่มเติม",
                            "uri": "https://www.marketwatch.com/investing/future/crude%20oil%20-%20electronic"
                        }
                    }
                ]
            }
        }
        
        return bubble
    
    @staticmethod
    def create_combined_message(news_items, wti_message=None):
        """Create combined message with news and WTI price"""
        bubbles = []
        
        if wti_message:
            wti_bubble = LineMessageBuilder.create_wti_bubble(wti_message)
            if wti_bubble:
                bubbles.append(wti_bubble)
                print(f"[WTI] เพิ่มข้อมูลราคาน้ำมันในข้อความ")
        
        for item in news_items[:BUBBLES_PER_CAROUSEL]:
            bubble = LineMessageBuilder.create_flex_bubble(item)
            if bubble:
                bubbles.append(bubble)
        
        if not bubbles:
            return None
        
        if wti_message and news_items:
            alt_text = f"ข่าวพลังงานและราคาน้ำมัน WTI ({len(bubbles)} รายการ)"
        elif wti_message:
            alt_text = f"ราคาน้ำมัน WTI อัปเดต {datetime.now(TZ).strftime('%d/%m/%Y')}"
        else:
            alt_text = f"สรุปข่าวพลังงาน {datetime.now(TZ).strftime('%d/%m/%Y')} ({len(bubbles)} ข่าว)"
        
        return {
            "type": "flex",
            "altText": alt_text,
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
    print("ระบบติดตามข่าวพลังงานและราคาน้ำมัน WTI")
    print("="*60)
    
    # Configuration check
    if not LINE_CHANNEL_ACCESS_TOKEN or LINE_CHANNEL_ACCESS_TOKEN == "YOUR_LINE_CHANNEL_ACCESS_TOKEN":
        print("[ERROR] กรุณาตั้งค่า LINE_CHANNEL_ACCESS_TOKEN ใน GitHub Secrets")
        print("[ERROR] ไปที่ Repository -> Settings -> Secrets and variables -> Actions")
        print("[ERROR] เพิ่ม Secrets ชื่อ: LINE_CHANNEL_ACCESS_TOKEN")
        return
    
    if WTI_ENABLED and (not WTI_API_KEY or WTI_API_KEY == "YOUR_ALPHA_VANTAGE_API_KEY"):
        print("[WARNING] กรุณาตั้งค่า WTI_API_KEY ใน GitHub Secrets เพื่อติดตามราคาน้ำมัน")
        print("[WARNING] ไปที่ Repository -> Settings -> Secrets and variables -> Actions")
        print("[WARNING] เพิ่ม Secrets ชื่อ: WTI_API_KEY")
        print("[WARNING] ข้ามการติดตามราคาน้ำมัน WTI")
    
    print(f"\n[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No (simple summary)'}")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] WTI Enabled: {'Yes' if WTI_ENABLED else 'No'}")
    print(f"[CONFIG] WTI Daily Send: {'Yes' if WTI_SEND_DAILY else 'No (threshold: ' + str(WTI_SEND_THRESHOLD) + '%)'}")
    print(f"[CONFIG] Allowed news sources: {ALLOWED_NEWS_SOURCES_LIST if ALLOWED_NEWS_SOURCES_LIST else 'All sources'}")
    
    # Initialize components
    processor = NewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # Step 1: Check WTI oil price
    wti_message = None
    if WTI_ENABLED:
        print("\n[1] กำลังตรวจสอบราคาน้ำมัน WTI...")
        wti_message = processor.check_wti_price()
        
        if wti_message:
            price = wti_message['raw_data']['latest_price']['price']
            change = wti_message['raw_data']['latest_price']['change_percent']
            print(f"[WTI] พบข้อมูล: {price:.2f} USD ({change:+.2f}%)")
        else:
            print("[WTI] ไม่มีข้อมูลใหม่ที่ต้องแจ้งเตือน")
    else:
        print("\n[1] ข้ามการตรวจสอบราคาน้ำมัน WTI (ปิดใช้งาน)")
    
    # Step 2: Fetch and filter news
    print("\n[2] กำลังดึงและกรองข่าวพลังงาน...")
    news_items = processor.fetch_and_filter_news()
    
    if not news_items and not wti_message:
        print("\n[INFO] ไม่พบข่าวใหม่หรือข้อมูลราคาน้ำมัน")
        return
    
    # Step 3: Create combined message
    print("\n[3] กำลังสร้างข้อความ LINE...")
    line_message = LineMessageBuilder.create_combined_message(news_items, wti_message)
    
    if not line_message:
        print("[ERROR] ไม่สามารถสร้างข้อความได้")
        return
    
    # แสดงสถิติ
    news_count = len(news_items) if news_items else 0
    has_wti = 1 if wti_message else 0
    total_items = news_count + has_wti
    
    print(f"\n[4] ข้อมูลที่จะส่ง:")
    print(f"   - ข่าวพลังงาน: {news_count} ข่าว")
    print(f"   - ราคาน้ำมัน WTI: {'มี' if wti_message else 'ไม่มี'}")
    print(f"   - รวมทั้งหมด: {total_items} รายการ")
    
    # Step 4: Send message
    print("\n[5] กำลังส่งข้อความ...")
    success = line_sender.send_message(line_message)
    
    # Step 5: Mark as sent if successful
    if success and not DRY_RUN:
        for item in news_items:
            append_sent_link(item.get('canon_url') or item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลสำเร็จ")
    
    print("\n" + "="*60)
    print("ดำเนินการเสร็จสิ้น")
    print("="*60)

# =============================================================================
# GITHUB ACTIONS WORKFLOW TEMPLATE
# =============================================================================
"""
name: Energy News Monitor

on:
  schedule:
    # รันทุกวันเวลา 9:00 และ 17:00 น. (ตามเวลาไทย)
    - cron: '0 2,10 * * *'  # UTC: 02:00 และ 10:00 (ไทย: 09:00 และ 17:00)
  workflow_dispatch:

jobs:
  monitor:
    runs-on: ubuntu-latest
    
    steps:
    - name: Checkout code
      uses: actions/checkout@v3
      
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.10'
        
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        
    - name: Run energy news monitor
      env:
        LINE_CHANNEL_ACCESS_TOKEN: ${{ secrets.LINE_CHANNEL_ACCESS_TOKEN }}
        GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
        WTI_API_KEY: ${{ secrets.WTI_API_KEY }}
        TZ: Asia/Bangkok
      run: |
        python energy_news_monitor.py
        
    - name: Commit cache files
      run: |
        git config --local user.email "action@github.com"
        git config --local user.name "GitHub Action"
        git add wti_cache.json wti_history.json sent_links/*.txt
        git commit -m "Update cache and history [skip ci]" || echo "No changes to commit"
        git push || echo "No changes to push"
"""

if __name__ == "__main__":
    main()
