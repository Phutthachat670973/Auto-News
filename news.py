# -*- coding: utf-8 -*-
"""
สคริปต์: ส่งสรุปข่าวพลังงาน/เศรษฐกิจไปยัง LINE (Flex Message) โดยใช้ฟีด RSS + สรุปด้วย Gemini
— ฉบับฝังพรอมพ์จากรูป "ธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติ" (Upstream & Gas Business)

ไฮไลท์สิ่งที่เปลี่ยน:
1) PTT_CONTEXT ← ครอบคลุม Upstream + Gas (LNG, Gas Separation, NGV, SPP/IPP)
2) llm_ptt_subsidiary_impact_filter() ← ใช้เกณฑ์จากรูปอย่างชัดเจน
3) gemini_summary_and_score() ← บังคับ schema + ให้เหตุผลผูก “กลไกกระทบ” ต่อ PTTEP/PTTLNG/PTTGL/PTTNGD
4) rank_candidates() ← เพิ่ม keyword_boost สำหรับ LNG/ก๊าซ/โรงแยก/NGV/SPP-IPP/FSRU/ท่อก๊าซ ฯลฯ
"""

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

# ========================= Helpers =========================
COLON_RX = re.compile(r"[：﹕꞉︓⦂⸿˸]")
def _normalize_colons(text: str) -> str:
    return COLON_RX.sub(":", text or "")

