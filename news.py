# -*- coding: utf-8 -*-
"""
ดึงข่าวช่วง 21:00 เมื่อวาน → 06:00 วันนี้
คัดเฉพาะข่าวที่ "เกี่ยวข้องกับ Upstream Business Group Subsidiary Management Department"
วิเคราะห์ด้วย Gemini (ตอบเป็น JSON) → สรุป/ให้คะแนน/ระบุความเกี่ยวข้องกับงานบริหารบริษัทย่อยสาย Upstream
สร้าง Flex Message และ (เลือก) Broadcast ไป LINE OA

หลักแก้ไขแบบกระทบข่าวน้อยที่สุด:
- เพิ่มตัวดึงข้อความจาก candidates.parts เมื่อ resp.text ว่าง
- ซ่อม/กู้ JSON ที่โดนตัดด้วยตัวช่วยแบบนุ่มนวล (ไม่บังคับมินิฟาย)
- เพิ่ม max_output_tokens ขึ้นเล็กน้อย + retry ด้วย prompt ที่ “ตัด detail” เฉพาะเมื่อจำเป็น
- จำกัดความยาว detail ที่ส่งเข้า LLM (~3500–4000 chars) เพื่อกัน finish_reason=2 โดยไม่กระทบสรุปหลัก
"""

import os, re, json, time, random
from datetime import datetime, timedelta
import feedparser
from dateutil import parser as dateutil_parser
import pytz
from newspaper import Article
import requests
import google.generativeai as genai

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ========================= CONFIG =========================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not GEMINI_API_KEY: raise RuntimeError("ไม่พบ GEMINI_API_KEY ใน Environment/Secrets")
if not LINE_CHANNEL_ACCESS_TOKEN: raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN ใน Environment/Secrets")

genai.configure(api_key=GEMINI_API_KEY)
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

GEMINI_DAILY_BUDGET = int(os.getenv("GEMINI_DAILY_BUDGET", "250"))
MAX_RETRIES = 6
SLEEP_BETWEEN_CALLS = (6.0, 7.0)
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"

bangkok_tz = pytz.timezone("Asia/Bangkok")
now = datetime.now(bangkok_tz)

SENT_LINKS_DIR = "sent_links"
os.makedirs(SENT_LINKS_DIR, exist_ok=True)

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
                    url = line.strip()
                    if url:
                        sent_links.add(url)
    return sent_links

def save_sent_links(new_links, date=None):
    path = get_sent_links_file(date)
    with open(path, "a", encoding="utf-8") as f):
        for url in new_links:
            f.write(url.strip() + "\n")

# ========================= FEEDS =========================
news_sources = {
    "Oilprice": {"type": "rss", "url": "https://oilprice.com/rss/main", "category": "Energy", "site": "Oilprice"},
    "CleanTechnica": {"type": "rss", "url": "https://cleantechnica.com/feed/", "category": "Energy", "site": "CleanTechnica"},
    "HydrogenFuelNews": {"type": "rss", "url": "https://www.hydrogenfuelnews.com/feed/", "category": "Energy", "site": "Hydrogen Fuel News"},
    "Economist-Latest": {"type": "rss", "url": "https://www.economist.com/latest/rss.xml", "category": "Economy", "site": "Economist"},
    "YahooFinance-News": {"type": "rss", "url": "https://finance.yahoo.com/news/rssindex", "category": "Economy", "site": "Yahoo Finance"},
}

DEFAULT_ICON_URL = "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"
UA = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36"}
GEMINI_CALLS = 0

