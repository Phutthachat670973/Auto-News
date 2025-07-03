import feedparser
from datetime import datetime, timedelta
import pytz
import requests
from transformers import pipeline
from bs4 import BeautifulSoup
import os
import json
from dateutil import parser as dateutil_parser
from newspaper import Article

# ----------- SETUP PIPELINE -----------
summarizer = pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

# ----------- RULE-BASED IMPACT ANALYZER -----------
def impact_analyzer(summary_en, summary_th, category, source):
    keywords_en = [
        "Thailand", "Thai", "Bangkok", "ASEAN", "export", "tourism", "energy", "oil",
        "rice", "rubber", "manufacturing", "supply chain"
    ]
    keywords_th = [
        "ไทย", "อาเซียน", "ส่งออก", "ท่องเที่ยว", "น้ำมัน",
        "ข้าว", "ยางพารา", "อุตสาหกรรม"
    ]
    if any(kw.lower() in summary_en.lower() for kw in keywords_en) or \
       any(kw in summary_th for kw in keywords_th):
        return "ข่าวนี้มีผลกระทบโดยตรงต่อประเทศไทย เช่น ภาคเศรษฐกิจ ความมั่นคง หรือประชาชน กรุณาติดตามข่าวนี้อย่างใกล้ชิด"
    elif category in {"Middle East", "Economy", "Energy", "Politics"} or source in ["BBC Economy", "CNBC"]:
        return (
            "ข่าวนี้ไม่มีผลกระทบโดยตรงกับประเทศไทย แต่เนื้อหาเกี่ยวข้องกับสถานการณ์โลก "
            "เช่น เศรษฐกิจ การเมือง หรือพลังงาน ซึ่งอาจส่งผลต่อไทยในทางอ้อม "
            "เช่น ราคาน้ำมัน การค้าระหว่างประเทศ หรือความมั่นคง โปรดติดตามสถานการณ์อย่างใกล้ชิด"
        )
    else:
        return "ข่าวนี้ไม่มีผลกระทบต่อประเทศไทยเลย ไม่ว่าจะโดยตรงหรือโดยอ้อม"

# ----------- CONFIG -----------
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") or "995e3d74-5184-444b-9fd9-a82a116c55cf:fx"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise ValueError("Missing LINE_CHANNEL_ACCESS_TOKEN.")

bangkok_tz = pytz.timezone("Asia/Bangkok")
now_thai = datetime.now(bangkok_tz)
today_thai = now_thai.date()
yesterday_thai = today_thai - timedelta(days=1)

news_sources = {
    "BBC Economy": {"type": "rss", "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    "CNBC": {"type": "rss", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
}

SENT_FILE = "sent_links.json"
def load_sent_links():
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()
def save_sent_links(sent_links):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent_links), f)

def translate_en_to_th(text):
    url = "https://api-free.deepl.com/v2/translate"
    params = {
        "auth_key": DEEPL_API_KEY,
        "text": text,
        "source_lang": "EN",
        "target_lang": "TH"
    }
    try:
        res = requests.post(url, data=params, timeout=10)
        return res.json()["translations"][0]["text"]
    except Exception as e:
        return f"[แปลไม่ได้] {e}"

def fetch_full_article_text(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.text.strip()
    except Exception as e:
        print(f"⚠️ ไม่สามารถดึงเนื้อหา: {url} | {e}")
        return ""

def summarize_en(text):
    try:
        input_words = text.split()
        input_trimmed = " ".join(input_words[:600])
        token_count = len(input_trimmed.split())
        max_len = max(40, min(200, int(token_count * 0.5)))
        result = summarizer(input_trimmed, max_length=max_len, min_length=40, do_sample=False)
        summary_en = result[0]['summary_text']
        return summary_en
    except Exception as e:
        print(f"❌ Summary Error: {e}")
        return ""

def summarize_and_translate(title, full_text, link=None, category=None, source=None):
    if len(full_text.split()) < 50 and link:
        full_text = fetch_full_article_text(link)
    if not full_text or len(full_text.strip()) < 30:
        return title, "ไม่สามารถดึงเนื้อหาข่าวได้", "ไม่สามารถวิเคราะห์ผลกระทบได้"
    summary_en = summarize_en(full_text)
    title_th = translate_en_to_th(title)
    summary_th = translate_en_to_th(summary_en)
    impact_th = impact_analyzer(summary_en, summary_th, category, source)
    return title_th.strip(), summary_th.strip(), impact_th.strip()

candidate_labels = ["Economy", "Energy", "Environment", "Politics", "Technology", "Middle East", "Other"]
def classify_category(entry):
    try:
        text = entry.title + " " + getattr(entry, 'summary', '')
        return classifier(text, candidate_labels)['labels'][0]
    except:
        return "Other"

def extract_image(entry):
    if hasattr(entry, 'media_content'):
        for media in entry.media_content:
            if 'url' in media:
                return media['url']
    try:
        res = requests.get(entry.link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        og = soup.find("meta", property="og:image")
        return og["content"] if og and og.get("content") else None
    except:
        return None

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
                "summary": fetch_full_article_text(link),
                "link": link,
                "image": image,
                "published": now_thai,
                "category": "Middle East"
            })
    except Exception as e:
        print(f"⚠️ Al Jazeera Error: {e}")
    return articles

