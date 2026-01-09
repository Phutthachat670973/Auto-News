# -*- coding: utf-8 -*-
"""
Enhanced News Aggregator with WTI Futures (EIA API Only)
ระบบรวบรวมข่าวพลังงาน + ราคา WTI Futures จาก EIA.gov
"""

import os
import re
import json
import time
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, unquote
from difflib import SequenceMatcher
from typing import List, Set, Tuple, Optional, Dict

import requests
import feedparser
import pytz
from dateutil import parser as dateutil_parser
from news_examples import get_few_shot_examples
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

# EIA API (Required!)
EIA_API_KEY = os.getenv("EIA_API_KEY", "").strip()
if not EIA_API_KEY:
    raise RuntimeError("Missing EIA_API_KEY - Get one from https://www.eia.gov/opendata/")

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "48"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "30"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))

# Debug mode
DEBUG_FILTERING = os.getenv("DEBUG_FILTERING", "1").strip().lower() in ["1", "true", "yes", "y"]

# Allowed news sources
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
# ENHANCED DEDUPLICATION SYSTEM (FIXED - Less Aggressive)
# =============================================================================
class EnhancedDeduplication:
    """ระบบกันข่าวซ้ำที่ปรับปรุงใหม่ - ลดความเข้มงวด"""
    
    THAI_STOP_WORDS = {
        'ที่', 'ใน', 'จาก', 'เป็น', 'การ', 'และ', 'ของ', 'ได้', 'มี', 'ว่า',
        'กับ', 'โดย', 'ให้', 'แล้ว', 'ไป', 'มา', 'อยู่', 'ยัง', 'คือ', 'ถึง',
        'นี้', 'นั้น', 'ซึ่ง', 'เพื่อ', 'แต่', 'ถ้า', 'จะ', 'ก็', 'ไม่', 'ขึ้น'
    }
    
    ENGLISH_STOP_WORDS = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
        'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'can', 'this', 'that', 'these',
        'those', 'it', 'its'
    }
    
    GROUPING_KEYWORDS = {
        'pttep', 'ปตท.', 'murphy', 'shell', 'chevron', 'exxon', 'total',
        'แบร็ค อิลส์', 'black hills', 'บางจาก', 'irpc', 'top',
        'appraisal', 'discovery', 'drilling', 'exploration', 'production',
        'field', 'block', 'concession', 'สัมปทาน', 'แหล่ง', 'โครงการ',
        'lng', 'terminal', 'pipeline', 'refinery', 'power plant',
        'โรงไฟฟ้า', 'ท่อส่ง', 'คลังน้ำมัน',
        'price', 'market', 'trading', 'ราคา', 'ตลาด',
        'oil', 'gas', 'electricity', 'renewable', 'solar', 'wind',
        'น้ำมัน', 'ก๊าซ', 'ไฟฟ้า', 'พลังงานทดแทน',
        'investment', 'deal', 'agreement', 'contract', 'acquisition',
        'ลงทุน', 'สัญญา', 'ซื้อ', 'ขาย'
    }
    
    def __init__(self):
        self.seen_urls: Set[str] = set()
        self.seen_fingerprints: Set[str] = set()
        self.processed_items: List[dict] = []
        self.title_cache: List[Tuple[str, str]] = []
    
    def normalize_text(self, text: str) -> str:
        """Normalize text สำหรับการเปรียบเทียบ"""
        if not text:
            return ""
        
        text = text.lower()
        text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\d+', '', text)
        text = ' '.join(text.split())
        
        words = text.split()
        filtered_words = [
            w for w in words 
            if w not in self.THAI_STOP_WORDS 
            and w not in self.ENGLISH_STOP_WORDS
            and len(w) > 1
        ]
        
        return ' '.join(filtered_words)
    
    def extract_keywords(self, text: str) -> Set[str]:
        """ดึงคำสำคัญจากข้อความ"""
        text_lower = text.lower()
        found_keywords = set()
        
        for keyword in self.GROUPING_KEYWORDS:
            if keyword in text_lower:
                found_keywords.add(keyword)
        
        return found_keywords
    
    def create_content_fingerprint(self, item: dict) -> str:
        """
        สร้าง fingerprint จากเนื้อหาข่าว
        
        ✅ FIX: ใช้ title ทั้งหมด + ตัดเลขออก เพื่อจับข่าวที่แทบจะเหมือนกัน
        """
        title = self.normalize_text(item.get('title', ''))
        
        # ✅ เพิ่ม: ตัด source และเวลาออก เพื่อจับข่าวที่เนื้อหาเดียวกัน
        # เช่น "9 หุ้นพลังงานกอดคอบวก SPRC-OR น่าคุ้ม 3.91%" จาก 2 แหล่ง
        title_clean = re.sub(r'\s+', ' ', title).strip()
        
        country = item.get('country', '')
        keywords = self.extract_keywords(f"{item.get('title', '')} {item.get('summary', '')}")
        keywords_str = '|'.join(sorted(keywords))
        
        # ใช้ title ที่ทำความสะอาดแล้ว แทนที่จะตัดเหลือ 100 ตัวอักษร
        content = f"{title_clean}|{country}|{keywords_str}"
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def calculate_similarity(self, text1: str, text2: str) -> float:
        """คำนวณความคล้ายคลึงระหว่างข้อความ"""
        norm1 = self.normalize_text(text1)
        norm2 = self.normalize_text(text2)
        
        if not norm1 or not norm2:
            return 0.0
        
        return SequenceMatcher(None, norm1, norm2).ratio()
    
    def is_duplicate_content(self, item: dict) -> Tuple[bool, Optional[str]]:
        """
        ตรวจสอบว่าเนื้อหาข่าวซ้ำหรือไม่ (ปรับลดความเข้มงวด)
        
        FIXED: ลดเกณฑ์การกรอง
        - Title similarity: 0.75 -> 0.90 (ต้องคล้ายกันมากกว่า 90%)
        - Keyword matching: 0.70 -> 0.85 (ต้องตรงกัน 85%+)
        - Time window: 48 -> 24 ชม. (ลดเวลา)
        """
        url = item.get('canon_url') or item.get('url', '')
        if self.is_duplicate_url(url):
            return True, "URL ซ้ำ"
        
        # ✅ FIX: เช็ค Exact Title Match ก่อน (สำหรับกรณีข่าวเหมือนกันทุกอย่าง)
        title = item.get('title', '')
        for existing in self.processed_items:
            existing_title = existing.get('title', '')
            # ถ้า title เหมือนกัน 100% = ซ้ำแน่นอน
            if title == existing_title:
                return True, "Title เหมือนกันทุกตัวอักษร"
            
            # ถ้า title คล้ายกันมากกว่า 95% (แทบจะเหมือนกัน)
            similarity = self.calculate_similarity(title, existing_title)
            if similarity > 0.95:
                return True, f"Title เหมือนกันเกือบทุกคำ ({similarity:.1%})"
        
        fingerprint = self.create_content_fingerprint(item)
        if fingerprint in self.seen_fingerprints:
            return True, "Fingerprint ซ้ำ (เนื้อหาเดียวกัน)"
        self.seen_fingerprints.add(fingerprint)
        
        title = item.get('title', '')
        
        # ✅ FIX 1: ยกเกณฑ์ title similarity จาก 0.75 -> 0.90
        for cached_norm_title, cached_orig_title in self.title_cache:
            similarity = self.calculate_similarity(title, cached_norm_title)
            
            # เปลี่ยนจาก 0.75 เป็น 0.90 (ต้องคล้ายกันมากกว่า 90%)
            if similarity > 0.90:
                return True, f"Title เหมือนกันเกือบทุกคำ ({similarity:.1%})"
            
            # เปลี่ยนจาก 0.65 เป็น 0.80
            if similarity > 0.80:
                for existing in self.processed_items:
                    if existing.get('title') == cached_orig_title:
                        # เช็คประเทศด้วย - ถ้าต่างประเทศก็ไม่ถือว่าซ้ำ
                        if existing.get('country') != item.get('country'):
                            continue
                        return True, f"Title คล้ายกันมาก + ประเทศเดียวกัน ({similarity:.1%})"
        
        # ✅ FIX 2: เพิ่มเกณฑ์ keyword matching จาก 0.70 -> 0.85
        current_keywords = self.extract_keywords(f"{item.get('title', '')} {item.get('summary', '')}")
        if len(current_keywords) >= 3:  # ต้องมีอย่างน้อย 3 keywords
            for existing in self.processed_items:
                existing_keywords = self.extract_keywords(
                    f"{existing.get('title', '')} {existing.get('summary', '')}"
                )
                
                # เปลี่ยนจาก 0.7 เป็น 0.85 (ต้องตรงกัน 85%+)
                common_keywords = current_keywords & existing_keywords
                if len(common_keywords) >= len(current_keywords) * 0.85:
                    title_sim = self.calculate_similarity(item.get('title', ''), existing.get('title', ''))
                    # เปลี่ยนจาก 0.5 เป็น 0.70
                    if title_sim > 0.70:
                        pub_dt1 = item.get('published_dt')
                        pub_dt2 = existing.get('published_dt')
                        if pub_dt1 and pub_dt2:
                            time_diff = abs((pub_dt1 - pub_dt2).total_seconds() / 3600)
                            # เปลี่ยนจาก 48 ชม. เป็น 24 ชม.
                            if time_diff < 24:
                                return True, f"คำสำคัญตรงกัน {len(common_keywords)} คำ + title คล้ายกัน + เวลาใกล้กัน"
        
        # ✅ FIX 3: เพิ่มเกณฑ์สำหรับ specific terms
        specific_terms = self._extract_specific_terms(title)
        if len(specific_terms) >= 2:  # ต้องมีอย่างน้อย 2 คำเฉพาะเจาะจง
            for existing in self.processed_items:
                existing_terms = self._extract_specific_terms(existing.get('title', ''))
                common_terms = specific_terms & existing_terms
                if len(common_terms) >= 2:  # ต้องตรงกันอย่างน้อย 2 คำ
                    title_sim = self.calculate_similarity(title, existing.get('title', ''))
                    if title_sim > 0.75:  # เปลี่ยนจาก 0.5 -> 0.75
                        return True, f"พบคำเฉพาะเจาะจงซ้ำ: {', '.join(common_terms)}"
        
        norm_title = self.normalize_text(title)
        self.title_cache.append((norm_title, title))
        
        return False, None
    
    def is_duplicate_url(self, url: str) -> bool:
        """ตรวจสอบ URL ซ้ำ"""
        normalized = normalize_url(url)
        if normalized in self.seen_urls:
            return True
        self.seen_urls.add(normalized)
        return False
    
    def _extract_specific_terms(self, text: str) -> Set[str]:
        """ดึงคำเฉพาะเจาะจง เช่น ชื่อโครงการ, สถานที่, วันที่"""
        text_lower = text.lower()
        specific_terms = set()
        
        # วันที่ในรูปแบบต่างๆ
        date_patterns = [
            r'\d{1,2}\s*ม\.ค\.',
            r'\d{1,2}\s*ก\.พ\.',
            r'\d{1,2}\s*มี\.ค\.',
            r'\d{1,2}/\d{1,2}/\d{2,4}',
            r'วันนี้',
            r'today'
        ]
        
        for pattern in date_patterns:
            matches = re.findall(pattern, text_lower)
            specific_terms.update(matches)
        
        # ชื่อโครงการเฉพาะ
        project_names = [
            'natuna sea a', 'natuna', 'arthit', 'zawtika', 'yadana',
            'sk309', 'sk311', 'block h', 'block 61', 'dunga',
            'จี 1/61', 'จี 2/61', 'เอส 1', 'บี 6/27',
            'indonesia', 'malaysia', 'vietnam', 'myanmar', 'oman', 'uae',
            'leighton asia', 'cimic', 'aiib', 'pasuruan'
        ]
        
        for project in project_names:
            if project in text_lower:
                specific_terms.add(project)
        
        # ตัวเลขเฉพาะ (ราคา, จำนวนเงิน)
        number_patterns = re.findall(r'\$\d+|\d+\s*(?:บาท|ดอลลาร์|ล้าน|พันล้าน)', text_lower)
        specific_terms.update(number_patterns[:2])  # เอาแค่ 2 ตัวแรก
        
        return specific_terms
        
    def add_item(self, item: dict) -> bool:
        """เพิ่มข่าวเข้าระบบ (ถ้าไม่ซ้ำ)"""
        is_dup, reason = self.is_duplicate_content(item)
        
        if is_dup:
            if DEBUG_FILTERING:
                print(f"  ✗ ข่าวซ้ำ: {reason}")
            return False
        
        self.processed_items.append(item)
        return True


