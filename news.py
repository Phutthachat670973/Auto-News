# ------------------- ส่วนนำเข้า Library -------------------
import feedparser
from datetime import datetime, timedelta
import pytz
import requests
from transformers import pipeline
import re
from bs4 import BeautifulSoup
from collections import Counter
import os
from dateutil import parser as dateutil_parser

# ------------------- โมเดลวิเคราะห์ข่าว และจัดหมวดหมู่ -------------------
summarizer = pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

# ------------------- DeepL Translate -------------------
DEEPL_API_KEY = "995e3d74-5184-444b-9fd9-a82a116c55cf:fx"  # 🔑 แทนที่ด้วย API Key ของคุณ

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

# ------------------- ตั้งค่า Timezone -------------------
bangkok_tz = pytz.timezone("Asia/Bangkok")
now_thai = datetime.now(bangkok_tz)
today_thai = now_thai.date()
yesterday_thai = today_thai - timedelta(days=1)

# ------------------- Line Channel Token -------------------
LINE_CHANNEL_ACCESS_TOKEN = 'tI3xxzlIq2sD6pg1ukIabWAnuxxoCgc68Bv0vDcvHZNCUnUYGk15EafVqLi3A6pDlyBiUwECDzwxLHtwzIfpoieIO5BIWVRHtfVa7uIy9XYuWwZpybcV/UmwOvhxySqTb4wOXdKRX8Gpo9N91VIOzAdB04t89/1O/w1cDnyilFU='

# ------------------- RSS URLs -------------------
feed_urls_filtered = {
    "BBC Economy": "http://feeds.bbci.co.uk/news/business/economy/rss.xml",
    "CNBC": "https://www.cnbc.com/id/15839135/device/rss/rss.html",
    "NYT": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml"
}

keywords = [
    "economy", "economic", "recession", "inflation", "deflation", "gdp", "interest rate",
    "fiscal policy", "monetary policy", "stimulus", "unemployment", "debt", "deficit", "growth",
    "macroeconomics", "financial crisis", "energy", "oil", "gas", "natural gas", "crude", "power",
    "electricity", "renewable", "solar", "wind", "nuclear", "hydropower", "geothermal", "fuel",
    "petroleum", "coal", "biofuel", "emissions", "carbon", "carbon footprint", "energy market",
    "energy price", "energy policy", "energy crisis", "energy transition", "green energy",
    "clean energy", "fossil fuels", "climate", "net zero"
]

def parse_date(entry):
    try:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            return datetime(*entry.updated_parsed[:6], tzinfo=pytz.utc)
        elif hasattr(entry, 'published') and entry.published:
            return dateutil_parser.parse(entry.published)
    except:
        return None
    return None

def is_relevant(entry):
    text = (entry.title + " " + getattr(entry, 'summary', "")).lower()
    return any(k in text for k in keywords)

def extract_image(entry):
    if hasattr(entry, 'media_content'):
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    if 'img' in getattr(entry, 'summary', ''):
        imgs = re.findall(r'<img[^>]+src="([^">]+)"', entry.summary)
        if imgs:
            return imgs[0]
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(entry.link, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                return og_image["content"]
    except Exception as e:
        print(f"โหลดรูปไม่สำเร็จ: {e}")
    return "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

def summarize_and_translate(title, summary):
    text = f"วิเคราะห์ข่าวนี้:\n\n{title}\n{summary}"
    try:
        result = summarizer(text, max_length=100, min_length=20, do_sample=False)
        english_summary = result[0]['summary_text']
        return translate_en_to_th(english_summary)
    except Exception as e:
        return f"วิเคราะห์ไม่ได้: {e}"

candidate_labels = ["Economy", "Energy", "Environment", "Politics", "Technology", "Other"]
category_mapping = {
    "Oil": "Energy",
    "Gas": "Energy",
    "Renewable": "Energy",
    "Economy": "Economy",
    "Energy": "Energy",
    "Environment": "Environment",
    "Politics": "Politics",
    "Technology": "Technology"
}

def classify_category(entry):
    text = (entry.title + " " + getattr(entry, 'summary', "")).strip()
    try:
        result = classifier(text, candidate_labels + list(category_mapping.keys()))
        best_label = result['labels'][0]
        return category_mapping.get(best_label, best_label if best_label in candidate_labels else "Other")
    except Exception as e:
        print(f"❗️จัดหมวดหมู่ไม่ได้: {e}")
        return "Other"

def create_flex_message(news_items):
    bubbles = []
    for item in news_items:
        summary_th = summarize_and_translate(item['title'], item['summary'])
        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": item.get("image", ""),
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": item['title'], "weight": "bold", "size": "md", "wrap": True},
                    {"type": "text", "text": f"🗓 {item['published'].strftime('%d/%m/%Y')}", "size": "xs", "color": "#888888", "margin": "sm"},
                    {"type": "text", "text": f"📌 {item['category']}", "size": "xs", "color": "#AAAAAA", "margin": "xs"},
                    {"type": "text", "text": f"📣 {item['source']}", "size": "xs", "color": "#AAAAAA", "margin": "xs"},
                    {"type": "text", "text": summary_th, "size": "sm", "wrap": True, "margin": "md"},
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {"type": "uri", "label": "อ่านต่อ", "uri": item['link']}
                    }
                ]
            }
        }
        if bubble["hero"]["url"].startswith("http"):
            bubbles.append(bubble)

    return [ {
        "type": "flex",
        "altText": "ข่าวเศรษฐกิจและพลังงาน",  
        "contents": {"type": "carousel", "contents": bubbles[i:i+10]}
    } for i in range(0, len(bubbles), 10) ]

