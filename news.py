import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
from dateutil import parser as dateutil_parser
import pytz
import requests
import google.generativeai as genai

# ===== .env =====
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ========================= CONFIG =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not GEMINI_API_KEY:
    raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))
MAX_RETRIES = 6
SLEEP_BETWEEN_CALLS = (6.0, 7.0)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})
TIMEOUT = 15

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

# ========================= Helpers =========================
def _normalize_link(url: str) -> str:
    try:
        p = urlparse(url)
        netloc = p.netloc.lower()
        scheme = (p.scheme or "https").lower()
        bad_keys = {"fbclid", "gclid", "ref", "ref_", "mc_cid", "mc_eid"}
        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            if k.startswith("utm_") or k in bad_keys:
                continue
            q.append((k, v))
        return urlunparse(p._replace(scheme=scheme, netloc=netloc, query=urlencode(q)))
    except Exception:
        return (url or "").strip()

def get_sent_links_file(date=None):
    if date is None:
        date = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    return os.path.join(SENT_LINKS_DIR, f"{date}.txt")

def load_sent_links_today_yesterday():
    sent_links = set()
    for i in range(2):
        date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        path = get_sent_links_file(date)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    url = _normalize_link(line.strip())
                    if url:
                        sent_links.add(url)
    return sent_links

def save_sent_links(new_links, date=None):
    path = get_sent_links_file(date)
    with open(path, "a", encoding="utf-8") as f:
        for url in new_links:
            f.write(_normalize_link(url) + "\n")

def _polish_impact_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*,\s*,", ", ", text)
    text = re.sub(r"\s*,\s*\.", ".", text)
    return text.strip()

# ========================= FEEDS =========================
news_sources = {
    "Oilprice": {"url": "https://oilprice.com/rss/main", "category": "Energy", "site": "Oilprice"},
    "CleanTechnica": {"url": "https://cleantechnica.com/feed/", "category": "Energy", "site": "CleanTechnica"},
    "HydrogenFuelNews": {"url": "https://www.hydrogenfuelnews.com/feed/", "category": "Energy", "site": "Hydrogen Fuel News"},
    "Economist": {"url": "https://www.economist.com/latest/rss.xml", "category": "Economy", "site": "Economist"},
    "YahooFinance": {"url": "https://finance.yahoo.com/news/rssindex", "category": "Economy", "site": "Yahoo Finance"},
}
DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"
GEMINI_CALLS = 0

COLON_RX = re.compile(r"[：﹕꞉︓⦂⸿˸]")
def _normalize_colons(text: str) -> str:
    return COLON_RX.sub(":", text or "")