# ========================= CONTEXT (สำหรับหน่วยงาน) =========================
UPSTREAM_SUBSIDIARY_CONTEXT = """
[บทบาทหน่วยงาน]
คุณกำลังช่วยงาน "Upstream Business Group Subsidiary Management Department" 
ซึ่งดูแล/กำกับ "บริษัทย่อย" ในสายธุรกิจสำรวจและผลิตปิโตรเลียม (Upstream: oil & gas E&P) 
โดยเน้นการลงทุน/ธรรมาภิบาล/ความเสี่ยง/ผลการดำเนินงานของบริษัทลูก และความสอดคล้องกับกลยุทธ์กลุ่ม Upstream

[ข่าวที่เกี่ยวข้องกับหน่วยงานนี้]
- ดีลการเข้าซื้อ/ขาย/ร่วมทุน (M&A/JV) ของบริษัทย่อยสาย Upstream หรือสิทธิในแหล่งผลิต
- การค้นพบ/พัฒนา/เริ่มผลิต/หยุดผลิต ของแหล่งน้ำมัน/ก๊าซที่บริษัทลูกถือสิทธิ
- การปรับโครงสร้าง/สรรหา/ธรรมาภิบาล/กฎเกณฑ์ที่กระทบบริษัทลูก
- ภูมิรัฐศาสตร์/ความเสี่ยงประเทศ/ภาษี/สัมปทาน/PSC ที่กระทบบริษัทลูก
- ราคาน้ำมัน/ก๊าซ/JKM/Brent/WTI ที่ส่งผลเชิงกลไกต่อพอร์ต upstream ของบริษัทลูก
- เหตุการณ์ supply disruption ท่อ/แท่น/โรงแยก/ท่าเรือ ที่มีผลต่อการผลิต/ส่งมอบของบริษัทลูก
- ผลประกอบการ/งบลงทุน/re-phasing โครงการ upstream ในบริษัทลูก

[ตัวอย่างสิ่งที่ "ไม่" โฟกัส]
- ข่าวพลังงานปลายน้ำทั่วไปที่ไม่เชื่อมกับบริษัทลูก upstream
- เทคโนโลยี/เมกะเทรนด์ที่ไม่มีผลเชิงกลไกต่อการบริหารบริษัทย่อย upstream
"""

# ========================= HELPERS =========================
def _truncate(s: str, n: int) -> str:  # [PATCH: minimal-impact]
    if not s: return ""
    s = re.sub(r"\s{2,}", " ", s).strip()
    return (s[:n-1] + "…") if len(s) > n else s

def _safe_resp_text(resp) -> str:  # [PATCH: minimal-impact]
    """ดึงข้อความจาก response.text; ถ้าไม่มี ให้รวมจาก candidates.parts"""
    try:
        txt = getattr(resp, "text", None)
        if txt: return txt
    except Exception:
        pass
    parts = []
    try:
        for c in getattr(resp, "candidates", []) or []:
            if getattr(c, "content", None):
                for p in getattr(c.content, "parts", []) or []:
                    t = getattr(p, "text", None)
                    if t: parts.append(t)
    except Exception:
        return ""
    return "\n".join(parts).strip()

def _replace_smart_quotes(s: str) -> str:  # [PATCH: minimal-impact]
    if not s: return s
    trans = {
        '\u201c':'"', '\u201d':'"', '\u201e':'"', '\u201f':'"',
        '\u2018':"'", '\u2019':"'", '\u201a':"'", '\u201b':"'",
        '\u00A0':" ", '\u200B':"", '\u200C':"", '\u200D':"", '\uFEFF':""
    }
    for k,v in trans.items():
        s = s.replace(k,v)
    return s

def _strip_code_fences(s: str) -> str:  # [PATCH: minimal-impact]
    if not s: return s
    s = re.sub(r"^```(?:json)?\s*", "", s.strip(), flags=re.I)
    s = re.sub(r"\s*```$", "", s.strip(), flags=re.I)
    return s

def _balanced_json_substring(s: str) -> str | None:  # [PATCH: minimal-impact]
    """ดึง JSON substring ที่ปิดวงปีกกาได้สมดุล (ช่วยซ่อมกรณีโดนตัด)"""
    if not s: return None
    start = s.find('{')
    if start == -1: return None
    i = start; depth = 0; in_str = False; esc = False
    while i < len(s):
        ch = s[i]
        if in_str:
            if esc: esc = False
            elif ch == '\\': esc = True
            elif ch == '"': in_str = False
        else:
            if ch == '"': in_str = True
            elif ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return s[start:i+1]
        i += 1
    if depth > 0:
        return s[start:] + ("}" * depth)
    return None

def _extract_json_robust(text: str) -> dict | None:  # [PATCH: minimal-impact]
    if not text: return None
    text = _replace_smart_quotes(_strip_code_fences(text))
    cand = _balanced_json_substring(text)
    if not cand: return None
    cand = re.sub(r",\s*([}\]])", r"\1", cand)  # ตัด comma ท้าย
    try:
        return json.loads(cand)
    except Exception:
        return None

