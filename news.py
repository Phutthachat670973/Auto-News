from __future__ import annotations

import os, re, json, time, random
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qsl, urlencode

import pytz
import requests
import feedparser

# =========================
# ENV
# =========================
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not LINE_CHANNEL_ACCESS_TOKEN: raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN")
if not GROQ_API_KEY: raise RuntimeError("Missing GROQ_API_KEY")

GROQ_MODEL = (os.getenv("GROQ_MODEL_NAME") or "llama-3.1-8b-instant").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (NewsBot/1.0)").strip()
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"

# Google News (แหล่งเดียว)
GOOGLE_NEWS_QUERY = os.getenv("GOOGLE_NEWS_QUERY", "energy OR LNG OR oil OR electricity policy").strip()
GOOGLE_NEWS_HL = os.getenv("GOOGLE_NEWS_HL", "th").strip()
GOOGLE_NEWS_GL = os.getenv("GOOGLE_NEWS_GL", "TH").strip()
GOOGLE_NEWS_CEID = os.getenv("GOOGLE_NEWS_CEID", "TH:th").strip()

SELECT_LIMIT = int(os.getenv("SELECT_LIMIT", "30"))         # ข่าวจาก RSS ที่จะให้ LLM คัด
SEND_LIMIT = int(os.getenv("SEND_LIMIT", "10"))             # bubble ที่ส่งจริง
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "10"))
SLEEP_MIN = float(os.getenv("SLEEP_MIN", "6"))
SLEEP_MAX = float(os.getenv("SLEEP_MAX", "10"))
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "18000"))

DEFAULT_HERO_URL = os.getenv("DEFAULT_HERO_URL", "").strip() or "https://i.imgur.com/4M34hi2.png"
TRACK_DIR = os.getenv("TRACK_DIR", "sent_links").strip()

SEED_NEWS_FILE = os.getenv("SEED_NEWS_FILE", "seed_news.txt").strip()
SEED_NEWS_TEXT = os.getenv("SEED_NEWS_TEXT", "").strip()

bangkok_tz = pytz.timezone("Asia/Bangkok")

# =========================
# Helpers
# =========================
def clean_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def clip(s: str, n: int) -> str:
    s = clean_ws(s)
    return s if len(s) <= n else s[:n] + "…"

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def normalize_url(url: str) -> str:
    try:
        u = (url or "").strip()
        if not u: return u
        p = urlparse(u)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in ("utm_source","utm_medium","utm_campaign","utm_term","utm_content","fbclid","gclid")]
        return urlunparse(p._replace(query=urlencode(q), fragment=""))
    except Exception:
        return (url or "").strip()

def read_seed_text() -> str:
    if SEED_NEWS_TEXT:
        return SEED_NEWS_TEXT
    if os.path.exists(SEED_NEWS_FILE):
        with open(SEED_NEWS_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    return ""

def load_sent_links() -> set:
    ensure_dir(TRACK_DIR)
    fp = os.path.join(TRACK_DIR, "sent_links.txt")
    if not os.path.exists(fp):
        return set()
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return set([x.strip() for x in f if x.strip()])
    except Exception:
        return set()

def save_sent_links(links: List[str]) -> None:
    ensure_dir(TRACK_DIR)
    fp = os.path.join(TRACK_DIR, "sent_links.txt")
    old = load_sent_links()
    new = old.union(set([normalize_url(x) for x in links if x]))
    with open(fp, "w", encoding="utf-8") as f:
        for x in sorted(new):
            f.write(x + "\n")

# =========================
# Groq LLM
# =========================
def _sleep_jitter():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

def groq_chat(prompt: str, temperature: float = 0.25) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role":"system","content":"คุณคือผู้ช่วยคัดกรองข่าวภาษาไทยแบบแม่นยำ ห้ามเดานอกข้อมูล"},
            {"role":"user","content": prompt}
        ],
        "temperature": temperature
    }
    backoff = 2.0
    for attempt in range(MAX_RETRIES):
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            wait = float(ra) if ra else backoff
            print(f"[429] sleep {wait:.1f}s")
            time.sleep(wait)
            backoff = min(backoff*1.8, 35.0)
            continue
        if r.status_code == 413:
            raise requests.HTTPError("413 Payload Too Large", response=r)
        if r.status_code >= 500:
            time.sleep(backoff)
            backoff = min(backoff*1.8, 35.0)
            continue
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    raise RuntimeError("Groq failed after retries")