def extract_image_from_aljazeera(link):
    try:
        res = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(res.content, "html.parser")
        meta = soup.find("meta", property="og:image")
        return meta["content"] if meta else None
    except:
        return None

def create_flex_message(news_items):
    bubbles = []
    for item in news_items:
        title_th, summary_th, impact_th = summarize_and_translate(
            item['title'],
            item['summary'],
            item['link'],
            item['category'],
            item['source']
        )
        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": item["image"] or "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png",
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": title_th,
                        "weight": "bold",
                        "size": "md",
                        "wrap": True,
                        "margin": "none"
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"🗓 {item['published'].strftime('%d/%m/%Y')}",
                                "size": "xs",
                                "color": "#888888",
                                "flex": 2
                            },
                            {
                                "type": "text",
                                "text": f"📌 {item['category']}",
                                "size": "xs",
                                "color": "#AAAAAA",
                                "align": "end",
                                "flex": 3
                            }
                        ]
                    },
                    {
                        "type": "text",
                        "text": f"📣 {item['source']}",
                        "size": "xs",
                        "color": "#AAAAAA",
                        "margin": "sm"
                    },
                    {
                        "type": "text",
                        "text": summary_th.strip(),
                        "size": "sm",
                        "wrap": True,
                        "margin": "md",
                        "maxLines": 8
                    },
                    {
                        "type": "text",
                        "text": f"🇹🇭 ผลกระทบต่อไทย: {impact_th}",
                        "size": "xs",
                        "color": "#f13e5c",
                        "margin": "md",
                        "wrap": True
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {
                            "type": "uri",
                            "label": "อ่านต่อ",
                            "uri": item['link']
                        }
                    }
                ]
            }
        }
        bubbles.append(bubble)
    return [{
        "type": "flex",
        "altText": f"ข่าวประจำวันที่ {now_thai.strftime('%d/%m/%Y')}",
        "contents": {
            "type": "carousel",
            "contents": bubbles[i:i+10]
        }
    } for i in range(0, len(bubbles), 10)]

def send_text_and_flex_to_line(header_text, flex_messages):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }
    requests.post(url, headers=headers, json={"messages": [{"type": "text", "text": header_text}]})
    for msg in flex_messages:
        requests.post(url, headers=headers, json={"messages": [msg]})

def is_breaking_news(item):
    breaking_keywords = [
        "breaking", "urgent", "emergency", "alert", "exclusive", "just in", "update", "developing", 
        "ด่วน", "ด่วน!", "เหตุการณ์เร่งด่วน"
    ]
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    for kw in breaking_keywords:
        if kw in text:
            return True
    return False

# ------------------ MAIN ------------------
sent_links = load_sent_links()
all_news = []

for source, info in news_sources.items():
    if info["type"] == "rss":
        feed = feedparser.parse(info["url"])
        for entry in feed.entries[:5]:
            pub_date = dateutil_parser.parse(entry.published) if hasattr(entry, "published") else now_thai
            local_date = pub_date.astimezone(bangkok_tz).date()
            if entry.link in sent_links or local_date not in [today_thai, yesterday_thai]:
                continue
            full_text = fetch_full_article_text(entry.link)
            if len(full_text.split()) < 50:
                continue
            category = classify_category(entry)
            all_news.append({
                "source": source,
                "title": entry.title,
                "summary": full_text,
                "link": entry.link,
                "image": extract_image(entry),
                "published": pub_date.astimezone(bangkok_tz),
                "category": category
            })
            sent_links.add(entry.link)

for item in fetch_aljazeera_articles():
    if item["link"] not in sent_links:
        all_news.append(item)
        sent_links.add(item["link"])

# Filter categories you want
allowed_categories = {"Politics", "Economy", "Energy", "Middle East"}
all_news = [n for n in all_news if n["category"] in allowed_categories]

# --- Breaking news ---
breaking_news = [n for n in all_news if is_breaking_news(n)]
normal_news = [n for n in all_news if not is_breaking_news(n)]

if breaking_news:
    flex_msgs = create_flex_message(breaking_news)
    send_text_and_flex_to_line("🚨 ข่าวด่วน! / Breaking News", flex_msgs)

if normal_news:
    order = ["Middle East", "Energy", "Politics", "Economy", "Environment", "Technology", "Other"]
    normal_news.sort(key=lambda x: order.index(x["category"]) if x["category"] in order else len(order))
    flex_msgs = create_flex_message(normal_news)
    send_text_and_flex_to_line("📊 ข่าวการเมือง เศรษฐกิจ พลังงาน ประจำวันนี้", flex_msgs)

save_sent_links(sent_links)