# ========================= FETCHERS =========================
def fetch_news_9pm_to_6am(days_back=1):
    now = datetime.now(bangkok_tz)
    start_time = (now - timedelta(days=days_back)).replace(hour=21, minute=0, second=0, microsecond=0)
    end_time   = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if end_time < start_time: end_time += timedelta(days=1)
    print("ช่วง fetch:", start_time, "ถึง", end_time)
    all_news = []
    for _, info in news_sources.items():
        try:
            feed = feedparser.parse(info["url"])
            for entry in feed.entries:
                pub_str = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if not pub_str: continue
                pub_dt = dateutil_parser.parse(pub_str).astimezone(bangkok_tz)
                if not (start_time <= pub_dt <= end_time): continue
                all_news.append({
                    "site": info["site"], "category": info["category"],
                    "title": getattr(entry, "title", "-"),
                    "summary": getattr(entry, "summary", "-"),
                    "link": getattr(entry, "link", ""),
                    "published": pub_dt,
                    "date": pub_dt.strftime("%d/%m/%Y %H:%M")
                })
        except Exception as e:
            print(f"[WARN] อ่านฟีด {info['site']} ล้มเหลว: {e}")
    print("ข่าวที่อยู่ในช่วง:", len(all_news))
    return all_news

def fetch_article_detail_and_image(url, timeout=15):
    # 1) newspaper3k ก่อน
    try:
        art = Article(url); art.download(); art.parse()
        text = (art.text or "").strip()
        img  = (art.top_image or "").strip()
        if text or img: return text, img
    except Exception: pass
    # 2) fallback: requests + meta
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        r.raise_for_status()
        html = r.text
        import lxml.html as LH
        doc = LH.fromstring(html)
        paragraphs = [p.text_content().strip() for p in doc.xpath("//p") if p.text_content()]
        text2 = "\n".join(paragraphs[:60]).strip()  # ไม่ตัดแรง
        og = doc.xpath("//meta[@property='og:image']/@content") or doc.xpath("//meta[@name='twitter:image']/@content")
        img2 = (og[0].strip() if og else "")
        return text2, img2
    except Exception:
        return "", ""

# ========================= GEMINI WRAPPER =========================
# [PATCH: minimal-impact] — เพิ่ม max_output_tokens และตัวอ่าน text แบบปลอดภัย
GENCFG = genai.GenerationConfig(
    temperature=0.35,
    max_output_tokens=1024,  # เพิ่มขึ้นเล็กน้อยเพื่อให้ JSON ปิดครบ
    response_mime_type="application/json"  # ยังขอ JSON เช่นเดิม
)

GEMINI_CALLS_FILE = os.path.join(SENT_LINKS_DIR, f"gemini_calls_{now.strftime('%Y-%m-%d')}.txt")
def _load_calls():
    try: return int(open(GEMINI_CALLS_FILE,"r",encoding="utf-8").read().strip())
    except Exception: return 0
def _save_calls(n):
    try: open(GEMINI_CALLS_FILE,"w",encoding="utf-8").write(str(n))
    except Exception: pass
GEMINI_CALLS = _load_calls()

def _call_and_parse_json(prompt) -> dict:  # [PATCH: minimal-impact]
    """เรียกโมเดล 1 ครั้ง แล้วพยายามกู้ JSON จาก text/parts"""
    resp = model.generate_content(prompt, generation_config=GENCFG)
    raw = _safe_resp_text(resp)
    out = _extract_json_robust(raw)
    if out is not None:
        return out
    # ใส่ดีบักเล็กน้อย (ไม่โยน raw ยาว ๆ)
    fr = None
    try:
        fr = getattr(resp.candidates[0], "finish_reason", None)
    except Exception:
        pass
    raise RuntimeError(f"Gemini ไม่ส่ง JSON ที่ parse ได้ (finish_reason={fr})")