def parse_json_loose(s: str) -> Optional[Any]:
    try:
        t = s.strip()
        t = re.sub(r"^```(json)?", "", t).strip()
        t = re.sub(r"```$", "", t).strip()
        return json.loads(t)
    except Exception:
        return None

# =========================
# Google News RSS
# =========================
def google_news_rss(query: str) -> str:
    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={GOOGLE_NEWS_HL}&gl={GOOGLE_NEWS_GL}&ceid={GOOGLE_NEWS_CEID}"

def fetch_google_news(limit: int) -> List[Dict[str, Any]]:
    url = google_news_rss(GOOGLE_NEWS_QUERY)
    d = feedparser.parse(url)
    out = []
    for e in d.entries[:max(1, limit)]:
        out.append({
            "title": clip(e.get("title",""), 240),
            "summary": clip(e.get("summary","") or e.get("description",""), 700),
            "link": normalize_url(e.get("link","")),
            "source": "GoogleNews"
        })
    return out

# =========================
# PROMPTS (Seed -> Theme -> Filter)
# =========================
THEME_PROMPT = """
คุณจะได้รับ “ข่าวตัวอย่าง” ที่บอกแนวทางว่าฉันต้องการข่าวประเภทไหน
งานของคุณคือสรุปออกมาเป็น “Theme/Scope” เพื่อใช้คัดข่าวจากแหล่งอื่นให้ได้แนวเดียวกัน

ข้อกำหนด:
- ตอบเป็น bullet 5–7 ข้อ (ขึ้นต้นด้วย "• ")
- เน้น "ชนิดข่าว" ที่ต้องการ (เช่น นโยบายพลังงาน, ค่าไฟ, LNG/ก๊าซ, ความมั่นคงพลังงาน, นิวเคลียร์, ภูมิรัฐศาสตร์, การค้า/ค่าเงินที่เกี่ยวกับต้นทุนพลังงาน)
- ห้ามอ้างชื่อโครงการ ห้ามผูกประเทศใดประเทศหนึ่ง (ต้องใช้ได้กับทุกประเทศ)
- ห้ามเล่าเป็นข่าวรายชิ้น ให้สรุปเป็นแนวทางคัดข่าว

ข่าวตัวอย่าง:
<<<SEED>>>

Theme/Scope:
"""

FILTER_PROMPT = """
ฉันมี Theme/Scope สำหรับคัดข่าว และมีรายการข่าวจาก Google News
ให้คุณคัดเฉพาะข่าวที่ “เข้ากับ Theme/Scope” เท่านั้น โดยไม่ผูกกับประเทศ/โครงการใดประเทศหนึ่ง

ผลลัพธ์ต้องเป็น JSON array เท่านั้น แต่ละรายการต้องมี:
{
  "idx": <ลำดับข่าวเริ่ม 1>,
  "pass": true/false,
  "country": "<ประเทศที่ข่าวกล่าวถึงหลัก ๆ หรือ '-' ถ้าไม่ชัด>",
  "category": "Energy|Politics|Finance|SupplyChain|Other",
  "impact": "• ...",
  "url": "<link>"
}

กติกา impact (รูปแบบต้องเหมือนกันทุกข่าว):
- bullet เดียวขึ้นต้น "• "
- 3 ประโยค:
  1) เหตุการณ์/มาตรการสำคัญจากข่าว (ยึด title/summary)
  2) ผลกระทบในมุม ต้นทุนพลังงาน/ความเสี่ยง/ทิศทางนโยบาย/ความเชื่อมั่น (ห้ามเดา)
  3) สิ่งที่ควรติดตามต่อ (policy/ราคา/ข้อกำกับ/timeline)

Theme/Scope:
<<<THEME>>>

News Items (JSON):
<<<ITEMS>>>
"""

def build_theme(seed_text: str) -> str:
    prompt = THEME_PROMPT.replace("<<<SEED>>>", seed_text)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]
    _sleep_jitter()
    out = groq_chat(prompt, temperature=0.2)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    return "\n".join(lines[:10]).strip()

def filter_by_theme(items: List[Dict[str, Any]], theme: str) -> List[Dict[str, Any]]:
    items_json = json.dumps(items, ensure_ascii=False)
    prompt = FILTER_PROMPT.replace("<<<THEME>>>", theme).replace("<<<ITEMS>>>", items_json)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]
    _sleep_jitter()
    raw = groq_chat(prompt, temperature=0.25)
    js = parse_json_loose(raw)
    return [x for x in js if isinstance(x, dict)] if isinstance(js, list) else []

