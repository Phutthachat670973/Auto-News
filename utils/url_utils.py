# -*- coding: utf-8 -*-
"""
URL Utilities
ฟังก์ชันจัดการ URL
"""

from urllib.parse import urlparse, parse_qs, unquote

def normalize_url(url: str) -> str:
    """ทำให้ URL เป็นมาตรฐาน (เอา fragment ออก)"""
    url = (url or "").strip()
    if not url:
        return url
    try:
        u = urlparse(url)
        return u._replace(fragment="").geturl()
    except Exception:
        return url

def extract_domain(url: str) -> str:
    """ดึงชื่อ domain จาก URL"""
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

def shorten_google_news_url(url: str) -> str:
    """ดึง URL จริงจาก Google News redirect"""
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
