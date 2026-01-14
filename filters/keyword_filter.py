# -*- coding: utf-8 -*-
"""
Keyword Filter
กรองข่าวตามคำสำคัญ
"""

class KeywordFilter:
    """กรองข่าวตามคำสำคัญพลังงาน"""
    
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
        """ตรวจสอบว่าเป็นข่าวพลังงานที่เกี่ยวข้องกับธุรกิจหรือไม่"""
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
        
        # เงื่อนไขผ่าน
        if found_energy_keywords and found_market_keywords:
            reasons.append("เป็นข่าวราคา/ตลาดพลังงาน")
            return True, "ผ่าน", reasons
        
        if found_energy_keywords and found_business_keywords:
            reasons.append("มีคำพลังงาน + คำธุรกิจ")
            return True, "ผ่าน", reasons
        
        country_keywords = ['thailand', 'vietnam', 'malaysia', 'indonesia', 'myanmar', 
                           'oman', 'uae', 'kazakhstan', 'ไทย', 'เวียดนาม', 'มาเลเซีย', 
                           'อินโดนีเซีย', 'เมียนมา', 'โอมาน', 'ยูเออี', 'คาซัคสถาน']
        if found_energy_keywords and any(country in text_lower for country in country_keywords):
            reasons.append("มีคำพลังงาน + ชื่อประเทศ")
            return True, "ผ่าน", reasons
        
        if found_energy_keywords and any(word in text_lower for word in 
                                         ['สำคัญ', 'ใหญ่', 'หลัก', 'โลก', 'global', 
                                          'major', 'significant', 'important', 'key']):
            reasons.append("เป็นข่าวพลังงานสำคัญ")
            return True, "ผ่าน", reasons
        
        if found_energy_keywords and len(text) > 100:
            reasons.append("มีคำพลังงาน + ข่าวยาวพอสมควร")
            return True, "ผ่าน", reasons
        
        reasons.append("ไม่มีคำบ่งบอกธุรกิจ/ตลาด/ประเทศ")
        return False, "ไม่ใช่ข่าวธุรกิจ", reasons
    
    @classmethod
    def detect_country(cls, text: str) -> str:
        """ตรวจสอบประเทศจากข้อความ"""
        text_lower = text.lower()
        
        primary_countries = {
            "Thailand": ['ไทย', 'ประเทศไทย', 'thailand', 'bangkok', 'กรุงเทพ'],
            "Myanmar": ['เมียนมา', 'myanmar', 'ย่างกุ้ง', 'yangon', 'burma'],
            "Malaysia": ['มาเลเซีย', 'malaysia', 'กัวลาลัมเปอร์', 'kuala lumpur'],
            "Vietnam": ['เวียดนาม', 'vietnam', 'ฮานอย', 'hanoi', 'ญวน'],
            "Indonesia": ['อินโดนีเซีย', 'indonesia', 'จาการ์ตา', 'jakarta'],
            "Kazakhstan": ['คาซัคสถาน', 'kazakhstan', 'astana', 'kazakh'],
            "Oman": ['โอมาน', 'oman', 'muscat'],
            "UAE": ['ยูเออี', 'uae', 'ดูไบ', 'dubai', 'อาบูดาบี', 'abu dhabi', 'emirates']
        }
        
        for country, patterns in primary_countries.items():
            if any(pattern in text_lower for pattern in patterns):
                return country
        
        international_keywords = [
            'opec', 'โอเปก', 'iea', 'global oil', 'world energy', 'crude oil',
            'brent', 'wti', 'oil market', 'gas market', 'energy market',
            'ตลาดน้ำมันโลก', 'ตลาดพลังงานโลก', 'น้ำมันโลก',
            'saudi', 'russia', 'united states', 'สหรัฐ', 'รัสเซีย', 'ซาอุดีอาระเบีย',
            'iran', 'iraq', 'venezuela', 'อิหร่าน', 'อิรัก', 'เวเนซุเอลา',
            'europe', 'european union', 'china', 'japan', 'korea',
            'ยุโรป', 'จีน', 'ญี่ปุ่น', 'เกาหลี', 'อียู'
        ]
        
        if any(keyword in text_lower for keyword in international_keywords):
            return "International"
        
        return ""
