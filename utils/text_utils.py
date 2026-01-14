# -*- coding: utf-8 -*-
"""
Text Utilities
ฟังก์ชันจัดการข้อความ
"""

import re

def cut(s: str, n: int) -> str:
    """ตัดข้อความให้เหลือ n ตัวอักษร"""
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"

def create_simple_summary(text: str, max_length: int = 150) -> str:
    """สร้างสรุปสั้นๆ จากข้อความ"""
    text = (text or "").strip()
    if not text:
        return ""
    
    # รวมช่องว่างหลายๆ ช่องเป็นช่องเดียว
    text = ' '.join(text.split())
    
    # ตัดประโยคแรก
    sentences = re.split(r'[.!?]', text)
    if sentences and len(sentences[0]) > 10:
        summary = sentences[0].strip()
        if len(summary) > max_length:
            summary = summary[:max_length-1] + "…"
        return summary + "."
    
    # ถ้าไม่มีประโยคที่ชัดเจน ให้ตัดตามความยาว
    if len(text) > max_length:
        return text[:max_length-1] + "…"
    return text
