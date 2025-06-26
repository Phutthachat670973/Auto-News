# ------------------- ส่วนนำเข้า Library -------------------
import feedparser
from datetime import datetime, timedelta
import pytz
import requests
from transformers import pipeline
import re
from bs4 import BeautifulSoup
import os
from dateutil import parser as dateutil_parser
from pathlib import Path
from newspaper import Article

# ------------------- ตั้งค่าโมเดล -------------------
summarizer = pipeline("summarization", model="google/pegasus-xsum")
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

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

# ------------------- ดึงเนื้อหาบทความ -------------------
def extract_full_article(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.text.strip()
    except Exception as e:
        print(f"⚠️ ดึงบทความไม่สำเร็จ (newspaper3k): {e}")
        return ""

# ------------------- หาบทความจาก Google -------------------
def fallback_search_from_google(title):
    try:
        search_url = f"https://www.google.com/search?q={requests.utils.quote(title)}"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a"):
            href = a.get("href", "")
            if "url?q=" in href and not "webcache" in href:
                true_url = re.findall(r"url\?q=(.*?)&", href)
                if true_url:
                    print(f"🔁 Fallback URL: {true_url[0]}")
                    return extract_full_article(true_url[0])
    except Exception as e:
        print(f"❗️ Google fallback failed: {e}")
    return ""

# ------------------- สรุป + แปล -------------------
def summarize_and_translate(title, summary_text):
    text = f"{title}\n{summary_text or ''}".strip()

    try:
        if len(text.split()) < 30:
            summary_en = "[สั้นเกินไป ไม่มีเนื้อหาสำหรับสรุป]"
        else:
            result = summarizer(text, max_length=160, min_length=60, do_sample=False)
            summary_en = result[0]['summary_text'] if result and isinstance(result, list) and 'summary_text' in result[0] else "[สรุปไม่ได้] ไม่มีผลลัพธ์จากโมเดล"
    except (IndexError, ValueError, KeyError) as e:
        summary_en = f"[สรุปไม่ได้] {type(e).__name__}: {e}"
    except Exception as e:
        summary_en = f"[สรุปไม่ได้] Unknown error: {e}"

    # fallback หากสรุปไม่ได้
    if "[สรุปไม่ได้" in summary_en:
        print("🔍 ใช้ fallback หาข่าวจาก Google")
        fallback_text = fallback_search_from_google(title)
        if fallback_text:
            try:
                result = summarizer(fallback_text, max_length=160, min_length=60, do_sample=False)
                summary_en = result[0]['summary_text']
            except Exception as e:
                summary_en = f"[สรุปไม่ได้ (fallback)] {e}"

    try:
        translated = translate_en_to_th(summary_en)
    except Exception as e:
        translated = f"[แปลไม่ได้] {e}"

    if "[สรุปไม่ได้" in summary_en or "[แปลไม่ได้" in translated:
        print("❌ DEBUG: สรุป/แปลไม่ได้")
        print("📰 TITLE:", title)
        print("📄 TEXT:", text[:300].replace("\n", " ") + "...")
        print("📉 SUMMARY:", summary_en)
        print("🌐 TRANSLATED:", translated)

    return translated
# ------------------- ประมวลผล RSS -------------------
def parse_date(entry):
    try:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
        elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            return datetime(*entry.updated_parsed[:6], tzinfo=pytz.utc)
        elif hasattr(entry, 'published'):
            return dateutil_parser.parse(entry.published)
    except:
        return None

def extract_image(entry):
    if hasattr(entry, 'media_content'):
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    if 'img' in getattr(entry, 'summary', ''):
        imgs = re.findall(r'<img[^>]+src="([^"]+)">', entry.summary)
        if imgs:
            return imgs[0]
    try:
        response = requests.get(entry.link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                return og_image["content"]
    except:
        pass
    return "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ------------------- จัดหมวดหมู่ -------------------
candidate_labels = ["Economy", "Energy", "Environment", "Politics", "Technology", "Middle East", "Other"]
def classify_category(entry):
    text = (entry.title + " " + entry.get('summary', '')).strip()
    try:
        result = classifier(text, candidate_labels)
        return result['labels'][0]
    except Exception as e:
        print(f"❗️จัดหมวดหม่ได้: {e}")
        return "Other"

# ------------------- ข่าวจาก Al Jazeera -------------------
def fetch_aljazeera_articles():
    articles = []
    try:
        resp = requests.get("https://www.aljazeera.com/middle-east/", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(resp.content, "html.parser")
        for a in soup.select('a.u-clickable-card__link')[:5]:
            title = a.get_text(strip=True)
            link = "https://www.aljazeera.com" + a['href']
            image = extract_image_from_aljazeera(link)
            articles.append({
                "source": "Al Jazeera",
                "title": title,
                "summary": "",
                "link": link,
                "image": image,
                "published": now_thai,
                "category": "Middle East"
            })
    except Exception as e:
        print(f"⚠️ ดึงข่าว Al Jazeera ไม่สำเร็จ: {e}")
    return articles

def extract_image_from_aljazeera(link):
    try:
        res = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(res.content, "html.parser")
        meta_img = soup.find("meta", property="og:image")
        if meta_img and meta_img.get("content"):
            return meta_img["content"]
    except:
        pass
    return "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"

# ------------------- Flex Message -------------------
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
        "altText": f"ข่าวประจำวันที่ {now_thai.strftime('%d/%m/%Y')}",
        "contents": {"type": "carousel", "contents": bubbles[i:i+10]}
    } for i in range(0, len(bubbles), 10)]

# ------------------- ส่งเข้า LINE -------------------
def send_text_and_flex_to_line(header_text, flex_messages):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }

    res1 = requests.post(url, headers=headers, json={"messages": [{"type": "text", "text": header_text}]})
    print(f"📢 ส่งหัวข้อ: {res1.status_code}, {res1.text}")

    for i, msg in enumerate(flex_messages):
        res2 = requests.post(url, headers=headers, json={"messages": [msg]})
        print(f"📦 ส่ง Flex {i+1}/{len(flex_messages)} ข่าว {len(msg['contents']['contents'])} เรื่อง | {res2.status_code}")

