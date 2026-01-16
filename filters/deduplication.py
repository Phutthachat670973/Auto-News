# -*- coding: utf-8 -*-
"""
Enhanced Deduplication System
ระบบกันข่าวซ้ำที่ปรับปรุงแล้ว + Event-based Deduplication
"""

import re
import hashlib
from typing import List, Set, Tuple, Optional, Dict
from difflib import SequenceMatcher
from utils.url_utils import normalize_url
from config.settings import DEBUG_FILTERING

class EnhancedDeduplication:
    """ระบบกันข่าวซ้ำที่ปรับปรุงใหม่"""
    
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
    
    # ← เพิ่มส่วนนี้: Event Signature Keywords
    EVENT_SIGNATURES = {
        'congress': ['congress', 'ประชุม', 'national congress', 'พรรค', 'การประชุม'],
        'election': ['election', 'เลือกตั้ง', 'vote', 'ballot'],
        'summit': ['summit', 'การประชุมสุดยอด', 'meeting'],
        'conference': ['conference', 'งานประชุม', 'expo', 'exhibition'],
        'deal': ['deal', 'agreement', 'contract', 'สัญญา', 'ข้อตกลง'],
        'acquisition': ['acquisition', 'merger', 'takeover', 'ซื้อกิจการ', 'เข้าซื้อ'],
        'lawsuit': ['lawsuit', 'court', 'legal', 'ฟ้อง', 'คดี', 'ศาล'],
        'regulation': ['regulation', 'rules', 'policy', 'กฎหมาย', 'ระเบียบ'],
        'appointment': ['appoint', 'resign', 'แต่งตั้ง', 'ลาออก', 'ceo', 'chairman'],
        'project': ['project', 'development', 'construction', 'โครงการ', 'ก่อสร้าง'],
        'incident': ['accident', 'fire', 'explosion', 'เหตุการณ์', 'อุบัติเหตุ'],
        'price_change': ['price', 'surge', 'drop', 'ราคา', 'ปรับขึ้น', 'ปรับลด'],
    }
    
    def __init__(self):
        self.seen_urls: Set[str] = set()
        self.seen_fingerprints: Set[str] = set()
        self.processed_items: List[dict] = []
        self.title_cache: List[Tuple[str, str]] = []
        self.event_signatures: Dict[str, List[dict]] = {}  # ← เพิ่ม: เก็บ event signatures
    
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
    
    # ← เพิ่มฟังก์ชันนี้: ตรวจจับ Event Type
    def detect_event_type(self, text: str) -> Optional[str]:
        """
        ตรวจจับประเภทของเหตุการณ์
        
        Returns:
            event_type (str) หรือ None ถ้าไม่พบ
        """
        text_lower = text.lower()
        
        for event_type, keywords in self.EVENT_SIGNATURES.items():
            for keyword in keywords:
                if keyword in text_lower:
                    return event_type
        
        return None
    
    # ← เพิ่มฟังก์ชันนี้: สร้าง Event Signature
    def create_event_signature(self, item: dict) -> Optional[str]:
        """
        สร้าง signature สำหรับเหตุการณ์
        
        สำหรับข่าวที่พูดถึงเหตุการณ์เดียวกัน เช่น:
        - "Vietnam's 14th National Congress"
        - "มงสการประชุมใหญ่พรรดคจังที่ 14"
        
        จะได้ signature เดียวกัน
        """
        full_text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        
        # 1. ตรวจจับประเภทเหตุการณ์
        event_type = self.detect_event_type(full_text)
        if not event_type:
            return None
        
        # 2. ดึงข้อมูลเฉพาะ
        country = item.get('country', '')
        
        # 3. ดึงตัวเลขสำคัญ (เช่น "14th", "2026")
        numbers = re.findall(r'\b\d+(?:st|nd|rd|th)?\b', full_text)
        numbers_str = '|'.join(sorted(set(numbers))[:3])  # เอาแค่ 3 ตัวแรก
        
        # 4. ดึงชื่อองค์กร/โครงการสำคัญ
        important_entities = self._extract_entities(full_text)
        entities_str = '|'.join(sorted(important_entities)[:2])
        
        # 5. สร้าง signature
        signature = f"{event_type}|{country}|{numbers_str}|{entities_str}"
        
        return hashlib.md5(signature.encode('utf-8')).hexdigest()
    
    def _extract_entities(self, text: str) -> Set[str]:
        """ดึงชื่อองค์กร/โครงการที่สำคัญ"""
        entities = set()
        
        # รายชื่อองค์กรสำคัญ
        organizations = [
            'pttep', 'ปตท', 'chevron', 'shell', 'exxon', 'total', 'bp',
            'petrovietnam', 'petronas', 'pertamina', 'celcomdigi',
            'egat', 'กฟผ', 'ptt', 'irpc', 'bangchak', 'บางจาก',
            'congress', 'parliament', 'government', 'รัฐบาล'
        ]
        
        for org in organizations:
            if org in text:
                entities.add(org)
        
        return entities
    
    def create_content_fingerprint(self, item: dict) -> str:
        """สร้าง fingerprint จากเนื้อหาข่าว"""
        title = self.normalize_text(item.get('title', ''))
        title_clean = re.sub(r'\s+', ' ', title).strip()
        
        country = item.get('country', '')
        keywords = self.extract_keywords(f"{item.get('title', '')} {item.get('summary', '')}")
        keywords_str = '|'.join(sorted(keywords))
        
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
        """ตรวจสอบว่าเนื้อหาข่าวซ้ำหรือไม่"""
        # Check 0: URL ซ้ำ
        url = item.get('canon_url') or item.get('url', '')
        if self.is_duplicate_url(url):
            return True, "URL ซ้ำ"
        
        # ← เพิ่ม Check 0.5: Event Signature (ตรวจสอบก่อนอื่นหมด)
        event_sig = self.create_event_signature(item)
        if event_sig:
            # ตรวจสอบว่ามี event นี้อยู่แล้วหรือไม่
            if event_sig in self.event_signatures:
                existing_items = self.event_signatures[event_sig]
                
                # ถ้ามีข่าวที่พูดถึง event เดียวกันแล้ว
                for existing in existing_items:
                    time_diff = abs((item.get('published_dt') - existing.get('published_dt')).total_seconds() / 3600)
                    
                    # ถ้าเผยแพร่ห่างกันไม่เกิน 24 ชั่วโมง = เหตุการณ์เดียวกัน
                    if time_diff <= 24:
                        return True, f"เป็นข่าวเหตุการณ์เดียวกันกับ '{existing.get('title', '')[:40]}...'"
            else:
                # สร้าง list ใหม่สำหรับ event นี้
                self.event_signatures[event_sig] = []
        
        # Check 1: Title เหมือนกันทุกตัวอักษร
        title = item.get('title', '')
        for existing in self.processed_items:
            existing_title = existing.get('title', '')
            if title == existing_title:
                return True, "Title เหมือนกันทุกตัวอักษร"
            
            similarity = self.calculate_similarity(title, existing_title)
            if similarity > 0.95:
                return True, f"Title เหมือนกันเกือบทุกคำ ({similarity:.1%})"
        
        # Check 2: Fingerprint ซ้ำ
        fingerprint = self.create_content_fingerprint(item)
        if fingerprint in self.seen_fingerprints:
            return True, "Fingerprint ซ้ำ (เนื้อหาเดียวกัน)"
        self.seen_fingerprints.add(fingerprint)
        
        # Check 3: Title คล้ายกันมาก
        for cached_norm_title, cached_orig_title in self.title_cache:
            similarity = self.calculate_similarity(title, cached_norm_title)
            
            if similarity > 0.90:
                return True, f"Title เหมือนกันเกือบทุกคำ ({similarity:.1%})"
            
            if similarity > 0.80:
                for existing in self.processed_items:
                    if existing.get('title') == cached_orig_title:
                        if existing.get('country') != item.get('country'):
                            continue
                        return True, f"Title คล้ายกันมาก + ประเทศเดียวกัน ({similarity:.1%})"
        
        # Check 4: คำสำคัญตรงกันมาก
        current_keywords = self.extract_keywords(f"{item.get('title', '')} {item.get('summary', '')}")
        if len(current_keywords) >= 3:
            for existing in self.processed_items:
                existing_keywords = self.extract_keywords(
                    f"{existing.get('title', '')} {existing.get('summary', '')}"
                )
                
                common_keywords = current_keywords & existing_keywords
                if len(common_keywords) >= len(current_keywords) * 0.85:
                    title_sim = self.calculate_similarity(item.get('title', ''), existing.get('title', ''))
                    if title_sim > 0.70:
                        pub_dt1 = item.get('published_dt')
                        pub_dt2 = existing.get('published_dt')
                        if pub_dt1 and pub_dt2:
                            time_diff = abs((pub_dt1 - pub_dt2).total_seconds() / 3600)
                            if time_diff < 24:
                                return True, f"คำสำคัญตรงกัน {len(common_keywords)} คำ + title คล้ายกัน + เวลาใกล้กัน"
        
        # Check 5: คำเฉพาะเจาะจงซ้ำ
        specific_terms = self._extract_specific_terms(title)
        if len(specific_terms) >= 2:
            for existing in self.processed_items:
                existing_terms = self._extract_specific_terms(existing.get('title', ''))
                common_terms = specific_terms & existing_terms
                if len(common_terms) >= 2:
                    title_sim = self.calculate_similarity(title, existing.get('title', ''))
                    if title_sim > 0.75:
                        return True, f"พบคำเฉพาะเจาะจงซ้ำ: {', '.join(common_terms)}"
        
        # ← เพิ่มข่าวเข้า event signature (ถ้ามี)
        if event_sig:
            self.event_signatures[event_sig].append(item)
        
        # เพิ่มข้อมูลลง cache
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
        """ดึงคำเฉพาะเจาะจง"""
        text_lower = text.lower()
        specific_terms = set()
        
        date_patterns = [
            r'\d{1,2}\s*ม\.ค\.', r'\d{1,2}\s*ก\.พ\.', r'\d{1,2}\s*มี\.ค\.',
            r'\d{1,2}/\d{1,2}/\d{2,4}', r'วันนี้', r'today'
        ]
        
        for pattern in date_patterns:
            matches = re.findall(pattern, text_lower)
            specific_terms.update(matches)
        
        project_names = [
            'natuna sea a', 'natuna', 'arthit', 'zawtika', 'yadana',
            'sk309', 'sk311', 'block h', 'block 61', 'dunga',
            'จี 1/61', 'จี 2/61', 'เอส 1', 'บี 6/27',
            'indonesia', 'malaysia', 'vietnam', 'myanmar', 'oman', 'uae'
        ]
        
        for project in project_names:
            if project in text_lower:
                specific_terms.add(project)
        
        number_patterns = re.findall(r'\$\d+|\d+\s*(?:บาท|ดอลลาร์|ล้าน|พันล้าน)', text_lower)
        specific_terms.update(number_patterns[:2])
        
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
