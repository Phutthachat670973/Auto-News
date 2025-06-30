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
summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

# ------------------- ตั้งค่า API -------------------
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY") or "995e3d74-5184-444b-9fd9-a82a116c55cf:fx"
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise ValueError("Missing LINE_CHANNEL_ACCESS_TOKEN.")

# ------------------- ตั้งค่า Timezone -------------------
bangkok_tz = pytz.timezone("Asia/Bangkok")
now_thai = datetime.now(bangkok_tz)
today_thai = now_thai.date()
yesterday_thai = today_thai - timedelta(days=1)

# ------------------- ลบไฟล์ข่าวเก่า -------------------
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
            except:
                continue

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
        res = requests.post(url, data=params, timeout=10)
        return res.json()["translations"][0]["text"]
    except Exception as e:
        return f"[แปลไม่ได้] {e}"

# ------------------- ดึงเนื้อหาข่าวจากหน้าเว็บ -------------------
def fetch_full_article_text(url):
    try:
        article = Article(url)
        article.download()
        article.parse()
        return article.text.strip()
    except Exception as e:
        print(f"⚠️ ไม่สามารถดึงเนื้อหา: {url} | {e}")
        return ""

# ------------------- ฟังก์ชันวิเคราะห์ผลกระทบระดับโลก -------------------
def analyze_impact(summary_en):
    prompt = f"""
    Based on the following article summary, identify one country or region that is most directly affected. If no country or region is clearly mentioned, respond with "Country: None".

    Then summarize the impact briefly.

    Output format:
    Country: <country name or 'None'>
    Impact: <1 sentence impact summary>

    Article summary:
    {summary_en}
    """
    try:
        result = summarizer(prompt, max_length=100, min_length=30, do_sample=False)
        return result[0]['summary_text']
    except:
        return ""

# ------------------- ฟังก์ชันสรุป + แปล + วิเคราะห์ผลกระทบ -------------------
def summarize_and_translate(title, full_text, link=None):
    if len(full_text.split()) < 50 and link:
        full_text = fetch_full_article_text(link)

    if not full_text or len(full_text.strip()) < 30:
        return title, "ไม่สามารถดึงเนื้อหาข่าวได้", ""

    input_words = full_text.split()
    input_trimmed = " ".join(input_words[:600])

    try:
        token_count = len(input_trimmed.split())
        max_len = max(40, min(200, int(token_count * 0.5)))
        result = summarizer(input_trimmed, max_length=max_len, min_length=40, do_sample=False)
        summary_en = result[0]['summary_text']
    except Exception as e:
        summary_en = f"{title}\nเนื้อหาบทความไม่สามารถสรุปได้อัตโนมัติ โปรดคลิกลิงก์เพื่ออ่านเพิ่มเติม"

    try:
        translated = translate_en_to_th(summary_en)
    except Exception as e:
        translated = f"[แปลไม่ได้] {e}"

    try:
        impact_en = analyze_impact(summary_en)
        impact_th = translate_en_to_th(impact_en) if impact_en else ""
    except:
        impact_th = ""

    translated = translated.replace("<n>", "\n").strip()
    if "\n" in translated:
        title_th, summary_th = translated.split("\n", 1)
    else:
        title_th, summary_th = title, translated

    return title_th.strip(), summary_th.strip(), impact_th.strip()


# ------------------- จัดหมวดหมู่ -------------------
candidate_labels = ["Economy", "Energy", "Environment", "Politics", "Technology", "Middle East", "Other"]
def classify_category(entry):
    try:
        text = entry.title + " " + getattr(entry, 'summary', '')
        return classifier(text, candidate_labels)['labels'][0]
    except:
        return "Other"

# ------------------- ดึงภาพข่าว -------------------
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

# ------------------- ดึงข่าวจาก Al Jazeera -------------------
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

# ------------------- Flex Message -------------------
# ------------------- Flex Message -------------------
def create_flex_message(news_items):
    bubbles = []
    for item in news_items:
        title_th, summary_th, impact_th = summarize_and_translate(item['title'], item['summary'], item.get('link'))

        affected_area = ""
        impact_detail = ""
        if "Country:" in impact_th and "Impact:" in impact_th:
            try:
                parts = impact_th.split("Country:", 1)[1].strip()
                affected_area, impact_detail = parts.split("Impact:", 1)
            except:
                affected_area = ""
                impact_detail = impact_th.strip()
        else:
            impact_detail = impact_th.strip()

        bubble_contents = [
            {"type": "text", "text": title_th, "weight": "bold", "size": "md", "wrap": True},
            {"type": "text", "text": f"🗓 {item['published'].strftime('%d/%m/%Y')}", "size": "xs", "color": "#888888", "margin": "sm"},
            {"type": "text", "text": f"📌 {item['category']}", "size": "xs", "color": "#AAAAAA", "margin": "xs"},
            {"type": "text", "text": f"📣 {item['source']}", "size": "xs", "color": "#AAAAAA", "margin": "xs"},
        ]

        if affected_area.strip() and affected_area.strip().lower() != "none":
            bubble_contents.append({"type": "text", "text": f"🌍 ประเทศ/ภูมิภาคที่ได้รับผลกระทบ: {affected_area.strip()}", "size": "xs", "color": "#888888", "wrap": True, "margin": "sm"})
        if impact_detail.strip():
            bubble_contents.append({"type": "text", "text": "📉 ผลกระทบที่เกิดขึ้น:", "size": "xs", "color": "#888888", "wrap": True, "margin": "xs"})
            bubble_contents.append({"type": "text", "text": impact_detail.strip(), "size": "xs", "color": "#444444", "wrap": True, "margin": "xs"})

        bubble_contents.append({"type": "text", "text": summary_th.strip(), "size": "sm", "wrap": True, "margin": "md"})

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": item.get("image", "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"),
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": bubble_contents
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

    return [{
        "type": "flex",
        "altText": f"ข่าวประจำวันที่ {now_thai.strftime('%d/%m/%Y')}",
        "contents": {
            "type": "carousel",
            "contents": bubbles[i:i+10]
        }
    } for i in range(0, len(bubbles), 10)]


# ------------------- ส่งเข้า LINE -------------------
def send_text_and_flex_to_line(header_text, flex_messages):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'
    }
    requests.post(url, headers=headers, json={"messages": [{"type": "text", "text": header_text}]})
    for msg in flex_messages:
        requests.post(url, headers=headers, json={"messages": [msg]})

# ------------------- เริ่มต้น -------------------
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
for source, info in news_sources.items():
    if info["type"] == "rss":
        feed = feedparser.parse(info["url"])
        for entry in feed.entries:
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

all_news += [item for item in fetch_aljazeera_articles() if item["link"] not in sent_links]

allowed_categories = {"Politics", "Economy", "Energy", "Middle East"}
all_news = [n for n in all_news if n["category"] in allowed_categories]

if all_news:
    order = ["Middle East", "Energy", "Politics", "Economy", "Environment", "Technology", "Other"]
    all_news.sort(key=lambda x: order.index(x["category"]) if x["category"] in order else len(order))
    flex_msgs = create_flex_message(all_news)
    send_text_and_flex_to_line("📊 ข่าวการเมือง เศรษฐกิจ พลังงาน ประจำวันนี้", flex_msgs)
    today_file.write_text("\n".join(sorted(sent_links)), encoding="utf-8")