def call_gemini_json(prompt, max_retries=MAX_RETRIES):  # [PATCH: minimal-impact]
    global GEMINI_CALLS
    if GEMINI_CALLS >= GEMINI_DAILY_BUDGET:
        raise RuntimeError(f"ถึงงบ Gemini ประจำวันแล้ว ({GEMINI_CALLS}/{GEMINI_DAILY_BUDGET})")
    last_error = None
    for attempt in range(1, max_retries+1):
        try:
            out = _call_and_parse_json(prompt)
            GEMINI_CALLS += 1
            _save_calls(GEMINI_CALLS)
            return out
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(4 * attempt)
                continue
            raise last_error

# ========================= PROMPTS (เวอร์ชันหน่วยงาน) =========================
def llm_is_relevant_for_department(news):
    # [PATCH: minimal-impact] ตัด detail แคป (~3500) กันโดนตัดเอาต์พุต แต่ไม่กระทบสาระ
    title   = _truncate(news['title'], 300)
    summary = _truncate(news.get('summary',''), 800)
    detail  = _truncate(news.get('detail',''), 3800)

    prompt = f"""
{UPSTREAM_SUBSIDIARY_CONTEXT}

จงตอบ JSON เท่านั้นตาม schema:
{{"relevant": true|false}}

ข่าว:
หัวข้อ: {title}
สรุป: {summary}
เนื้อหาเพิ่มเติม: {detail}
"""
    try:
        if DRY_RUN:
            s = (title + " " + summary).lower()
            keys = ["e&p","exploration","production","oil","gas","brent","wti","lng","field","rig","psc","concession","m&a","acquisition","portfolio","subsidiary","joint venture","supply"]
            return any(k in s for k in keys)
        out = call_gemini_json(prompt)
        return bool(out.get("relevant", False))
    except Exception as e:
        print("[ERROR] LLM Filter:", e)
        return False

def llm_summary_for_department(news):
    """สรุปข่าว + ความเกี่ยวข้องกับหน่วยงาน + คะแนน (JSON) — fallback แบบ 'ลด detail' เฉพาะจำเป็น"""
    title   = _truncate(news['title'], 300)
    summary = _truncate(news.get('summary',''), 800)
    detail  = _truncate(news.get('detail',''), 3800)  # ส่งรอบแรกแบบยาวพอควร (ไม่หั่นสาระ)

    def _make_prompt(_title, _summary, _detail):
        return f"""
{UPSTREAM_SUBSIDIARY_CONTEXT}

ตอบเป็น JSON เท่านั้น:
{{
  "summary": "ไทยสั้น 1–2 ประโยค อธิบายเหตุการณ์หลัก+กลไก (หลีกเลี่ยงศัพท์กว้าง)",
  "importance": 1,
  "importance_reasons": ["รายการสั้นๆ 1-3 ข้อ อธิบายกลไกเชิงธุรกิจ"],
  "department_relevance": "อธิบายว่าข่าวนี้เกี่ยวข้องกับการบริหารบริษัทย่อย upstream อย่างไร (ดีล/โครงสร้าง/ความเสี่ยง/พอร์ต/ผลการดำเนินงาน)",
  "tags": ["เช่น upstream","m&a","psc","geo-risk","price","supply"]
}}

ข้อกำหนด:
- importance ∈ [1,5] และเหตุผลต้องสอดคล้อง
- ตอบเฉพาะ JSON (ห้ามข้อความอื่น/โค้ดบล็อก)

ข่าว:
หัวข้อ: {_title}
สรุปย่อ: {_summary}
เนื้อหาข่าว (ถ้ามี): {_detail}
"""

    if DRY_RUN:
        s = (summary or title)
        return {
            "summary": _truncate(s, 240),
            "importance": 4,
            "importance_reasons": ["ข่าว upstream บริษัทลูก", "มีผลต่อพอร์ต/ความเสี่ยง"],
            "department_relevance": "เกี่ยวข้องกับการกำกับบริษัทย่อย upstream (พอร์ต/ความเสี่ยง/ผลการดำเนินงาน)",
            "tags": ["upstream","portfolio"]
        }

    # รอบ 1: ส่งพร้อม detail แบบย่อ (ยาวพอควร)
    try:
        return call_gemini_json(_make_prompt(title, summary, detail))
    except Exception as e1:
        print("Analyze warn (full detail):", e1)

    # รอบ 2: ลดความยาวโดยตัด detail ออก (ผลกระทบต่อข่าว 'น้อยที่สุด' แต่ช่วยโมเดลปิด JSON)
    try:
        return call_gemini_json(_make_prompt(title, summary, ""))
    except Exception as e2:
        print("Analyze warn (no detail):", e2)

    # รอบสุดท้าย: fallback—ยังคงโครงสร้าง JSON เดิม เพื่อให้ pipeline ไปต่อ
    return {
        "summary": _truncate(summary or title, 240),
        "importance": 3,
        "importance_reasons": ["Fallback: โมเดลไม่ตอบหรือ JSON พัง"],
        "department_relevance": "Fallback: ใช้สรุปย่อชั่วคราวเพื่อส่งงานต่อ",
        "tags": ["fallback"]
    }