def fetch_article_image(url: str) -> str:
    try:
        r = S.get(url, timeout=TIMEOUT)
        if r.status_code >= 400:
            return ""
        html = r.text
        m = re.search(r'<meta[^>]+property=[\'\"]og:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m: return m.group(1)
        m = re.search(r'<meta[^>]+name=[\'\"]twitter:image[\'\"][^>]+content=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m: return m.group(1)
        m = re.search(r'<img[^>]+src=[\'\"]([^\'\"]+)[\'\"]', html, re.I)
        if m:
            src = m.group(1)
            if src.startswith("//"):
                parsed = urlparse(url); return f"{parsed.scheme}:{src}"
            if src.startswith("/"):
                parsed = urlparse(url); return f"{parsed.scheme}://{parsed.netloc}{src}"
            return src
        return ""
    except Exception:
        return ""

# ========================= Upstream & Gas Context =========================
PTT_CONTEXT = """
[บริบทธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติของกลุ่ม ปตท. — ฉบับอธิบายง่าย]

ภาพรวม value chain ของกลุ่ม ปตท. ด้านปิโตรเลียมขั้นต้นและก๊าซธรรมชาติเป็นช่วงต่อเนื่องกันดังนี้:

1) แหล่งก๊าซและน้ำมันดิบ
   - ก๊าซธรรมชาติจากแหล่งในประเทศ (เช่น อ่าวไทย)
   - ก๊าซธรรมชาติในรูปของเหลว (LNG) ที่นำเข้าจากต่างประเทศ
   - การสำรวจและผลิต (Upstream) ส่วนใหญ่ดำเนินการโดย PTTEP

2) การขนส่งก๊าซและระบบท่อ
   - ก๊าซจากแหล่งผลิตและจากการนำเข้า LNG จะถูกส่งเข้าสู่ระบบท่อก๊าซธรรมชาติของ ปตท.
   - มีหน่วยควบคุมและจุดควบคุมต่าง ๆ ดูแลความมั่นคงของระบบขนส่งก๊าซ

3) โรงแยกก๊าซธรรมชาติ
   - โรงแยกก๊าซทำหน้าที่แยกก๊าซธรรมชาติออกเป็นส่วนประกอบต่าง ๆ เช่น
     LPG, ก๊าซสำหรับอุตสาหกรรม, feedstock สำหรับปิโตรเคมี และก๊าซเชื้อเพลิง
   - ส่วนนี้เชื่อมโดยตรงกับต้นทุนและความต่อเนื่องของการจัดหาเชื้อเพลิงให้ลูกค้า

4) ลูกค้าปลายทางหลักของก๊าซธรรมชาติ
   - โรงไฟฟ้า (ใช้ก๊าซเป็นเชื้อเพลิงหลัก ทั้ง SPP/IPP)
   - โรงงานอุตสาหกรรมและภาคการผลิตที่ใช้ก๊าซเป็นพลังงานหรือวัตถุดิบ
   - ธุรกิจปิโตรเคมีและน้ำมัน (ใช้น้ำมัน/ก๊าซเป็น feedstock)
   - ระบบก๊าซธรรมชาติสำหรับยานยนต์ (NGV) และภาคขนส่งบางส่วน
   - ลูกค้าที่เกี่ยวข้องกับผลิตภัณฑ์น้ำมันที่เชื่อมโยงกับก๊าซและของเหลวจากโรงแยก

5) บริษัทสำคัญในกลุ่ม ปตท. ที่เกี่ยวข้องกับห่วงโซ่นี้
   - PTTEP      : สำรวจและผลิตปิโตรเลียม (Upstream) ทั้งในและต่างประเทศ
   - PTTLNG     : นำเข้า จัดเก็บ และแปรรูป LNG
   - PTTGL / ระบบท่อก๊าซ : ขนส่งก๊าซผ่านท่อและโครงข่ายก๊าซธรรมชาติ
   - PTTNGD     : การจัดจำหน่ายก๊าซธรรมชาติไปยังลูกค้าอุตสาหกรรม/ผู้ใช้ปลายทาง

[เกณฑ์ใช้คัดกรองว่า "ข่าวเกี่ยวข้องกับธุรกิจนี้หรือไม่"]

ให้ถือว่าข่าว "เกี่ยวข้องอย่างมีนัยสำคัญ" หากมีอย่างน้อยหนึ่งข้อดังนี้:
- กระทบต่อการสำรวจ/ผลิต/ปริมาณสำรอง/กำลังการผลิต หรือโครงสร้างสัญญา (PSC/สัมปทาน) ของ upstream
- กระทบต่อโครงสร้างการจัดหา/นำเข้า/ส่งออก LNG, การเดินเรือ, ท่าเทียบเรือ, FSRU, โรงแยกก๊าซ, ท่อส่งก๊าซ
- กระทบต่อราคาและสมดุล supply–demand ของน้ำมันดิบ (Brent/WTI) หรือก๊าซ/LNG (JKM, TTF, spot/contract) ในระดับภูมิภาคหรือโลก
- เกี่ยวข้องกับนโยบาย ภาษี กฎระเบียบ หรือมาตรการรัฐที่มีผลต่อธุรกิจ Upstream หรือธุรกิจก๊าซของกลุ่ม ปตท.
- เหตุขัดข้อง เหตุฉุกเฉิน ภัยพิบัติ หรือเหตุภูมิรัฐศาสตร์ที่อาจทำให้ supply หายไป ชะงัก หรือต้นทุนพุ่งขึ้น
- การลงทุน/ดีล M&A/FID/โครงการท่อก๊าซ/โรงแยก/คลัง LNG/โรงไฟฟ้าใหม่ ที่เปลี่ยนกำลังผลิตหรือบทบาทในตลาด

ให้ตัดข่าวออก ("ไม่ใช่") เมื่อ:
- เป็นข่าวการตลาด downstream, โปรโมชั่นน้ำมันสำเร็จรูป, EV หรือเรื่องภาพลักษณ์ทั่วไป
  ที่ไม่เชื่อมโยงชัดเจนกับ supply–demand หรือความสามารถในการจัดหาก๊าซ/น้ำมันของกลุ่ม ปตท.
"""

# ========================= Gemini Wrapper =========================
def call_gemini(prompt, max_retries=MAX_RETRIES):
    global GEMINI_CALLS
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError(f"ถึงงบ Gemini ประจำวันแล้ว ({GEMINI_CALLS}/{GEMINI_DAILY_BUDGET})")
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = model.generate_content(prompt)
            GEMINI_CALLS += 1
            return resp
        except Exception as e:
            err_str = str(e)
            if attempt < max_retries and any(x in err_str for x in ["429","exhausted","temporarily","unavailable","deadline","500","503"]):
                time.sleep(min(60, 5 * attempt))
                continue
            last_error = e
            if attempt < max_retries:
                time.sleep(3 * attempt)
            else:
                raise last_error
    raise last_error

# ===== Filter: ใช่/ไม่ใช่ =====
def llm_ptt_subsidiary_impact_filter(news):
    prompt = f'''
{PTT_CONTEXT}

บทบาทของคุณ: ทำหน้าที่เป็น "News Screener" ของกลุ่ม ปตท. ด้านปิโตรเลียมขั้นต้นและก๊าซธรรมชาติ

คำตอบที่อนุญาตมีแค่ 2 คำเท่านั้น:
- "ใช่"    = ข่าวนี้เกี่ยวข้องเชิงสาระสำคัญกับ Upstream หรือธุรกิจก๊าซของกลุ่ม ปตท.
- "ไม่ใช่" = ข่าวนี้เป็น downstream/PR/เรื่องทั่วไปที่ไม่โยงมาถึง upstream/gas ตามเกณฑ์ด้านบน

ข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เนื้อหาเพิ่มเติม: {news.get('detail','')}

ให้ตอบสั้น ๆ เพียงคำเดียว: "ใช่" หรือ "ไม่ใช่"
'''
    try:
        resp = call_gemini(prompt)
        ans = (resp.text or "").strip().replace("\n", "")
        return ans.startswith("ใช่")
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        return False

# ===== Tag ข่าว: สรุป + บริษัท / ประเด็น / ภูมิภาค =====
def gemini_tag_news(news):
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "impact_companies": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]
                }
            },
            "topic_type": {
                "type": "string",
                "enum": [
                    "supply_disruption",
                    "price_move",
                    "policy",
                    "investment",
                    "geopolitics",
                    "other"
                ]
            },
            "region": {
                "type": "string",
                "enum": [
                    "global",
                    "asia",
                    "europe",
                    "middle_east",
                    "us",
                    "other"
                ]
            },
            "impact_reason": {"type": "string"}
        },
        "required": ["summary", "impact_companies", "topic_type", "region", "impact_reason"]
    }

    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ของกลุ่ม ปตท. (เฉพาะธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติ)