def _polish_impact_text(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"\((?:[^)]*(?:บวก|ลบ|ไม่ชัดเจน|สั้น|กลาง|ยาว)[^)]*)\)", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*,\s*,", ", ", text)
    text = re.sub(r"\s*,\s*\.", ".", text)
    return text.strip()

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

# ========================= Upstream & Gas Context (จากรูป) =========================
PTT_CONTEXT = """
[PTT Group — Upstream & Natural Gas Business Context (จากเอกสารธุรกิจปิโตรเลียมขั้นต้นและก๊าซธรรมชาติ)]

เป้าหมาย: คัดเลือกและสรุปเฉพาะข่าวที่ "เกี่ยวข้องเชิงสาระสำคัญ" กับ
1) ธุรกิจปิโตรเลียมขั้นต้น (Upstream / PTTEP) — สำรวจและผลิตในไทย/ต่างประเทศ (~50 โครงการใน 12 ประเทศ)
2) ธุรกิจก๊าซธรรมชาติ (PTTLNG, PTTGL, PTTNGD) — ครอบคลุมการจัดหา/นำเข้า LNG, ขนส่ง/ท่อ, โรงแยกก๊าซ, การจัดจำหน่าย,
   NGV (ก๊าซยานยนต์), และการใช้ก๊าซเป็นเชื้อเพลิงสำหรับโรงไฟฟ้า SPP/IPP

ให้ถือว่าข่าวสำคัญเมื่อมี "กลไกกระทบ" ต่อราคาหรือความมั่นคงด้าน supply-demand ของน้ำมัน/ก๊าซ หรือขีดความสามารถของกิจการ
(ผลการผลิต ต้นทุน รายได้ กำไร ความเสี่ยง โอกาสการลงทุน โครงสร้างตลาด/นโยบาย)

ตัวอย่างหัวข้อที่นับเป็น "เกี่ยวข้อง":
• ราคาน้ำมันดิบ Brent/WTI และราคาก๊าซ JKM/TTF/LNG (spot/contract) ผันผวนโดดเด่น
• เหตุขัดข้องหรือเหตุการณ์ในแหล่งผลิต/ท่อส่ง/โรงแยกก๊าซ/คลัง LNG/ท่าเทียบเรือ/FSRU
• นโยบาย ภาษี สัมปทาน/PSC การกำกับดูแล ที่กระทบ upstream และธุรกิจก๊าซ
• การลงทุน/ดีล M&A/FID/ฟาร์มอิน-ฟาร์มเอาท์/การค้นพบเชิงพาณิชย์
• โครงการ LNG terminal, regasification, storage, gas separation plant, NGV, และโรงไฟฟ้า SPP/IPP ที่ใช้ก๊าซ
• ปัจจัยภูมิรัฐศาสตร์/สงคราม/ภัยธรรมชาติที่เปลี่ยนสมดุล supply-demand น้ำมันและก๊าซ

สิ่งที่ "ไม่ใช่":
• ข่าว downstream/PR/EV/การตลาดน้ำมันสำเร็จรูป ที่ไม่เชื่อมโยงสู่กลไก upstream หรือธุรกิจก๊าซของ PTT Group
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
                time.sleep(min(60, 5 * attempt)); continue
            last_error = e
            if attempt < max_retries: time.sleep(3 * attempt)
            else: raise last_error
    raise last_error

# ===== Filter: ใช่/ไม่ใช่ (ฝังคอนเท็กซ์จากรูป) =====
def llm_ptt_subsidiary_impact_filter(news):
    prompt = f'''
{PTT_CONTEXT}

บทบาทของคุณ: News Screener ของ PTT Group (Upstream & Gas)
คำตอบที่อนุญาต: "ใช่" หรือ "ไม่ใช่" เท่านั้น

เกณฑ์ "ใช่":
- Upstream/PTTEP: exploration, appraisal, development, production, reserve, PSC, สัมปทาน, M&A/FID, supply disruption,
  ราคาน้ำมัน/ก๊าซ (Brent, WTI, JKM, TTF, LNG spot/contract)
- Gas/PTTLNG-PTTGL-PTTNGD: LNG import/export/terminal/regas/storage/FSRU, gas separation plant, pipeline/ท่อส่ง,
  distribution/NGV, ก๊าซเชื้อเพลิงสำหรับ SPP/IPP, นโยบายที่กระทบต้นทุน/กำลังผลิต/ความมั่นคง
- ปัจจัยโลก/ภูมิรัฐศาสตร์/ภัยพิบัติที่พลิก supply-demand น้ำมัน/ก๊าซของภูมิภาค/ไทย

เกณฑ์ "ไม่ใช่": downstream/PR/EV หรือตลาดน้ำมันสำเร็จรูปที่ไม่โยงกลไกต่อ upstream/gas

ข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เนื้อหา: {news.get('detail','')}
'''
    try:
        resp = call_gemini(prompt)
        ans = (resp.text or "").strip().replace("\n", "")
        return ans.startswith("ใช่")
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        return False

# ===== Summarize & Score (ยึดกลไกกระทบต่อบริษัทในเครือ) =====
def gemini_summary_and_score(news):
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "score": {"type": "integer"},
            "score_breakdown": {
                "type": "array",
                "items": {"type": "object", "properties": {"points":{"type":"integer"}, "reason":{"type":"string"}}, "required": ["points","reason"]}
            },
            "impact_companies": {"type": "array", "items": {"type": "string", "enum": ["PTTEP","PTTLNG","PTTGL","PTTNGD"]}},
            "impact_reason": {"type": "string"}
        },
        "required": ["summary","score","score_breakdown","impact_companies","impact_reason"]
    }
    prompt = f"""
{PTT_CONTEXT}

บทบาท: Analyst — อธิบายกลไกกระทบแบบกระชับ เน้น upstream/gas ก่อน
อินพุตข่าว:
หัวข้อ: {news['title']}
สรุป: {news['summary']}
เนื้อหา: {news.get('detail','')}

ตอบกลับ **เฉพาะ JSON** ตาม schema ต่อไปนี้:
{json.dumps(schema, ensure_ascii=False)}

ข้อกำหนด:
- summary: ชี้เหตุการณ์ + กลไก (เช่น ท่อก๊าซขัดข้อง → JKM↑ → นำเข้า LNG ไทยต้นทุน↑ (PTTLNG) / Brent↑ → margin PTTEP↑)
- score: 1–5 (ผลกระทบสาระสำคัญต่อ Upstream/Gas เป็นหลัก)
- score_breakdown: รวมแต้ม = score (แจกแจงเหตุผล เช่น supply, price, policy, capacity, FX)
- impact_companies: เลือกไม่เกิน 2 และให้ PTTEP นำถ้าเกี่ยว supply/price/PSC/production
- impact_reason: ชี้ชัด “เพราะอะไร/ช่องทางไหน” (หลีกเลี่ยงคำกว้าง ๆ)
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
        print("[WARN] JSON parse fail:", e)
        return {
            "summary": "ไม่สามารถแปลง JSON ได้",
            "score": 3,
            "score_breakdown": [{"points":2,"reason":"default"},{"points":1,"reason":"fallback"}],
            "impact_companies": [],
            "impact_reason": "-",
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
                    "site": info["site"], "category": info["category"], "title": title,
                    "summary": summary, "link": link, "published": pub_dt,
                    "date": pub_dt.strftime("%d/%m/%Y %H:%M"),
                })
        except Exception as e:
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")

    # de-dup by normalized URL
    seen, uniq = set(), []
    for n in all_news:
        key = _normalize_link(n.get("link", ""))
        if key and key not in seen:
            seen.add(key); uniq.append(n)
    return uniq