def send_text_and_flex_to_line(header_text, flex_messages):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }

    safe_header_text = header_text
    text_payload = {"messages": [{"type": "text", "text": safe_header_text}]}
    res1 = requests.post(url, headers=headers, json=text_payload)
    print(f"📢 ส่งหัวข้อ: {res1.status_code}, {res1.text}")

    for i, msg in enumerate(flex_messages):
        print(f"📦 ส่ง Flex {i+1}/{len(flex_messages)} ข่าว {len(msg['contents']['contents'])} เรื่อง")
        res2 = requests.post(url, headers=headers, json={"messages": [msg]})
        print(f"LINE Response: {res2.status_code}, {res2.text}")

# ------------------- ป้องกันข่าวซ้ำ -------------------
sent_file = "sent_links.txt"
if os.path.exists(sent_file):
    with open(sent_file, "r", encoding="utf-8") as f:
        sent_links = set(f.read().splitlines())
else:
    sent_links = set()

# ------------------- ดึงข่าวจากทุกแหล่ง -------------------
all_news = []

for source, url in feed_urls_filtered.items():
    print(f"🌐 โหลดฟีดจาก: {source}")
    feed = feedparser.parse(url)
    print(f"🔎 {source} พบ {len(feed.entries)} ข่าว")

    for entry in feed.entries:
        pub_date = parse_date(entry)
        if not pub_date:
            print(f"⛔️ {source} - ไม่มีวันที่")
            continue
        local_date = pub_date.astimezone(bangkok_tz).date()

        print(f"🔍 {source} | {entry.title[:60]}... | วันที่: {local_date}")

        if entry.link in sent_links:
            print("⏩ ข่าวนี้ส่งไปแล้ว")
            continue

        if source in ["BBC Economy", "NYT"]:
            if local_date in [today_thai, yesterday_thai]:
                print("✅ เก็บข่าว BBC/NYT")
                all_news.append({
                    "source": source,
                    "title": entry.title,
                    "summary": getattr(entry, 'summary', ''),
                    "link": entry.link,
                    "image": extract_image(entry),
                    "published": pub_date.astimezone(bangkok_tz),
                    "category": classify_category(entry)
                })
                sent_links.add(entry.link)
        else:
            if local_date in [today_thai, yesterday_thai] and is_relevant(entry):
                print("✅ เก็บข่าวที่เกี่ยวข้อง")
                all_news.append({
                    "source": source,
                    "title": entry.title,
                    "summary": getattr(entry, 'summary', ''),
                    "link": entry.link,
                    "image": extract_image(entry),
                    "published": pub_date.astimezone(bangkok_tz),
                    "category": classify_category(entry)
                })
                sent_links.add(entry.link)

# ------------------- ส่งข่าว -------------------
if all_news:
    preferred_order = ["Energy", "Politics", "Economy", "Environment", "Tecnology", "Other"]
    all_news = sorted(all_news, key=lambda item: preferred_order.index(item["category"]) if item["category"] in preferred_order else len(preferred_order))
    flex_messages = create_flex_message(all_news)
    send_text_and_flex_to_line("📊 ข่าวเศรษฐกิจและพลังงานประจำวันที่วันนี้", flex_messages)

# ------------------- บันทึกลิงก์ที่ส่งแล้ว -------------------
with open(sent_file, "w", encoding="utf-8") as f:
    f.write("\n".join(sent_links))
