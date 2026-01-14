# -*- coding: utf-8 -*-
"""
Configuration Settings
การตั้งค่าทั้งหมดของระบบ
"""

import os
import pytz

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# =============================================================================
# TIMEZONE
# =============================================================================
TZ = pytz.timezone(os.getenv("TZ", "Asia/Bangkok"))

# =============================================================================
# LINE CONFIGURATION
# =============================================================================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN")

# =============================================================================
# GROQ LLM CONFIGURATION
# =============================================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions").strip()
USE_LLM_SUMMARY = os.getenv("USE_LLM_SUMMARY", "1").strip().lower() in ["1", "true", "yes", "y"]

# =============================================================================
# EIA API CONFIGURATION
# =============================================================================
EIA_API_KEY = os.getenv("EIA_API_KEY", "").strip()
if not EIA_API_KEY:
    raise RuntimeError("Missing EIA_API_KEY - Get one from https://www.eia.gov/opendata/")

# =============================================================================
# NEWS FILTERING CONFIGURATION
# =============================================================================
WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "48"))
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "30"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ["1", "true", "yes", "y"]
BUBBLES_PER_CAROUSEL = int(os.getenv("BUBBLES_PER_CAROUSEL", "10"))
DEBUG_FILTERING = os.getenv("DEBUG_FILTERING", "1").strip().lower() in ["1", "true", "yes", "y"]

# =============================================================================
# ALLOWED NEWS SOURCES
# =============================================================================
ALLOWED_NEWS_SOURCES = os.getenv("ALLOWED_NEWS_SOURCES", "").strip()
if ALLOWED_NEWS_SOURCES:
    ALLOWED_NEWS_SOURCES_LIST = [s.strip().lower() for s in ALLOWED_NEWS_SOURCES.split(",") if s.strip()]
    print(f"[CONFIG] เลือกเฉพาะเว็บข่าว: {ALLOWED_NEWS_SOURCES_LIST}")
else:
    ALLOWED_NEWS_SOURCES_LIST = []
    print("[CONFIG] รับข่าวจากทุกเว็บข่าว")

# =============================================================================
# STORAGE CONFIGURATION
# =============================================================================
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

# =============================================================================
# WTI PRICE ALERT CONFIGURATION
# =============================================================================
WTI_ALERT_THRESHOLD = float(os.getenv("WTI_ALERT_THRESHOLD", "58.0"))
WTI_ALERT_ENABLED = os.getenv("WTI_ALERT_ENABLED", "1").strip().lower() in ["1", "true", "yes", "y"]