# ========================= RANK / FLEX =========================
def rank_candidates(news_list):
    ranked = []
    for n in news_list:
        age_h = (now - n["published"]).total_seconds() / 3600.0
        recency = max(0.0, (72.0 - min(72.0, age_h))) / 72.0 * 3.0
        cat_w = {"Energy": 3.0, "Economy": 2.0}.get(n["category"], 1.0)
        length = min(len(n.get("summary","")) / 500.0, 1.0)
        score = recency + cat_w + length
        ranked.append((score, n))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in ranked]

def create_flex_message(news_items):
    now_thai = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []
    for item in news_items:
        title = (item.get("title","-"))  # ไม่หั่น เพื่อกระทบเนื้อหาน้อยที่สุด
        summary_txt = (item.get("dept_summary") or "ไม่พบสรุปข่าว")
        rel_txt = (item.get("dept_relevance") or "-")
        reasons = item.get("dept_reasons") or []
        score_line = f"คะแนนความสำคัญต่อหน่วยงาน: {item.get('dept_importance','-')}"
        bd_clean = "\n".join([f"- {r}" for r in reasons]) if reasons else "-"

        img = item.get("image") or DEFAULT_ICON_URL
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL

        body_contents = [
            {"type":"text","text": title,"weight":"bold","size":"lg","wrap":True,"color":"#111111"},
            {"type":"box","layout":"horizontal","margin":"sm","contents":[
                {"type":"text","text": f"🗓 {item.get('date','-')}", "size":"xs","color":"#aaaaaa","flex":5},
                {"type":"text","text": f"📌 {item.get('category','')}", "size":"xs","color":"#888888","align":"end","flex":5}
            ]},
            {"type":"text","text": f"🌍 {item.get('site','')}", "size":"xs","color":"#448AFF","margin":"sm"},
            {"type":"text","text": summary_txt,"size":"md","wrap":True,"margin":"md","color":"#1A237E","weight":"bold"},
            {"type":"box","layout":"vertical","margin":"lg","contents":[
                {"type":"text","text":"ความเกี่ยวข้องกับ Upstream Subsidiary Management","weight":"bold","size":"lg","color":"#D32F2F"},
                {"type":"text","text": rel_txt,"size":"md","wrap":True,"color":"#C62828","weight":"bold"},
                {"type":"text","text": score_line,"size":"lg","wrap":True,"color":"#000000","weight":"bold"},
                {"type":"text","text": bd_clean,"size":"sm","wrap":True,"color":"#8E0000","weight":"bold"}
            ]}
        ]

        bubbles.append({
            "type":"bubble","size":"mega",
            "hero":{"type":"image","url":img,"size":"full","aspectRatio":"16:9","aspectMode":"cover"},
            "body":{"type":"box","layout":"vertical","spacing":"md","contents": body_contents},
            "footer":{
                "type":"box","layout":"vertical","spacing":"sm",
                "contents":[
                    {"type":"text","text":"หมายเหตุ: การวิเคราะห์ทั้งหมดอยู่ในช่วงทดสอบ ขออภัยในความไม่สะดวก","size":"xs","color":"#FF0000","wrap":True,"margin":"md"},
                    {"type":"button","style":"primary","color":"#1DB446","action":{"type":"uri","label":"อ่านต่อ","uri": item.get("link","#")}}
                ]
            }
        })

    carousels = []
    for i in range(0, len(bubbles), 10):
        carousels.append({
            "type":"flex",
            "altText": f"ข่าวสำหรับ Upstream Subsidiary Mgmt {now_thai}",
            "contents":{"type":"carousel","contents": bubbles[i:i+10]}
        })
    return carousels

