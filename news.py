# ------------------- ส่วนนำเข้า Library -------------------
import feedparser
from datetime import datetime, timedelta
import pytz
import requests
from transformers import pipeline, AutoTokenizer
import re
from bs4 import BeautifulSoup
import os
from dateutil import parser as dateutil_parser
from pathlib import Path

# ------------------- ตั้งค่าโมเดล -------------------
summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")
tokenizer = AutoTokenizer.from_pretrained("facebook/bart-large-cnn")

# ------------------- ตั้งค่า API -------------------
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") or "995e3d74-5184-444b-9fd9-a82a116c55cf:fx"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise ValueError("Missing LINE_CHANNEL_ACCESS_TOKEN. Please set it as an environment variable.")

# ------------------- ตั้งค่า Timezone -------------------
bangkok_tz = pytz.timezone("Asia/Bangkok")
now_thai = datetime.now(bangkok_tz)
today_thai = now_thai.date()
yesterday_thai = today_thai - timedelta(days=1)

# ------------------- ฟังก์ชันลบไฟล์ข่าวเก่า -------------------
def cleanup_old_sent_links(folder="sent_links", keep_days=5):
    cutoff_date = today_thai - timedelta(days=keep_days)
    if not os.path.exists(folder):
        return
    for filename in os.listdir(folder):
        if filename.endswith(".txt"):
            try:
                file_date = datetime.strptime(filename.replace(".txt", ""), "%Y-%m-%d").date()
                if file_date < cutoff_date:
                    os.remove(os.path.join(folder, filename))
                    print(f"🪝 ลบไฟล์ข่าวเก่า: {filename}")
            except Exception as e:
                print(f"⚠️ ไม่สามารรมผล {filename}: {e}")

# ------------------- แหล่งข่าว -------------------
news_sources = {
    "BBC Economy": {"type": "rss", "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    "CNBC": {"type": "rss", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
}

# ------------------- คำค้นหาหลัก -------------------
keywords = ["economy", "gdp", "inflation", "energy", "oil", "gas", "climate", "carbon", "power", "electricity", "emissions"]

# ------------------- แปลภาษา -------------------
def translate_en_to_th(text):
    url = "https://api-free.deepl.com/v2/translate"
    params = {
        "auth_key": DEEPL_API_KEY,
        "text": text,
        "source_lang": "EN",
        "target_lang": "TH"
    }
    try:
        response = requests.post(url, data=params, timeout=10)
        response.raise_for_status()
        result = response.json()
        return result["translations"][0]["text"]
    except Exception as e:
        return f"แปลไม่สำเร็จ: {e}"

# ------------------- ดึงเนื้อหาข่าวเต็มจากเว็บ -------------------
def fetch_full_article_text(link):
    try:
        res = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            paragraphs = soup.find_all('p')
            full_text = "\n".join(p.get_text() for p in paragraphs if p.get_text())
            return full_text.strip()
    except Exception as e:
        print(f"⚠️ ไม่สามารถึงเนื้อหาเต็มจาก {link}: {e}")
    return ""

# ------------------- จำกัดความยาวเนื้อหาสำหรับสรุป -------------------
def clip_text(text, max_words=500):
    words = text.split()
    return " ".join(words[:max_words])

# ------------------- ฟังก์ชันสรุป + แปล -------------------
def summarize_and_translate(title, summary_text, link=None):
    if (not summary_text or len(summary_text.split()) < 100) and link:
        summary_text = fetch_full_article_text(link)

    raw_text = f"{title}\n{clip_text(summary_text)}"
    tokens = tokenizer.encode(raw_text, truncation=True, max_length=1024)

    try:
        if len(tokens) < 50:
            summary_en = raw_text
        else:
            result = summarizer(raw_text, max_length=200, min_length=40, do_sample=False)
            summary_en = result[0]['summary_text']
    except Exception as e:
        print(f"⚠️ สรุปข่าวไม่ได้: {e}")
        summary_en = raw_text

    try:
        translated = translate_en_to_th(summary_en)
    except Exception as e:
        translated = f"[แปลไม่ได้] {e}"

    translated = translated.replace("<n>", "\n").strip()

    if "\n" in translated:
        title_th, summary_th = translated.split("\n", 1)
    else:
        title_th = title
        summary_th = translated

    return title_th, summary_th