# =============================================================================
# KEYWORD FILTER (FIXED - Less Strict)
# =============================================================================
class KeywordFilter:
    """กรองข่าวตามคำสำคัญ - ปรับให้ผ่อนปรนมากขึ้น"""
    
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
        'energy policy', 'energy project', 'energy investment',
        # ✅ เพิ่มคำที่เกี่ยวข้อง
        'crude', 'petroleum', 'brent', 'wti', 'opec',
        'น้ำมันดิบ', 'ปิโตรเลียม', 'โอเปก'
    ]
    
    ENERGY_MARKET_KEYWORDS = [
        'ราคา', 'ราคาน้ำมัน', 'ราคาก๊าซ', 'ราคาไฟฟ้า', 'ค่าไฟ',
        'ตลาด', 'ตลาดพลังงาน', 'ตลาดน้ำมัน', 'ตลาดก๊าซ',
        'โลก', 'โลกา', 'ต่างประเทศ', 'สหรัฐ', 'เวเนซุเอลา',
        'ร่วง', 'ปรับขึ้น', 'ปรับลด', 'ผันผวน', 'ตก', 'เพิ่ม',
        'ดอลลาร์', 'บาร์เรล', 'ตลาดหุ้น', 'ตลาดโลก',
        'price', 'market', 'global', 'crude', 'brent', 'wti',
        'increase', 'decrease', 'drop', 'rise', 'fall',
        # ✅ เพิ่มคำที่เกี่ยวข้อง
        'trading', 'futures', 'commodity', 'barrel',
        'ซื้อขาย', 'ล่วงหน้า', 'สินค้าโภคภัณฑ์'
    ]
    
    BUSINESS_KEYWORDS = [
        'โครงการ', 'ลงทุน', 'สัญญา', 'สัมปทาน', 'มูลค่า',
        'ล้าน', 'พันล้าน', 'ดอลลาร์', 'บาท', 'เหรียญ',
        'พบ', 'สำรวจ', 'ขุดเจาะ', 'ผลิต', 'ส่งออก', 'นำเข้า',
        'ประกาศ', 'แถลง', 'รายงาน', 'ผลประกอบการ', 'รายได้',
        'ขยาย', 'พัฒนา', 'สร้าง', 'ก่อสร้าง', 'ติดตั้ง',
        'ตลาด', 'ซื้อ', 'ขาย', 'ซื้อขาย', 'ซื้อขายล่วงหน้า',
        'หุ้น', 'ตลาดหุ้น', 'ตลาดหลักทรัพย์', 'ตลาดโลก',
        'เพิ่ม', 'ลด', 'ปรับ', 'เปลี่ยนแปลง', 'วิกฤต', 'โอกาส',
        'project', 'investment', 'contract', 'agreement', 'deal',
        'discovery', 'exploration', 'drilling', 'production', 'export',
        'announce', 'report', 'financial', 'revenue', 'expand',
        'development', 'construction', 'installation',
        'market', 'trading', 'stock', 'exchange', 'global',
        # ✅ เพิ่มคำที่เกี่ยวข้อง
        'growth', 'decline', 'forecast', 'outlook', 'trend',
        'เติบโต', 'ลดลง', 'คาดการณ์', 'แนวโน้ม', 'วิเคราะห์'
    ]
    
    EXCLUDE_KEYWORDS = [
        'ตลาดรถยนต์', 'รถยนต์', 'รถ', 'รถใหม่', 'รถยนต์ใหม่',
        'ยานยนต์', 'อุตสาหกรรมยานยนต์',
        'ดารา', 'ศิลปิน', 'นักแสดง', 'นักร้อง', 'คนดัง',
        'ร่วมบุญ', 'การกุศล', 'จิตอาสา', 'มอบ', 'ให้', 'ช่วยเหลือ',
        'celebrity', 'actor', 'singer', 'donation', 'charity', 'philanthropy',
        'car', 'automotive', 'vehicle', 'automobile'
    ]
    
    @classmethod
    def check_valid_energy_news(cls, text: str) -> tuple:
        """
        ตรวจสอบว่าเป็นข่าวพลังงานที่เกี่ยวข้องกับธุรกิจหรือไม่
        
        FIXED: ลดความเข้มงวด
        - ถ้ามีคำพลังงาน + ราคา/ตลาด = ผ่านทันที (ไม่ต้องมีคำธุรกิจ)
        - ถ้ามีคำพลังงาน + คำธุรกิจ = ผ่าน
        - ถ้ามีแค่คำพลังงาน + ประเทศ = ผ่าน
        """
        text_lower = text.lower()
        reasons = []
        
        # เช็คคำต้องห้ามก่อน
        for exclude in cls.EXCLUDE_KEYWORDS:
            if exclude.lower() in text_lower:
                reasons.append(f"มีคำต้องห้าม: '{exclude}'")
                return False, "ข่าวสังคม", reasons
        
        found_energy_keywords = [kw for kw in cls.ENERGY_KEYWORDS if kw.lower() in text_lower]
        found_market_keywords = [kw for kw in cls.ENERGY_MARKET_KEYWORDS if kw.lower() in text_lower]
        found_business_keywords = [kw for kw in cls.BUSINESS_KEYWORDS if kw.lower() in text_lower]
        
        # ถ้าไม่มีคำพลังงานเลย
        if not found_energy_keywords and not found_market_keywords:
            reasons.append("ไม่มีคำที่เกี่ยวข้องกับพลังงาน")
            return False, "ไม่เกี่ยวข้องกับพลังงาน", reasons
        
        if found_energy_keywords:
            reasons.append(f"พบคำพลังงาน: {', '.join(found_energy_keywords[:3])}")
        if found_market_keywords:
            reasons.append(f"พบคำตลาดพลังงาน: {', '.join(found_market_keywords[:3])}")
        if found_business_keywords:
            reasons.append(f"พบคำธุรกิจ: {', '.join(found_business_keywords[:3])}")
        
        # ✅ FIX: ผ่อนปรนเงื่อนไข
        
        # 1. มีคำพลังงาน + ราคา/ตลาด = ผ่านทันที
        if found_energy_keywords and found_market_keywords:
            reasons.append("เป็นข่าวราคา/ตลาดพลังงาน")
            return True, "ผ่าน", reasons
        
        # 2. มีคำพลังงาน + คำธุรกิจ = ผ่าน
        if found_energy_keywords and found_business_keywords:
            reasons.append("มีคำพลังงาน + คำธุรกิจ")
            return True, "ผ่าน", reasons
        
        # 3. มีคำพลังงาน + ชื่อประเทศ = ผ่าน
        country_keywords = ['thailand', 'vietnam', 'malaysia', 'indonesia', 'myanmar', 
                           'oman', 'uae', 'kazakhstan', 'ไทย', 'เวียดนาม', 'มาเลเซีย', 
                           'อินโดนีเซีย', 'เมียนมา', 'โอมาน', 'ยูเออี', 'คาซัคสถาน']
        if found_energy_keywords and any(country in text_lower for country in country_keywords):
            reasons.append("มีคำพลังงาน + ชื่อประเทศ")
            return True, "ผ่าน", reasons
        
        # 4. มีคำพลังงาน + คำสำคัญ (ใหญ่, สำคัญ, หลัก, ฯลฯ) = ผ่าน
        if found_energy_keywords and any(word in text_lower for word in 
                                         ['สำคัญ', 'ใหญ่', 'หลัก', 'โลก', 'global', 
                                          'major', 'significant', 'important', 'key']):
            reasons.append("เป็นข่าวพลังงานสำคัญ")
            return True, "ผ่าน", reasons
        
        # 5. ถ้ามีแค่คำพลังงาน แต่มีรายละเอียดมากพอ = ผ่าน
        if found_energy_keywords and len(text) > 100:  # ข่าวยาวกว่า 100 ตัวอักษร
            reasons.append("มีคำพลังงาน + ข่าวยาวพอสมควร")
            return True, "ผ่าน", reasons
        
        # ถ้าไม่ผ่านเงื่อนไขใดเลย
        reasons.append("ไม่มีคำบ่งบอกธุรกิจ/ตลาด/ประเทศ")
        return False, "ไม่ใช่ข่าวธุรกิจ", reasons
    
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
        '(พลังงาน OR "ค่าไฟ" OR ก๊าซ OR LNG OR น้ำมัน OR ไฟฟ้า OR "โรงไฟฟ้า" OR "พลังงานทดแทน" OR "สัมปทาน") -"รถยนต์" -"ตลาดรถ" -"ดารา" -"นักแสดง"',
        hl="th", gl="TH", ceid="TH:th"
    )),
    ("GoogleNewsEN", "international", gnews_rss(
        '(energy OR electricity OR power OR oil OR gas OR "power plant" OR "energy project") AND (Thailand OR Vietnam OR Malaysia OR Indonesia) -car -automotive -celebrity',
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
                time.sleep(2 ** attempt)
            else:
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
# =============================================================================
# LLM ANALYZER (ปรับปรุงด้วย Few-Shot Learning)
# =============================================================================
class LLMAnalyzer:
    """วิเคราะห์ข่าวด้วย LLM พร้อม Few-Shot Learning"""
    
    def __init__(self, api_key: str, model: str, endpoint: str):
        self.api_key = api_key
        self.model = model
        self.endpoint = endpoint
        # ✨ โหลดตัวอย่างข่าวสำหรับ Few-Shot Learning
        self.example_news = get_few_shot_examples()
        print(f"[LLM] โหลดตัวอย่างข่าวสำหรับ Few-Shot Learning สำเร็จ")
    
    def analyze_news(self, title: str, summary: str) -> dict:
        """วิเคราะห์ข่าวด้วย LLM + Few-Shot Learning"""
        if not self.api_key:
            return self._get_default_analysis(title, summary)
        
        # ✨ System Prompt แบบใหม่ที่มีตัวอย่าง
        system_prompt = f"""คุณเป็นผู้เชี่ยวชาญด้านข่าวธุรกิจพลังงาน วิเคราะห์ข่าวตามตัวอย่างเหล่านี้:

{self.example_news}

กฎการตัดสิน:
- ข่าวต้องเกี่ยวกับ: การสำรวจ, การผลิต, การลงทุน, ราคาตลาด, นโยบาย, โครงการพลังงาน
- รวมถึงข่าวภูมิรัฐศาสตร์ที่ส่งผลต่อตลาดพลังงาน (อิหร่าน, รัสเซีย, เวเนซุเอลา, OPEC)
- ไม่รับ: ข่าวยานยนต์, ข่าวบันเทิง, ข่าวสังคม, ข่าวการกุศล

ตอบกลับเป็น JSON เท่านั้น:
{{
    "relevant": true/false,
    "country": "ชื่อประเทศภาษาอังกฤษ (Thailand/Vietnam/Malaysia/etc) หรือค่าว่าง",
    "summary_th": "สรุปภาษาไทยสั้นๆ 1 ประโยค",
    "topics": ["หัวข้อ1", "หัวข้อ2"],
    "confidence": 0.0-1.0
}}"""
        
        user_prompt = f"""วิเคราะห์ข่าวนี้:

หัวข้อ: {title}
เนื้อหา: {summary[:500]}

คำถาม:
1. ข่าวนี้เกี่ยวข้องกับธุรกิจพลังงานหรือไม่?
2. เกี่ยวกับประเทศไหน?
3. สรุปเป็นภาษาไทย 1 ประโยค"""
        
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
                    "max_tokens": 400
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
                
                confidence = float(analysis.get("confidence", 0.5))
                relevant = bool(analysis.get("relevant", True))
                
                if confidence < 0.7 and DEBUG_FILTERING:
                    print(f"[LLM] ความมั่นใจต่ำ ({confidence:.2f}): {title[:40]}...")
                
                return {
                    "relevant": relevant,
                    "country": str(analysis.get("country", "")).strip(),
                    "summary_th": str(analysis.get("summary_th", "")).strip()[:150],
                    "topics": [str(t).strip() for t in analysis.get("topics", []) if t],
                    "confidence": confidence
                }
                
        except json.JSONDecodeError:
            print("[LLM] Failed to parse JSON response")
        except Exception as e:
            print(f"[LLM] Error: {str(e)}")
        
        return self._get_default_analysis(title, summary)
    
    def _get_default_analysis(self, title: str, summary: str):
        """สร้างการวิเคราะห์พื้นฐาน"""
        combined = f"{title} {summary}"
        simple_summary = create_simple_summary(combined, 100)
        
        return {
            "relevant": True,
            "country": "",
            "summary_th": simple_summary if simple_summary else "สรุปข้อมูลไม่พร้อมใช้งาน",
            "topics": [],
            "confidence": 0.5
        }
# =============================================================================
# NEWS PROCESSOR
# =============================================================================
class NewsProcessor:
    def __init__(self):
        self.sent_links = read_sent_links()
        self.llm_analyzer = LLMAnalyzer(GROQ_API_KEY, GROQ_MODEL, GROQ_ENDPOINT) if GROQ_API_KEY else None
        self.dedup = EnhancedDeduplication()
        
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
        }
        
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
                'duplicate': 0,
                'passed': 0
            }
        }
        
        self.filtered_news = []
    
    def get_source_name(self, url: str) -> str:
        """ดึงชื่อเว็บข่าวจาก URL"""
        domain = extract_domain(url)
        if not domain:
            return domain
        
        for source_domain, source_name in self.news_sources.items():
            if source_domain in domain:
                return source_name
        
        return domain
    
    def fetch_and_filter_news(self):
        """Fetch and filter news from all feeds"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name} ({feed_type})...")
            
            try:
                entries = fetch_feed_with_retry(feed_name, feed_url)
                
                for entry in entries[:MAX_PER_FEED]:
                    self.filter_stats['total_processed'] += 1
                    news_item, filter_reason = self._process_entry(entry, feed_name, feed_type)
                    if news_item:
                        all_news.append(news_item)
                        self.filter_stats['filtered_by']['passed'] += 1
                        print(f"  ✓ {news_item['title'][:50]}...")
                    elif filter_reason and DEBUG_FILTERING:
                        print(f"  ✗ {filter_reason}")
                        
            except Exception as e:
                print(f"  ✗ Error: {str(e)}")
        
        all_news.sort(key=lambda x: -((x.get('published_dt') or datetime.min).timestamp()))
        
        return all_news
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """Process individual news entry"""
        item = parse_entry(entry, feed_name, feed_type)
        
        if not item["title"]:
            self.filter_stats['filtered_by']['no_title'] += 1
            return None, "ไม่มีหัวข้อข่าว"
        
        if not item["url"]:
            self.filter_stats['filtered_by']['no_url'] += 1
            return None, "ไม่มี URL"
        
        if item["canon_url"] in self.sent_links or item["url"] in self.sent_links:
            self.filter_stats['filtered_by']['already_sent'] += 1
            return None, f"ส่งแล้ว: {item['title'][:30]}..."
        
        if item["published_dt"] and not in_time_window(item["published_dt"], WINDOW_HOURS):
            self.filter_stats['filtered_by']['out_of_window'] += 1
            return None, f"เกินเวลา: {item['title'][:30]}..."
        
        if feed_type != "direct":
            display_url = item["canon_url"] or item["url"]
            if not is_allowed_source(display_url):
                self.filter_stats['filtered_by']['not_allowed_source'] += 1
                return None, f"แหล่งข่าวไม่อนุญาต: {extract_domain(display_url)}"
        
        full_text = f"{item['title']} {item['summary']}"
        is_valid, reason, details = KeywordFilter.check_valid_energy_news(full_text)
        
        if not is_valid:
            self.filter_stats['filtered_by']['invalid_energy_news'] += 1
            return None, f"{reason}: {item['title'][:30]}..."
        
        country = KeywordFilter.detect_country(full_text)
        if not country:
            if feed_type == "direct":
                country = "Thailand"
            else:
                self.filter_stats['filtered_by']['no_country'] += 1
                return None, f"ไม่พบประเทศ: {item['title'][:30]}..."
        
# ✨ ใช้ LLM วิเคราะห์พร้อมเช็ค confidence
        llm_summary = ""
        if USE_LLM_SUMMARY and self.llm_analyzer:
            llm_analysis = self.llm_analyzer.analyze_news(item['title'], item['summary'])
            
            # ✨ เช็คว่า LLM บอกว่าไม่เกี่ยวข้อง
            if not llm_analysis.get('relevant', True):
                confidence = llm_analysis.get('confidence', 0.5)
                if confidence >= 0.7:  # มั่นใจ
        
        project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
        display_url = item["canon_url"] or item["url"]
        source_name = self.get_source_name(display_url)
        
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
        
        if not self.dedup.add_item(final_item):
            self.filter_stats['filtered_by']['duplicate'] += 1
            return None, f"ข่าวซ้ำ: {item['title'][:30]}..."
        
        return final_item, None

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
        
        contents.append({
            "type": "text",
            "text": f"ประเทศ: {news_item.get('country', 'N/A')}",
            "size": "sm",
            "margin": "xs",
            "color": color,
            "weight": "bold"
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
                "paddingAll": "12px",
                "spacing": "sm"
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
            print("DRY RUN - Would send message")
            print("="*60)
            print(json.dumps(message_obj, indent=2, ensure_ascii=False)[:500])
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
# WTI FUTURES MODULE - Real Market Data (NYMEX via Public APIs)
# =============================================================================
# =============================================================================
# WTI FUTURES MODULE - Yahoo Finance Primary Source (FIXED)
# =============================================================================
class WTIFuturesFetcher:
    """ดึงข้อมูลราคา WTI Futures จาก Yahoo Finance (Primary) + EIA (Fallback)"""
    
    def __init__(self, api_key: str = None):
        """Initialize WTI Futures Fetcher"""
        self.eia_api_key = api_key
        self.eia_base_url = "https://api.eia.gov/v2"
    
    def fetch_futures_from_yahoo(self) -> Tuple[List[Dict], float]:
        """ดึงข้อมูล WTI Futures จาก Yahoo Finance (Primary Method)"""
        try:
            print("[WTI/Yahoo] กำลังดึงข้อมูล Futures จาก Yahoo Finance...")
            
            # Yahoo Finance WTI Futures symbols (NYMEX)
            contracts = {
                'CL=F': 'Front Month',
                'CLG26.NYM': 'Feb 2026',
                'CLH26.NYM': 'Mar 2026',
                'CLJ26.NYM': 'Apr 2026',
                'CLK26.NYM': 'May 2026',
                'CLM26.NYM': 'Jun 2026',
                'CLN26.NYM': 'Jul 2026',
                'CLQ26.NYM': 'Aug 2026',
                'CLU26.NYM': 'Sep 2026',
                'CLV26.NYM': 'Oct 2026',
                'CLX26.NYM': 'Nov 2026',
                'CLZ26.NYM': 'Dec 2026',
                'CLF27.NYM': 'Jan 2027'
            }
            
            futures_data = []
            base_price = None
            
            for symbol, month_label in contracts.items():
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                    params = {
                        'interval': '1d',
                        'range': '5d'
                    }
                    
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    
                    response = requests.get(url, params=params, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                            result = data['chart']['result'][0]
                            
                            if 'meta' in result and 'regularMarketPrice' in result['meta']:
                                price = result['meta']['regularMarketPrice']
                                
                                if symbol == 'CL=F':
                                    # บันทึกราคา Front Month เป็น base
                                    base_price = price
                                    print(f"[WTI/Yahoo] ✓ Current Price: ${price:.2f}/barrel")
                                else:
                                    # คำนวณ change จาก Front Month (base_price) ไม่ใช่ previous close
                                    if base_price:
                                        change = price - base_price
                                        change_pct = (change / base_price) * 100
                                    else:
                                        # ถ้ายังไม่มี base ให้ใช้ previous close
                                        prev_close = result['meta'].get('chartPreviousClose', price)
                                        change = price - prev_close
                                        change_pct = (change / prev_close) * 100 if prev_close else 0
                                    
                                    futures_data.append({
                                        "month": month_label,
                                        "contract": symbol.replace('.NYM', ''),
                                        "price": round(price, 2),
                                        "change": round(change, 2),
                                        "change_pct": round(change_pct, 2)
                                    })
                    
                    time.sleep(0.2)  # Rate limiting
                    
                except Exception as e:
                    print(f"[WTI/Yahoo] Warning for {symbol}: {str(e)}")
                    continue
            
            if futures_data and base_price:
                print(f"[WTI/Yahoo] ✓ ดึงข้อมูล {len(futures_data)} สัญญา")
                return futures_data, base_price
            
            return [], None
                
        except Exception as e:
            print(f"[WTI/Yahoo] Error: {str(e)}")
            return [], None
    
    def fetch_current_wti_price(self) -> Tuple[float, str]:
        """ดึงราคา WTI Spot Price จาก EIA (Fallback only)"""
        if not self.eia_api_key:
            return None, None
            
        url = f"{self.eia_base_url}/petroleum/pri/spt/data/"
        params = {
            "api_key": self.eia_api_key,
            "frequency": "daily",
            "data[0]": "value",
            "facets[product][]": "EPCWTI",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 1
        }
        
        try:
            print(f"[WTI/EIA] กำลังดึงราคา WTI Spot Price (Fallback)...")
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            response_data = data['response']['data']
            if response_data:
                price = float(response_data[0]['value'])
                period = response_data[0].get('period', '')
                print(f"[WTI/EIA] ✓ Spot Price: ${price:.2f}/barrel ({period})")
                return price, period
                
        except Exception as e:
            print(f"[WTI/EIA] Warning: {str(e)}")
        
        return None, None
    
    def _estimate_futures_from_spot(self, spot_price: float) -> List[Dict]:
        """คำนวณ futures จาก spot price (Emergency fallback)"""
        futures_data = []
        now = datetime.now(TZ)
        
        # Contango curve based on historical patterns
        # WTI typically shows ~$0.25-0.50 per month contango
        monthly_premium = 0.35
        
        for i in range(12):
            months_ahead = i + 1
            future_date = now + timedelta(days=30 * months_ahead)
            
            # Simple contango calculation
            premium = months_ahead * monthly_premium
            future_price = spot_price + premium
            
            futures_data.append({
                "month": future_date.strftime("%b %Y"),
                "contract": future_date.strftime("%b%y").upper(),
                "price": round(future_price, 2),
                "change": round(premium, 2),
                "change_pct": round((premium / spot_price) * 100, 2)
            })
        
        return futures_data
    
    def get_current_and_futures(self) -> Dict:
        """ดึงข้อมูลราคาปัจจุบันและ futures (Yahoo Finance First)"""
        print("\n[WTI] กำลังดึงข้อมูลราคา WTI Futures...")
        
        # Strategy 1: Try Yahoo Finance first (Best quality, real-time data)
        futures_data, current_price = self.fetch_futures_from_yahoo()
        
        if futures_data and current_price:
            print(f"[WTI] ✓ ใช้ข้อมูลจาก Yahoo Finance - {len(futures_data)} สัญญา")
            
            return {
                "current": {
                    "source": "Yahoo Finance (NYMEX)",
                    "current_price": current_price,
                    "timestamp": datetime.now(TZ).isoformat(),
                    "currency": "USD/barrel",
                    "commodity": "WTI Crude Oil Futures"
                },
                "futures": futures_data[:12],
                "updated_at": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
                "is_estimated": False,
                "method": "Real-time data from Yahoo Finance (NYMEX)"
            }
        
        # Strategy 2: Fallback to EIA Spot + Estimation
        print("[WTI] Yahoo Finance ไม่สำเร็จ กำลังใช้ EIA Spot Price...")
        spot_price, spot_date = self.fetch_current_wti_price()
        
        if spot_price:
            print(f"[WTI] ✓ ใช้ EIA Spot Price + คำนวณ Futures")
            futures_data = self._estimate_futures_from_spot(spot_price)
            
            return {
                "current": {
                    "source": f"U.S. EIA Spot Price ({spot_date})",
                    "current_price": spot_price,
                    "timestamp": datetime.now(TZ).isoformat(),
                    "currency": "USD/barrel",
                    "commodity": "WTI Crude Oil (Cushing, OK)"
                },
                "futures": futures_data,
                "updated_at": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
                "is_estimated": True,
                "method": "EIA spot price + statistical estimation"
            }
        
        # Strategy 3: Use default fallback
        print("[WTI] ⚠️ ทุกแหล่งล้มเหลว ใช้ค่าเริ่มต้น")
        default_price = 75.00
        
        return {
            "current": {
                "source": "Default Estimate",
                "current_price": default_price,
                "timestamp": datetime.now(TZ).isoformat(),
                "currency": "USD/barrel",
                "commodity": "WTI Crude Oil"
            },
            "futures": self._estimate_futures_from_spot(default_price),
            "updated_at": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
            "is_estimated": True,
            "method": "Emergency fallback (all sources failed)"
        }


class WTIFlexMessageBuilder:
    """สร้าง LINE Flex Message สำหรับแสดงราคา WTI Futures"""
    
    @staticmethod
    def create_wti_futures_message(data: dict) -> dict:
        """สร้าง Flex Message แสดงราคา WTI Futures ครบ 12 เดือน"""
        current = data.get("current", {})
        futures = data.get("futures", [])
        updated_at = data.get("updated_at", "")
        current_price = current.get("current_price", 0)
        is_estimated = data.get("is_estimated", True)
        method = data.get("method", "")
        source = current.get("source", "Unknown")
        
        header_contents = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "🛢️ WTI Crude Oil Futures",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#FFFFFF"
                },
                {
                    "type": "text",
                    "text": "ราคาน้ำมันล่วงหน้า 12 เดือน",
                    "size": "sm",
                    "color": "#FFFFFF",
                    "margin": "xs"
                }
            ],
            "backgroundColor": "#1E3A8A",
            "paddingAll": "20px"
        }
        
        current_box = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "ราคาปัจจุบัน (Front Month)",
                    "size": "sm",
                    "color": "#8B8B8B",
                    "weight": "bold"
                },
                {
                    "type": "text",
                    "text": f"${current_price:.2f}",
                    "size": "xxl",
                    "weight": "bold",
                    "color": "#1E3A8A",
                    "margin": "xs"
                },
                {
                    "type": "text",
                    "text": "per barrel",
                    "size": "xs",
                    "color": "#8B8B8B",
                    "margin": "xs"
                }
            ],
            "backgroundColor": "#F0F9FF",
            "cornerRadius": "10px",
            "paddingAll": "15px",
            "margin": "md"
        }
        
        table_header = {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": "เดือน",
                    "size": "xs",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": "ราคา",
                    "size": "xs",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "align": "end",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": "เปลี่ยน",
                    "size": "xs",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "align": "end",
                    "flex": 1
                }
            ],
            "backgroundColor": "#3B82F6",
            "paddingAll": "10px",
            "cornerRadius": "5px",
            "margin": "lg"
        }
        
        futures_rows = []
        for i, future in enumerate(futures[:12]):
            month = future.get("month", "")
            price = future.get("price", 0)
            change_pct = future.get("change_pct", 0)
            
            change_color = "#16A34A" if change_pct >= 0 else "#DC2626"
            change_symbol = "+" if change_pct >= 0 else ""
            
            row = {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {
                        "type": "text",
                        "text": month,
                        "size": "sm",
                        "color": "#333333",
                        "flex": 2
                    },
                    {
                        "type": "text",
                        "text": f"${price:.2f}",
                        "size": "sm",
                        "color": "#333333",
                        "align": "end",
                        "weight": "bold",
                        "flex": 2
                    },
                    {
                        "type": "text",
                        "text": f"{change_symbol}{change_pct:.1f}%",
                        "size": "xs",
                        "color": change_color,
                        "align": "end",
                        "weight": "bold",
                        "flex": 1
                    }
                ],
                "paddingAll": "8px",
                "backgroundColor": "#F9FAFB" if i % 2 == 0 else "#FFFFFF"
            }
            futures_rows.append(row)
        
        footer_contents = [
            {
                "type": "separator",
                "margin": "md"
            },
            {
                "type": "text",
                "text": f"อัปเดต: {updated_at}",
                "size": "xs",
                "color": "#8B8B8B",
                "align": "center",
                "margin": "md"
            },
            {
                "type": "text",
                "text": f"📡 ข้อมูลจาก {source}",
                "size": "xxs",
                "color": "#8B8B8B",
                "align": "center",
                "margin": "xs"
            }
        ]
        
        if is_estimated:
            footer_contents.append({
                "type": "text",
                "text": "⚠️ ราคา Futures เป็นการประมาณการ",
                "size": "xxs",
                "color": "#F59E0B",
                "align": "center",
                "margin": "xs"
            })
        else:
            footer_contents.append({
                "type": "text",
                "text": "✅ ราคาจริงจากตลาด NYMEX",
                "size": "xxs",
                "color": "#16A34A",
                "align": "center",
                "margin": "xs"
            })
        
        footer = {
            "type": "box",
            "layout": "vertical",
            "contents": footer_contents
        }
        
        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": header_contents,
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    current_box,
                    table_header,
                    *futures_rows,
                    footer
                ],
                "paddingAll": "0px"
            }
        }
        
        return {
            "type": "flex",
            "altText": f"ราคา WTI Crude Oil Futures: ${current_price:.2f}/barrel",
            "contents": bubble
        }

# =============================================================================
# MAIN FUNCTION
# =============================================================================
def main():
    print("="*60)
    print("ระบบติดตามข่าวพลังงาน + WTI Futures (EIA API)")
    print("="*60)
    
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    if not EIA_API_KEY:
        print("[ERROR] EIA_API_KEY is required")
        print("        Get one from: https://www.eia.gov/opendata/")
        return
    
    if USE_LLM_SUMMARY and not GROQ_API_KEY:
        print("[WARNING] LLM summary enabled but no GROQ_API_KEY provided")
        print("[INFO] Will use simple summary for all news")
    
    print(f"\n[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No'}")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] Debug filtering: {'Yes' if DEBUG_FILTERING else 'No'}")
    print(f"[CONFIG] WTI Data Source: EIA (U.S. Energy Information Administration)")
    
    processor = NewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    print("\n[1] กำลังดึงและกรองข่าว...")
    news_items = processor.fetch_and_filter_news()
    
    print(f"\n[FILTER STATISTICS]")
    print(f"  รวมข่าวที่ประมวลผล: {processor.filter_stats['total_processed']}")
    print(f"  ผ่านการกรอง: {processor.filter_stats['filtered_by']['passed']}")
    print(f"  ไม่ผ่านการกรอง: {processor.filter_stats['total_processed'] - processor.filter_stats['filtered_by']['passed']}")
    
    if processor.filter_stats['total_processed'] - processor.filter_stats['filtered_by']['passed'] > 0:
        print(f"\n  รายละเอียดการกรอง:")
        for reason, count in processor.filter_stats['filtered_by'].items():
            if reason != 'passed' and count > 0:
                print(f"    - {reason}: {count} ข่าว")
    
    success_news = False
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่ที่เกี่ยวข้อง")
    else:
        print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
        
        llm_summary_count = sum(1 for item in news_items if item.get('llm_summary'))
        source_counts = {}
        country_counts = {}
        
        for item in news_items:
            source = item.get('source_name') or item.get('domain', 'Unknown')
            source_counts[source] = source_counts.get(source, 0) + 1
            
            country = item.get('country', 'Unknown')
            country_counts[country] = country_counts.get(country, 0) + 1
        
        print(f"   - สรุปด้วย AI: {llm_summary_count} ข่าว")
        print(f"   - แหล่งข่าวที่พบ:")
        for source, count in sorted(source_counts.items()):
            print(f"     • {source}: {count} ข่าว")
        print(f"   - แบ่งตามประเทศ:")
        for country, count in sorted(country_counts.items()):
            print(f"     • {country}: {count} ข่าว")
        
        print("\n[3] กำลังสร้างข้อความ LINE...")
        line_message = LineMessageBuilder.create_carousel_message(news_items)
        
        if line_message:
            print("\n[4] กำลังส่งข่าวพลังงาน...")
            success_news = line_sender.send_message(line_message)
        else:
            print("[WARNING] ไม่สามารถสร้างข้อความข่าวได้")
    
    print("\n[5] กำลังส่งข้อมูล WTI Futures...")
    success_wti = False
    try:
        wti_fetcher = WTIFuturesFetcher(api_key=EIA_API_KEY)
        wti_data = wti_fetcher.get_current_and_futures()
        wti_message = WTIFlexMessageBuilder.create_wti_futures_message(wti_data)
        
        success_wti = line_sender.send_message(wti_message)
        
    except Exception as e:
        print(f"[WTI ERROR] {str(e)}")
    
    if news_items and not DRY_RUN:
        for item in news_items:
            append_sent_link(item.get('canon_url') or item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    print("\n" + "="*60)
    if news_items:
        print(f"ดำเนินการเสร็จสิ้น - ส่งข่าว: {'✓' if success_news else '✗'}, ส่ง WTI: {'✓' if success_wti else '✗'}")
    else:
        print(f"ดำเนินการเสร็จสิ้น - ไม่มีข่าวใหม่, ส่ง WTI: {'✓' if success_wti else '✗'}")
    print("="*60)

if __name__ == "__main__":
    main()
