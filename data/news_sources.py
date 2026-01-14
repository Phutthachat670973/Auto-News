# -*- coding: utf-8 -*-
"""
News Sources Database
ฐานข้อมูลแหล่งข่าว - Mapping domain -> ชื่อเว็บข่าว
"""

NEWS_SOURCES = {
    # International
    'reuters.com': 'Reuters',
    'bloomberg.com': 'Bloomberg',
    'cnbc.com': 'CNBC',
    'wsj.com': 'Wall Street Journal',
    'ft.com': 'Financial Times',
    
    # Thailand - English
    'bangkokpost.com': 'Bangkok Post',
    'nationthailand.com': 'The Nation Thailand',
    
    # Thailand - Thai
    'thansettakij.com': 'ฐานเศรษฐกิจ',
    'posttoday.com': 'Post Today',
    'prachachat.net': 'ประชาชาติธุรกิจ',
    'mgronline.com': 'ผู้จัดการออนไลน์',
    'komchadluek.net': 'คมชัดลึก',
    'naewna.com': 'แนวหน้า',
    'dailynews.co.th': 'เดลินิวส์',
    'thairath.co.th': 'ไทยรัฐ',
    'khaosod.co.th': 'ข่าวสด',
    'matichon.co.th': 'มติชน',
    'sanook.com': 'สนุก',
    'thaipbs.or.th': 'ไทยพีบีเอส',
    
    # Energy-specific
    'energyvoice.com': 'Energy Voice',
    'oilprice.com': 'OilPrice.com',
    'offshore-technology.com': 'Offshore Technology',
    'upstreamonline.com': 'Upstream Online',
}

def get_source_name(domain: str) -> str:
    """
    ดึงชื่อเว็บข่าวจาก domain
    
    Args:
        domain: domain name (เช่น 'reuters.com')
    
    Returns:
        ชื่อเว็บข่าว หรือ domain ถ้าไม่พบ
    """
    if not domain:
        return ""
    
    domain = domain.lower()
    
    # ลบ www. ออก
    if domain.startswith("www."):
        domain = domain[4:]
    
    # ค้นหาในฐานข้อมูล
    for source_domain, source_name in NEWS_SOURCES.items():
        if source_domain in domain:
            return source_name
    
    # ถ้าไม่เจอ ให้คืนค่า domain
    return domain