งานของคุณคือ "สรุปข่าว และติดแท็ก" เพื่อให้ระบบสามารถเลือกข่าวให้ครอบคลุมตามห่วงโซ่ธุรกิจจาก
แหล่งก๊าซ → ระบบท่อ → โรงแยกก๊าซ → ลูกค้าโรงไฟฟ้า/อุตสาหกรรม/NGV ฯลฯ

อินพุตข่าว:
หัวข้อ: {news['title']}
สรุป (จาก RSS): {news['summary']}
เนื้อหาเพิ่มเติม: {news.get('detail','')}

ให้ตอบกลับเป็น JSON ตาม schema นี้เท่านั้น:
{json.dumps(schema, ensure_ascii=False)}

คำอธิบาย field:
- summary:
  • เขียนสั้น ๆ แต่ให้บอกชัดว่าข่าวนี้เกิดอะไร ที่ไหน เกี่ยวกับก๊าซ/น้ำมัน/โครงสร้างพื้นฐานอย่างไร
- impact_companies:
  • เลือกจาก ["PTTEP","PTTLNG","PTTGL","PTTNGD"]
  • เลือกได้ 0–2 บริษัท ถ้าไม่เกี่ยวกับบริษัทใดเลย ให้ส่งเป็น [] (array ว่าง)
