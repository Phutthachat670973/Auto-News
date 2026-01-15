# -*- coding: utf-8 -*-
"""
HTML Utilities
ฟังก์ชันจัดการ HTML entities และ tags
"""

import re
import html
from html.parser import HTMLParser

class HTMLTextExtractor(HTMLParser):
    """ดึงข้อความจาก HTML โดยไม่มี tags"""
    
    def __init__(self):
        super().__init__()
        self.text_parts = []
    
    def handle_data(self, data):
        self.text_parts.append(data)
    
    def get_text(self):
        return ''.join(self.text_parts)


def decode_html_entities(text: str) -> str:
    """
    แปลง HTML entities เป็นข้อความปกติ
    
    Examples:
        &lt; → <
        &gt; → >
        &quot; → "
        &amp; → &
        &#39; → '
    
    Args:
        text: ข้อความที่มี HTML entities
    
    Returns:
        ข้อความที่แปลงแล้ว
    """
    if not text:
        return ""
    
    # ใช้ html.unescape() เพื่อแปลง HTML entities
    return html.unescape(text)


def strip_html_tags(text: str) -> str:
    """
    ลบ HTML tags ทั้งหมดออกจากข้อความ
    
    Examples:
        <a href="...">Link</a> → Link
        <strong>Bold</strong> → Bold
        <p>Text</p> → Text
    
    Args:
        text: ข้อความที่มี HTML tags
    
    Returns:
        ข้อความที่ลบ tags แล้ว
    """
    if not text:
        return ""
    
    # Method 1: ใช้ HTMLParser (ปลอดภัยกว่า)
    try:
        parser = HTMLTextExtractor()
        parser.feed(text)
        return parser.get_text()
    except Exception:
        pass
    
    # Method 2: ใช้ regex (fallback)
    # ลบ HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    return text


def clean_html_text(text: str) -> str:
    """
    ทำความสะอาดข้อความจาก HTML (แปลง entities + ลบ tags)
    
    Args:
        text: ข้อความที่มี HTML
    
    Returns:
        ข้อความที่สะอาดแล้ว
    """
    if not text:
        return ""
    
    # 1. ลบ HTML tags ก่อน
    text = strip_html_tags(text)
    
    # 2. แปลง HTML entities
    text = decode_html_entities(text)
    
    # 3. ลบช่องว่างเกิน
    text = ' '.join(text.split())
    
    return text.strip()


def clean_google_news_text(text: str) -> str:
    """
    ทำความสะอาดข้อความจาก Google News RSS โดยเฉพาะ
    
    Google News มักมี:
    - HTML entities (&lt;, &gt;, &quot;, etc.)
    - HTML tags (<a>, <strong>, etc.)
    - ช่องว่างและ newlines เกิน
    
    Args:
        text: ข้อความจาก Google News RSS
    
    Returns:
        ข้อความที่สะอาดและอ่านง่าย
    """
    if not text:
        return ""
    
    # 1. แปลง HTML entities
    text = html.unescape(text)
    
    # 2. ลบ HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # 3. ลบ URLs (ถ้ามี)
    text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
    
    # 4. ลบ special characters ที่ไม่จำเป็น
    text = re.sub(r'[\r\n\t]+', ' ', text)
    
    # 5. ลบช่องว่างเกิน
    text = ' '.join(text.split())
    
    # 6. ลบเครื่องหมายคำพูดซ้อน (ถ้ามี)
    text = re.sub(r'"+', '"', text)
    text = re.sub(r"'+", "'", text)
    
    return text.strip()


# ======================================================================
# ฟังก์ชันทดสอบ
# ======================================================================

if __name__ == "__main__":
    # ทดสอบ
    test_cases = [
        "&lt;a href=&quot;https://example.com&quot;&gt;Link&lt;/a&gt;",
        "Thailand&#39;s energy &amp; power sector",
        "<strong>Breaking News:</strong> Oil price rises",
        "Text with &nbsp; spaces &amp; &lt;tags&gt;",
        "ข่าวพลังงาน: ราคาน้ำมันปรับขึ้น &gt; $75/barrel"
    ]
    
    print("Testing HTML cleaning functions:")
    print("=" * 60)
    
    for i, test in enumerate(test_cases, 1):
        print(f"\nTest {i}:")
        print(f"  Original: {test}")
        print(f"  Cleaned:  {clean_google_news_text(test)}")