# ------------------- เริ่มต้นหลัก -------------------
cleanup_old_sent_links()

sent_dir = Path("sent_links")
sent_dir.mkdir(exist_ok=True)

today_file = sent_dir / f"{today_thai}.txt"
yesterday_file = sent_dir / f"{yesterday_thai}.txt"

sent_links = set()
for f in [today_file, yesterday_file]:
    if f.exists():
        sent_links.update(f.read_text(encoding="utf-8").splitlines())

all_news = []

# ✅ ดึงจาก RSS และใช้บทความเต็ม
for source, info in news_sources.items():
    if info["type"] == "rss":
        feed = feedparser.parse(info["url"])
        for entry in feed.entries:
            pub_date = parse_date(entry)
            if not pub_date:
                continue
            local_date = pub_date.astimezone(bangkok_tz).date()
            if entry.link in sent_links:
                continue
            if local_date in [today_thai, yesterday_thai]:
                full_article = extract_full_article(entry.link)
                summary_source = full_article if len(full_article) > 200 else getattr(entry, 'summary', '')
                all_news.append({
                    "source": source,
                    "title": entry.title,
                    "summary": summary_source,
                    "link": entry.link,
                    "image": extract_image(entry),
                    "published": pub_date.astimezone(bangkok_tz),
                    "category": classify_category(entry)
                })
                sent_links.add(entry.link)

# ✅ ดึงจาก Al Jazeera (คงไว้)
aljazeera_news = fetch_aljazeera_articles()
for item in aljazeera_news:
    if item["link"] not in sent_links:
        all_news.append(item)
        sent_links.add(item["link"])

# 🔍 กรองตามหมวด
allowed_categories = {"Politics", "Economy", "Energy", "Middle East"}
all_news = [news for news in all_news if news['category'] in allowed_categories]

# ✅ สร้าง Flex แล้วส่ง
if all_news:
    preferred_order = ["Middle East", "Energy", "Politics", "Economy", "Environment", "Technology", "Other"]
    all_news = sorted(all_news, key=lambda x: preferred_order.index(x["category"]) if x["category"] in preferred_order else len(preferred_order))
    flex_messages = create_flex_message(all_news)
    send_text_and_flex_to_line("📊 ข่าวการเมือง เศรษฐกิจ พลังงาน ประจำวันนี้", flex_messages)
    today_file.write_text("\n".join(sorted(sent_links)), encoding="utf-8")
