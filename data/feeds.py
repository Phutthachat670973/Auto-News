# -*- coding: utf-8 -*-
"""
RSS Feed Sources
แหล่งข้อมูลข่าวจาก RSS Feeds
"""

import requests

def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    """สร้าง Google News RSS URL"""
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

FEEDS = [
    (
        "GoogleNewsTH", 
        "thai", 
        gnews_rss(
            '(พลังงาน OR "ค่าไฟ" OR ก๊าซ OR LNG OR น้ำมัน OR ไฟฟ้า OR "โรงไฟฟ้า" OR "พลังงานทดแทน" OR "สัมปทาน") -"รถยนต์" -"ตลาดรถ" -"ดารา" -"นักแสดง"',
            hl="th", gl="TH", ceid="TH:th"
        )
    ),
    (
        "GoogleNewsEN", 
        "international", 
        gnews_rss(
            '(energy OR electricity OR power OR oil OR gas OR "power plant" OR "energy project") AND (Thailand OR Vietnam OR Malaysia OR Indonesia) -car -automotive -celebrity',
            hl="en", gl="US", ceid="US:en"
        )
    ),
]