def broadcast_flex_message(access_token, flex_carousels):
    url = 'https://api.line.me/v2/bot/message/broadcast'
    headers = {"Content-Type":"application/json","Authorization": f"Bearer {access_token}"}
    for idx, carousel in enumerate(flex_carousels, 1):
        payload = {"messages":[carousel]}
        if DRY_RUN:
            print(f"[DRY_RUN] จะส่ง Carousel #{idx}: {json.dumps(payload)[:500]}...")
            continue
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        print(f"Broadcast #{idx} status:", resp.status_code, getattr(resp, "text", ""))
        if resp.status_code >= 300:
            print("LINE Error:", resp.status_code, resp.text[:500]); break

# ========================= MAIN =========================
def main():
    all_news = fetch_news_9pm_to_6am()
    print(f"ดึงข่าวช่วง 21:00 เมื่อวาน ถึง 06:00 วันนี้: {len(all_news)} รายการ")
    if not all_news:
        print("ไม่พบข่าว"); return

    SLEEP_MIN, SLEEP_MAX = SLEEP_BETWEEN_CALLS

    # -------- เตรียมรายละเอียด/คัดกรอง --------
    filtered_news = []
    for news in all_news:
        if len(news.get('summary','')) < 50:
            txt, _ = fetch_article_detail_and_image(news['link'])
            # [PATCH: minimal-impact] จำกัดยาวพอควรเพื่อกันตัด แต่ยังเก็บสาระได้ดี
            news['detail'] = _truncate((txt or "").strip() or news['title'], 3800)
        else:
            news['detail'] = ""

        if llm_is_relevant_for_department(news):
            filtered_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ข่าวที่เกี่ยวข้องกับหน่วยงาน: {len(filtered_news)} ข่าว")
    if not filtered_news:
        print("ไม่มีข่าวเกี่ยวข้องหน่วยงาน"); return

    # -------- วิเคราะห์เชิงหน่วยงาน --------
    ranked = rank_candidates(filtered_news)
    top_candidates = ranked[:min(10, len(ranked))]
    print(f"ส่งให้ Gemini วิเคราะห์เพียง {len(top_candidates)} ข่าว (จำกัด 10)")

    dept_news = []
    for news in top_candidates:
        try:
            out = llm_summary_for_department(news)
        except Exception as e:
            print("Error: Analyze:", e)
            continue

        news['dept_summary']    = out.get('summary','ไม่พบสรุปข่าว')
        news['dept_importance'] = int(out.get('importance', 3) or 3)
        news['dept_reasons']    = list(out.get('importance_reasons') or [])
        news['dept_relevance']  = out.get('department_relevance','-')
        news['dept_tags']       = out.get('tags') or []

        dept_news.append(news)
        time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

    print(f"ใช้ Gemini ไปแล้ว: {_load_calls()}/{GEMINI_DAILY_BUDGET} calls")

    if not dept_news:
        print("ไม่พบข่าวที่เหมาะกับหน่วยงานจากตัวเต็ง"); return

    # เรียงตามคะแนนความสำคัญก่อน แล้วตามเวลาเผยแพร่
    dept_news.sort(key=lambda n: (n.get('dept_importance',0), n.get('published', datetime.min)), reverse=True)
    top_news = dept_news[:10]

    # กันส่งซ้ำ
    sent_links = load_sent_links_today_yesterday()
    top_news_to_send = [n for n in top_news if n["link"] not in sent_links]
    if not top_news_to_send:
        print("ข่าววันนี้กับเมื่อวานส่งครบหมดแล้ว ไม่มีข่าวใหม่"); return

    # เติมรูป
    for item in top_news_to_send:
        _, img = fetch_article_detail_and_image(item["link"])
        if not (str(img).startswith("http://") or str(img).startswith("https://")):
            img = DEFAULT_ICON_URL
        item["image"] = img

    # Flex + ส่ง
    carousels = create_flex_message(top_news_to_send)
    broadcast_flex_message(LINE_CHANNEL_ACCESS_TOKEN, carousels)
    save_sent_links([n["link"] for n in top_news_to_send])
    print("เสร็จสิ้น.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[ERROR]", e)