- topic_type:
  • supply_disruption = การหยุดชะงัก/ลดลงของ supply (ท่อ, แหล่งผลิต, โรงแยก, LNG, คลัง ฯลฯ)
  • price_move        = ข่าวราคาน้ำมัน/ราคาก๊าซ/ราคา LNG ขยับขึ้นหรือลงแบบมีนัย
  • policy            = นโยบายรัฐ, ภาษี, กฎหมาย, มาตรการกำกับดูแลที่กระทบ Upstream/Gas
  • investment        = โครงการลงทุน, FID, M&A, การขยายกำลังผลิต, โครงการใหม่
  • geopolitics       = ภูมิรัฐศาสตร์, สงคราม, ความขัดแย้งระหว่างประเทศที่กระทบตลาดพลังงาน
  • other             = ข่าวที่เกี่ยวกับ Upstream/Gas แต่ไม่เข้าหมวดด้านบน
- region:
  • global, asia, europe, middle_east, us, other
  • ให้เลือก region หลักที่เกี่ยวข้องกับเหตุการณ์ในข่าว
- impact_reason:
  • เขียนอธิบาย 1–3 ประโยคว่า ข่าวนี้จะไปกระทบห่วงโซ่ธุรกิจของกลุ่ม ปตท. อย่างไร
  • ถ้า impact_companies เป็น [] ให้เขียนในเชิงภาพรวมตลาด/ราคา

ห้ามใส่คำอธิบายอื่นนอกจาก JSON ตาม schema ข้างต้น
"""

    try:
        resp = call_gemini(prompt)
        raw = (resp.text or "").strip()

        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()

        data = json.loads(raw)
        return data

    except Exception as e:
        print("[WARN] JSON parse fail in gemini_tag_news:", e)
        return {
            "summary": news.get("summary") or news.get("title") or "ไม่สามารถสรุปข่าวได้",
            "impact_companies": [],
            "topic_type": "other",
            "region": "other",
            "impact_reason": "fallback – ใช้สรุปจาก RSS แทน"
        }

# ========================= Logic =========================
def is_ptt_related_from_output(impact_companies) -> bool:
    return bool(impact_companies)

def fetch_news_9pm_to_6am():
    now_local = datetime.now(bangkok_tz)
    start_time = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end_time = now_local.replace(hour=6, minute=0, second=0, microsecond=0)

    all_news = []
    for _, info in news_sources.items():
        try:
            feed = feedparser.parse(info["url"])
            for entry in feed.entries:
                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if not pub_str and getattr(entry, "published_parsed", None):
                    t = entry.published_parsed
                    pub_dt = datetime(*t[:6], tzinfo=pytz.UTC).astimezone(bangkok_tz)
                else:
                    if not pub_str:
                        continue
                    pub_dt = dateutil_parser.parse(pub_str)
                    if pub_dt.tzinfo is None:
                        pub_dt = pytz.UTC.localize(pub_dt)
                    pub_dt = pub_dt.astimezone(bangkok_tz)

                if not (start_time <= pub_dt <= end_time):
                    continue

                summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                link = getattr(entry, "link", "")
                title = getattr(entry, "title", "-")

                all_news.append({
                    "site": info["site"],
                    "category": info["category"],
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": pub_dt,
                    "date": pub_dt.strftime("%d/%m/%Y %H:%M"),
                })
        except Exception as e:
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")

    # de-dup by normalized URL
    seen, uniq = set(), []
    for n in all_news:
        key = _normalize_link(n.get("link", ""))
        if key and key not in seen:
            seen.add(key)
            uniq.append(n)
    return uniq

# --------- Coverage-first selection ----------
KEY_COMPANIES = ["PTTEP", "PTTLNG", "PTTGL", "PTTNGD"]
KEY_TOPICS = ["supply_disruption", "price_move", "policy", "investment", "geopolitics"]

def select_news_coverage_first(news_list, max_items=10):
    """
    เลือกข่าว/กลุ่มข่าวโดยเน้นความครอบคลุม:
    1) พยายามให้มีข่าวของทุกบริษัทหลักเท่าที่เป็นไปได้
    2) พยายามให้มีทุก topic_type สำคัญเท่าที่เป็นไปได้
    3) ที่เหลือเติมด้วยข่าวใหม่สุด (published ใหม่สุดก่อน)
    """
    if not news_list:
        return []

    sorted_news = sorted(news_list, key=lambda n: n.get("published"), reverse=True)

    selected = []
    used_ids = set()

    def _add_if_not_selected(candidate):
        key = _normalize_link(candidate.get("link", "")) or id(candidate)
        if key in used_ids:
            return False
        if len(selected) >= max_items:
            return False
        selected.append(candidate)
        used_ids.add(key)
        return True

    # รอบที่ 1: ครอบคลุมบริษัท
    for comp in KEY_COMPANIES:
        if len(selected) >= max_items:
            break
        for n in sorted_news:
            companies = n.get("ptt_companies") or []
            if comp in companies:
                if _add_if_not_selected(n):
                    break

    # รอบที่ 2: ครอบคลุม topic_type
    for topic in KEY_TOPICS:
        if len(selected) >= max_items:
            break
        if any((x.get("topic_type") == topic) for x in selected):
            continue
        for n in sorted_news:
            if n.get("topic_type") == topic:
                if _add_if_not_selected(n):
                    break

    # รอบที่ 3: เติมด้วยข่าวใหม่สุด
    for n in sorted_news:
        if len(selected) >= max_items:
            break
        _add_if_not_selected(n)

    return selected

# --------- Grouping ข่าวตาม topic + region ----------
def group_related_news(news_list, min_group_size=3):
    """
    รับข่าวที่ tag แล้ว (มี topic_type, region)
    คืน list ที่มีทั้ง:
      - single ข่าวปกติ (dict เดิม)
      - group ข่าว (dict ใหม่ is_group=True และมี news_items เป็น list ข่าวย่อย)
    """
    buckets = {}
    for n in news_list:
        key = (n.get("topic_type", "other"), n.get("region", "other"))
        buckets.setdefault(key, []).append(n)

    grouped_items = []

    for (topic, region), items in buckets.items():
        if len(items) >= min_group_size:
            all_companies = []
            for it in items:
                all_companies.extend(it.get("ptt_companies") or [])
            all_companies = list(dict.fromkeys(all_companies))

            items_sorted = sorted(items, key=lambda x: x.get("published"), reverse=True)
            anchor = items_sorted[0]

            group_obj = {
                "is_group": True,
                "topic_type": topic,
                "region": region,
                "ptt_companies": all_companies,
                "news_items": items_sorted,

                "title": anchor.get("title", "-"),
                "site": "หลายแหล่งข่าว",
                "category": anchor.get("category", ""),
                "date": anchor.get("date", ""),
                "published": anchor.get("published"),
                "link": anchor.get("link", ""),
            }
            grouped_items.append(group_obj)
        else:
            grouped_items.extend(items)

    return grouped_items

def gemini_summarize_group(group):
    """
    สร้าง meta-summary สำหรับกลุ่มข่าว (เช่น geopolitics + middle_east ที่มีหลายข่าว)
    """
    items = group.get("news_items", [])
    if not items:
        return {
            "summary": "ไม่พบข่าวในกลุ่ม",
            "impact_reason": "-"
        }

    lines = []
    for idx, n in enumerate(items, 1):
        line = f"{idx}. {n.get('title','-')} — {n.get('summary','')}"
        lines.append(line)
    news_block = "\n".join(lines)

    prompt = f"""
{PTT_CONTEXT}

