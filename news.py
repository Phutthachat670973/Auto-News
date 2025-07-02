import feedparser
from datetime import datetime, timedelta
import pytz
import requests
from transformers import pipeline
from bs4 import BeautifulSoup
import os
from dateutil import parser as dateutil_parser
from newspaper import Article

# ----------------- LOAD PIPELINES -----------------
summarizer = pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")
impact_llm = pipeline("text-generation", model="microsoft/phi-3-mini-4k-instruct", max_new_tokens=120)

# ----------------- CONFIG -----------------
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") or "YOUR_DEEPL_API_KEY"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or "YOUR_LINE_ACCESS_TOKEN"
bangkok_tz = pytz.timezone("Asia/Bangkok")
now_thai = datetime.now(bangkok_tz)
today_thai = now_thai.date()
yesterday_thai = today_thai - timedelta(days=1)

news_sources = {
    "BBC Economy": {"type": "rss", "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    "CNBC": {"type": "rss", "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
}

# ----------------- HELPER FUNCTIONS -----------------
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

def impact_analyzer_llm(summary_th):
    prompt = (
        "จงวิเคราะห์ข่าวต่อไปนี้และสรุปเป็นภาษาไทย: ข่าวนี้มีผลกระทบต่อประเทศไทยหรือไม่ ทั้งทางตรงหรืออ้อม "
        "ถ้าเป็นข่าวระดับโลกที่อาจกระทบเศรษฐกิจหรือพลังงาน ให้ชี้แจงด้วย ถ้าไม่มีผลกระทบเลยให้ตอบว่า 'ไม่มีผลกระทบโดยตรง'\n\n"
        f"ข่าว: {summary_th}\n\n"
        "ผลกระทบต่อประเทศไทย:"
    )
    result = impact_llm(prompt)[0]['generated_text']
    answer = result.split("ผลกระทบต่อประเทศไทย:")[-1].strip()
    if answer == "" or answer.startswith("ข่าว:"):
        answer = "ไม่สามารถวิเคราะห์ผลกระทบได้"
    return answer

def summarize_and_translate(title, full_text, link=None):
    if len(full_text.split()) < 50 and link:
        full_text = fetch_full_article_text(link)
    if not full_text or len(full_text.strip()) < 30:
        return title, "ไม่สามารถดึงเนื้อหาข่าวได้", "ไม่สามารถวิเคราะห์ผลกระทบได้"
    summary_en = summarize_en(full_text)
    title_th = translate_en_to_th(title)
    summary_th = translate_en_to_th(summary_en)
    impact_th = impact_analyzer_llm(summary_th)
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
        title_th, summary_th, impact_th = summarize_and_translate(item['title'], item['summary'], item['link'])
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

# ------------------ MAIN WORKFLOW ------------------
sent_links = set()
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
            all_news.append({
                "source": source,
                "title": entry.title,
                "summary": full_text,
                "link": entry.link,
                "image": extract_image(entry),
                "published": pub_date.astimezone(bangkok_tz),
                "category": classify_category(entry)
            })
            sent_links.add(entry.link)

for item in fetch_aljazeera_articles():
    if item["link"] not in sent_links:
        all_news.append(item)
        sent_links.add(item["link"])

allowed_categories = {"Politics", "Economy", "Energy", "Middle East"}
all_news = [n for n in all_news if n["category"] in allowed_categories]

if all_news:
    order = ["Middle East", "Energy", "Politics", "Economy", "Environment", "Technology", "Other"]
    all_news.sort(key=lambda x: order.index(x["category"]) if x["category"] in order else len(order))
    flex_msgs = create_flex_message(all_news)
    send_text_and_flex_to_line("📊 ข่าวการเมือง เศรษฐกิจ พลังงาน ประจำวันนี้", flex_msgs)