# ---- Keyword boost ชี้เฉพาะ “ก๊าซ/LNG/โรงแยก/NGV/SPP-IPP/FSRU/ท่อก๊าซ” ฯลฯ ----
GAS_KEYWORDS = [
    "LNG","JKM","TTF","regas","FSRU","terminal","storage","gas separation","โรงแยกก๊าซ",
    "pipeline","ท่อก๊าซ","NGV","SPP","IPP","methane","นโยบายก๊าซ","gas price","spot cargo",
    "PTTLNG","PTTGL","PTTNGD","PTTEP","PSC","สัมปทาน","production","offshore","well","field",
]
def _kw_boost(text: str) -> float:
    text_low = (text or "").lower()
    hits = sum(1 for k in GAS_KEYWORDS if k.lower() in text_low)
    # cap boost เพื่อไม่ให้แรงเกิน
    return min(hits * 0.35, 2.0)

def rank_candidates(news_list, use_keyword_boost=True):
    ranked = []
    for n in news_list:
        age_h = (now - n["published"]).total_seconds() / 3600.0
        recency = max(0.0, (72.0 - min(72.0, age_h))) / 72.0 * 3.0
        cat_w = {"Energy": 3.0, "Economy": 2.0, "Politics": 1.0}.get(n["category"], 1.0)
        length = min(len(n.get("summary", "")) / 500.0, 1.0)
        boost = _kw_boost(n.get("title","") + " " + n.get("summary","")) if use_keyword_boost else 0.0
        score = recency + cat_w + length + boost
        ranked.append((score, n))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in ranked]

def create_flex_message(news_items):
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    def join_companies(codes):
        codes = codes or []; return ", ".join(codes) if codes else "ไม่มีระบุ"
    bubbles = []
    for item in news_items:
        bd = item.get("score_breakdown", [])
        bd_lines = [f"- {x.get('points', 0)} คะแนน: {x.get('reason', '-')}\n" for x in bd]
        bd_clean = "".join(bd_lines).strip() or "-"
        impact_line = {"type":"text","text":f"กระทบ: {join_companies(item.get('ptt_companies'))}",
                       "size":"xs","color":"#000000","weight":"bold","wrap":True,"margin":"sm"}
        img = item.get("image") or DEFAULT_ICON_URL
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        body_contents = [
            {"type":"text","text":item.get("title","-"),"weight":"bold","size":"lg","wrap":True,"color":"#111111"},
            {"type":"box","layout":"horizontal","margin":"sm","contents":[
                {"type":"text","text":f"🗓 {item.get('date','-')}","size":"xs","color":"#aaaaaa","flex":5},
                {"type":"text","text":f"📌 {item.get('category','')}","size":"xs","color":"#888888","align":"end","flex":5}
            ]},
            {"type":"text","text":f"🌍 {item.get('site','')}","size":"xs","color":"#448AFF","margin":"sm"},
            impact_line,
            {"type":"text","text":item.get("gemini_summary") or "ไม่พบสรุปข่าว","size":"md","wrap":True,"margin":"md","color":"#1A237E","weight":"bold"},
            {"type":"box","layout":"vertical","margin":"lg","contents":[
                {"type":"text","text":"ผลกระทบ / เหตุผลคะแนน","weight":"bold","size":"lg","color":"#D32F2F"},
                {"type":"text","text":(item.get("gemini_reason") or "-"),"size":"md","wrap":True,"color":"#C62828","weight":"bold"},
                {"type":"text","text":f"คะแนนรวม: {item.get('gemini_score','-')} คะแนน","size":"lg","wrap":True,"color":"#000000","weight":"bold"},
                {"type":"text","text":bd_clean,"size":"sm","wrap":True,"color":"#8E0000","weight":"bold"}
            ]}
        ]
        bubble = {
            "type":"bubble","size":"mega",
            "hero":{"type":"image","url":img,"size":"full","aspectRatio":"16:9","aspectMode":"cover"},
            "body":{"type":"box","layout":"vertical","spacing":"md","contents":body_contents},
            "footer":{"type":"box","layout":"vertical","spacing":"sm","contents":[
                {"type":"text","text":"หมายเหตุ: การวิเคราะห์อยู่ในช่วงทดสอบ","size":"xs","color":"#FF0000","wrap":True,"margin":"md"},
                {"type":"button","style":"primary","color":"#1DB446","action":{"type":"uri","label":"อ่านต่อ","uri":item.get("link","#")}}
            ]}
        }
        bubbles.append(bubble)
    carousels = []
    for i in range(0, len(bubbles), 10):
        carousels.append({"type":"flex","altText":f"ข่าวเกี่ยวข้องกับ ปตท. {now_thai}",
                          "contents":{"type":"carousel","contents":bubbles[i:i+10]}})
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
            if resp.status_code >= 300: break
            time.sleep(1.2)
        except Exception as e:
            print("[LINE ERROR]", e); break