บทบาทของคุณ: Analyst ที่ต้องสรุป "ภาพรวม" ของชุดข่าวหลายข่าวที่พูดถึงประเด็นเดียวกัน
จุดประสงค์: ทำให้ผู้บริหารอ่าน bubble เดียวแล้วเข้าใจว่า เกิดอะไรขึ้นในประเด็นนี้ โดยไม่ต้องอ่านทุกข่าวย่อย

กลุ่มข่าว (หัวข้อและสรุปย่อย):
{news_block}

ให้ตอบกลับเป็น JSON รูปแบบ:
{{
  "summary": "<สรุปภาพรวมของทั้งกลุ่ม 3–5 ประโยค>",
  "impact_reason": "<อธิบายสั้น ๆ ว่ากลุ่มข่าวนี้กระทบกลุ่ม ปตท. อย่างไร โดยโยงกับ upstream/gas value chain>"
}}

ห้ามตอบคำอธิบายอื่น นอกจาก JSON ตามรูปแบบข้างต้น
"""

    try:
        resp = call_gemini(prompt)
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(json)?", "", raw).strip()
            raw = re.sub(r"```$", "", raw).strip()
        data = json.loads(raw)
        return data
    except Exception as e:
        print("[WARN] JSON parse fail in gemini_summarize_group:", e)
        return {
            "summary": "ไม่สามารถสรุปภาพรวมของกลุ่มข่าวได้",
            "impact_reason": "-"
        }

# --------- Labels สำหรับ Flex ---------
TOPIC_LABELS_TH = {
    "supply_disruption": "Supply ขัดข้อง/ลดลง",
    "price_move": "ราคาน้ำมัน/ก๊าซเปลี่ยน",
    "policy": "นโยบาย/กฎหมาย",
    "investment": "โครงการลงทุน/M&A",
    "geopolitics": "ภูมิรัฐศาสตร์/สงคราม",
    "other": "อื่น ๆ ที่เกี่ยวกับ Upstream/ก๊าซ",
}
REGION_LABELS_TH = {
    "global": "Global",
    "asia": "Asia",
    "europe": "Europe",
    "middle_east": "Middle East",
    "us": "US",
    "other": "อื่น ๆ",
}

def create_flex_message(news_items):
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")

    def join_companies(codes):
        codes = codes or []
        return ", ".join(codes) if codes else "ไม่มีระบุ"

    bubbles = []
    for item in news_items:
        img = item.get("image") or DEFAULT_ICON_URL
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL

        topic_key = item.get("topic_type", "other")
        region_key = item.get("region", "other")
        topic_label = TOPIC_LABELS_TH.get(topic_key, "อื่น ๆ")
        region_label = REGION_LABELS_TH.get(region_key, "อื่น ๆ")

        impact_line = {
            "type": "text",
            "text": f"กระทบ: {join_companies(item.get('ptt_companies'))}",
            "size": "xs",
            "color": "#000000",
            "weight": "bold",
            "wrap": True,
            "margin": "sm"
        }

        meta_line = {
            "type": "text",
            "text": f"ประเภท: {topic_label} | ภูมิภาค: {region_label}",
            "size": "xs",
            "color": "#555555",
            "wrap": True,
            "margin": "sm"
        }

        # ถ้าเป็นกลุ่มข่าว → แสดง list ข่าวย่อย
        group_sublist_box = None
        if item.get("is_group"):
            sub_items = item.get("news_items", [])[:5]
            sub_lines = []
            for sub in sub_items:
                line = f"• [{sub.get('site','')}] {sub.get('title','-')}"
                sub_lines.append(line)
            sub_text = "\n".join(sub_lines) if sub_lines else "-"

            group_sublist_box = {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "ข่าวย่อยในกลุ่มนี้:",
                        "size": "xs",
                        "weight": "bold",
                        "color": "#000000",
                        "wrap": True
                    },
                    {
                        "type": "text",
                        "text": sub_text,
                        "size": "xs",
                        "color": "#444444",
                        "wrap": True
                    }
                ]
            }

        title_text = item.get("title", "-")
        if item.get("is_group"):
            count_sub = len(item.get("news_items", []))
            title_text = f"{topic_label} ({region_label}) – {count_sub} ข่าวสำคัญ"

        body_contents = [
            {
                "type": "text",
                "text": title_text,
                "weight": "bold",
                "size": "lg",
                "wrap": True,
                "color": "#111111"
            },
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": f"🗓 {item.get('date','-')}",
                        "size": "xs",
                        "color": "#aaaaaa",
                        "flex": 5
                    },
                    {
                        "type": "text",
                        "text": f"📌 {item.get('category','')}",
                        "size": "xs",
                        "color": "#888888",
                        "align": "end",
                        "flex": 5
                    }
                ]
            },
            {
                "type": "text",
                "text": f"🌍 {item.get('site','')}",
                "size": "xs",
                "color": "#448AFF",
                "margin": "sm"
            },
            impact_line,
            meta_line,
            {
                "type": "text",
                "text": item.get("gemini_summary") or "ไม่พบสรุปข่าว",
                "size": "md",
                "wrap": True,
                "margin": "md",
                "color": "#1A237E",
                "weight": "bold"
            },
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "contents": [
                    {
                        "type": "text",
                        "text": "ผลกระทบต่อกลุ่ม ปตท.",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#D32F2F"
                    },
                    {
                        "type": "text",
                        "text": (item.get("gemini_reason") or "-"),
                        "size": "md",
                        "wrap": True,
                        "color": "#C62828",
                        "weight": "bold"
                    },
                ]
            }
        ]

        if group_sublist_box:
            body_contents.append(group_sublist_box)

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": img,
                "size": "full",
                "aspectRatio": "16:9",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": body_contents
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": "หมายเหตุ: การวิเคราะห์อยู่ในช่วงทดสอบ",
                        "size": "xs",
                        "color": "#FF0000",
                        "wrap": True,
                        "margin": "md"
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#1DB446",
                        "action": {
                            "type": "uri",
                            "label": "อ่านต่อ",
                            "uri": item.get("link", "#")
                        }
                    }
                ]
            }
        }
        bubbles.append(bubble)

    carousels = []
    for i in range(0, len(bubbles), 10):
        carousels.append({
            "type": "flex",
            "altText": f"ข่าวเกี่ยวข้องกับ ปตท. {now_thai}",
            "contents": {
                "type": "carousel",
                "contents": bubbles[i:i+10]
            }
        })
    return carousels

def broadcast_flex_message(access_token, flex_carousels):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    for idx, carousel in enumerate(flex_carousels, 1):
        payload = {"messages": [carousel]}
        if DRY_RUN:
            print(f"[DRY_RUN] Carousel #{idx}: {json.dumps(payload)[:500]}...")
            continue
        try:
            resp = S.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            print(f"Broadcast #{idx} status:", resp.status_code, getattr(resp, "text", ""))
            if resp.status_code >= 300:
                break
            time.sleep(1.2)
        except Exception as e:
            print("[LINE ERROR]", e)
            break

# ========================= MAIN =========================
def main():
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว")
        return

    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS

    # 1) Filter: เลือกข่าวที่เกี่ยวข้องกับ Upstream/Gas
    filtered_news = []
    for news in all_news:
        news['detail'] = news['title'] if len((news.get('summary') or '')) < 50 else ''
        if llm_ptt_subsidiary_impact_filter(news):
            filtered_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ข่าวผ่านฟิลเตอร์ (เกี่ยวข้อง Upstream/Gas): {len(filtered_news)} ข่าว")
    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้อง")
        return

    # 2) Tagging: สรุป + tag บริษัท/ประเด็น/ภูมิภาค
    tagged_news = []
    print(f"ส่งให้ Gemini ติดแท็ก {len(filtered_news)} ข่าว")
    for news in filtered_news:
        tag = gemini_tag_news(news)

        news['gemini_summary'] = _normalize_colons(tag.get('summary', '')).strip() or 'ไม่พบสรุปข่าว'
        companies = [c for c in (tag.get('impact_companies') or []) if c in {"PTTEP", "PTTLNG", "PTTGL", "PTTNGD"}]
        news['ptt_companies'] = list(dict.fromkeys(companies))
        news['topic_type'] = tag.get('topic_type', 'other')
        news['region'] = tag.get('region', 'other')
        news['gemini_reason'] = _polish_impact_text(tag.get('impact_reason', '').strip()) or '-'

        if is_ptt_related_from_output(news['ptt_companies']):
            tagged_news.append(news)

        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")
    if not tagged_news:
        print("ไม่พบข่าวที่ผูกกับบริษัทในเครือ PTT โดยตรง")
        return

    # 3) Grouping: ยุบข่าวที่ topic+region เดียวกัน (ถ้ามี >= 3 ข่าว)
    collapsed_list = group_related_news(tagged_news, min_group_size=3)

    # 4) ทำ meta-summary สำหรับกลุ่มข่าว
    for item in collapsed_list:
        if item.get("is_group"):
            data = gemini_summarize_group(item)
            item["gemini_summary"] = _normalize_colons(data.get("summary", "")).strip()
            item["gemini_reason"] = _polish_impact_text(data.get("impact_reason", "").strip() or "-")

    # 5) เลือกชุดข่าว/กลุ่มข่าวแบบ coverage-first (ไม่เกิน 10 bubble)
    top_news = select_news_coverage_first(collapsed_list, max_items=10)

    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if _normalize_link(n.get('link', '')) not in sent_links]
    if not top_news_to_send:
        print("ข่าววันนี้/เมื่อวานส่งครบแล้ว")
        return

    # 6) ดึงรูปข่าว
    for item in top_news_to_send:
        img = fetch_article_image(item.get("link", "")) or ""
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    # 7) ส่งเข้า LINE
    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)
    save_sent_links([n.get("link", "") for n in top_news_to_send])
    print("เสร็จสิ้น.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