# =========================
# LINE Flex (Carousel)
# =========================
LINE_BROADCAST = "https://api.line.me/v2/bot/message/broadcast"

def _is_good_image_url(u: str) -> bool:
    return isinstance(u, str) and u.startswith("https://") and len(u) < 1200

def create_flex_carousel(passed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []
    for n in passed:
        title = (n.get("title") or "ข่าว").strip()[:140]
        url = (n.get("url") or n.get("link") or "https://news.google.com/").strip()
        country = (n.get("country") or "-").strip()
        impact = (n.get("impact") or "").strip()
        if impact and not impact.startswith("•"):
            impact = "• " + impact.lstrip("• ").strip()
        img = DEFAULT_HERO_URL
        if not _is_good_image_url(img):
            img = "https://news.google.com/"

        contents = [
            {"type":"text","text":title,"wrap":True,"weight":"bold","size":"lg"},
            {"type":"text","text":country,"size":"sm","color":"#1E90FF","wrap":True,"margin":"sm"},
            {"type":"text","text":"ผลกระทบ (รูปแบบมาตรฐาน)","size":"lg","weight":"bold","margin":"lg"},
            {"type":"text","text":impact or "• (ไม่มีข้อความ)","wrap":True,"size":"md","weight":"bold","margin":"xs"}
        ]

        bubbles.append({
            "type":"bubble",
            "size":"mega",
            "hero":{"type":"image","url":img,"size":"full","aspectRatio":"16:9","aspectMode":"cover"},
            "body":{"type":"box","layout":"vertical","contents":contents},
            "footer":{"type":"box","layout":"vertical","contents":[
                {"type":"button","style":"primary","color":"#1DB446","action":{"type":"uri","label":"อ่านต่อ","uri":url}}
            ]}
        })

    return [{
        "type":"flex",
        "altText": f"Energy News Focus {now_txt}",
        "contents":{"type":"carousel","contents":bubbles}
    }]

def send_to_line(messages: List[Dict[str, Any]]) -> None:
    if DRY_RUN:
        print("[DRY_RUN] sending", len(messages), "messages")
        print(json.dumps(messages, ensure_ascii=False)[:2000])
        return
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}
    r = requests.post(LINE_BROADCAST, headers=headers, json={"messages": messages}, timeout=60)
    if r.status_code >= 400:
        print("[LINE ERROR]", r.status_code, r.text[:800])
        r.raise_for_status()

# =========================
# MAIN
# =========================
def main():
    seed = read_seed_text()
    if not seed:
        raise RuntimeError("ไม่พบ seed ข่าวตัวอย่าง: ใส่ SEED_NEWS_TEXT หรือไฟล์ seed_news.txt")

    print("1) สร้าง Theme/Scope จากข่าวตัวอย่าง...")
    theme = build_theme(seed)
    print("\n[THEME]\n", theme, "\n")

    print("2) ดึงข่าวจาก Google News RSS...")
    raw_items = fetch_google_news(SELECT_LIMIT)
    print("GoogleNews items:", len(raw_items))

    # dedupe ด้วย sent_links
    sent = load_sent_links()
    candidates = []
    for it in raw_items:
        link = it.get("link","")
        if link and link in sent:
            continue
        candidates.append(it)

    if not candidates:
        print("ไม่มีข่าวใหม่")
        return

    print("3) คัดข่าวให้เข้า Theme/Scope ด้วย LLM...")
    tags = filter_by_theme(candidates, theme)

    passed = []
    for it, tg in zip(candidates, tags):
        if not isinstance(tg, dict) or not tg.get("pass"):
            continue
        url = tg.get("url") or it.get("link") or "-"
        if url == "-":
            continue
        passed.append({
            "title": it.get("title",""),
            "url": url,
            "country": tg.get("country","-"),
            "category": tg.get("category","Other"),
            "impact": tg.get("impact",""),
        })

    passed = passed[:SEND_LIMIT]
    if not passed:
        print("ไม่มีข่าวที่เข้า Theme/Scope")
        return

    print("4) ส่ง Flex carousel อย่างเดียว...")
    msgs = create_flex_carousel(passed)
    send_to_line(msgs)

    save_sent_links([p["url"] for p in passed if p.get("url")])
    print("Done")

if __name__ == "__main__":
    main()