# ========================= MAIN =========================
def main():
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว"); return

    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS

    filtered_news = []
    for news in all_news:
        news['detail'] = news['title'] if len((news.get('summary') or '')) < 50 else ''
        if llm_ptt_subsidiary_impact_filter(news):
            filtered_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
    print(f"ข่าวผ่านฟิลเตอร์ (เกี่ยวข้อง Upstream/Gas): {len(filtered_news)} ข่าว")
    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้อง"); return

    ranked = rank_candidates(filtered_news, use_keyword_boost=True)
    top_candidates = ranked[: min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์ {len(top_candidates)} ข่าว")

    ptt_related_news = []
    for news in top_candidates:
        data = gemini_summary_and_score(news)
        news['gemini_summary'] = _normalize_colons(data.get('summary','')).strip() or 'ไม่พบสรุปข่าว'
        score = int(data.get('score', 3)); news['gemini_score'] = max(1, min(5, score))
        bd = data.get('score_breakdown', []); total_points = sum(int(x.get('points',0)) for x in bd)
        if bd and total_points != news['gemini_score']:
            diff = news['gemini_score'] - total_points
            bd[-1]['points'] = int(bd[-1].get('points',0)) + diff
        news['score_breakdown'] = bd
        reason = _polish_impact_text(data.get('impact_reason','').strip()); news['gemini_reason'] = reason or '-'
        companies = [c for c in data.get('impact_companies', []) if c in {"PTTEP","PTTLNG","PTTGL","PTTNGD"}]
        news['ptt_companies'] = list(dict.fromkeys(companies))
        if is_ptt_related_from_output(companies):
            ptt_related_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))
    print(f"ใช้ Gemini ไปแล้ว: {GEMINI_CALLS}/{GEMINI_DAILY_BUDGET} calls")

    if not ptt_related_news:
        print("ไม่พบข่าวที่มีรายชื่อบริษัทในเครือ PTT"); return

    ptt_related_news.sort(key=lambda n: (n.get('gemini_score', 0), n.get('published', datetime.min)), reverse=True)
    top_news = ptt_related_news[:10]

    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if _normalize_link(n.get('link','')) not in sent_links]
    if not top_news_to_send:
        print("ข่าววันนี้/เมื่อวานส่งครบแล้ว"); return

    for item in top_news_to_send:
        img = fetch_article_image(item.get("link","")) or ""
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)
    save_sent_links([n.get("link","") for n in top_news_to_send])
    print("เสร็จสิ้น.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
