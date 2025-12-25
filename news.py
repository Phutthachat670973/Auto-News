from __future__ import annotations

import os
import re
import json
import time
import random
from typing import Any, Dict, List, Optional
from datetime import datetime
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qsl, urlencode

import pytz
import requests
import feedparser


# =====================================================================================
# ENV
# =====================================================================================

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN")
if not GROQ_API_KEY:
    raise RuntimeError("Missing GROQ_API_KEY")

GROQ_MODEL = (os.getenv("GROQ_MODEL_NAME") or "llama-3.1-8b-instant").strip()
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

USER_AGENT = os.getenv("USER_AGENT", "Mozilla/5.0 (NewsBot/1.0)").strip()
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() == "true"

# Google News (แหล่งเดียว)
GOOGLE_NEWS_QUERY = os.getenv(
    "GOOGLE_NEWS_QUERY",
    'energy OR LNG OR oil OR "electricity tariff" OR nuclear OR sanctions OR geopolitics'
).strip()
GOOGLE_NEWS_HL = os.getenv("GOOGLE_NEWS_HL", "en").strip()
GOOGLE_NEWS_GL = os.getenv("GOOGLE_NEWS_GL", "US").strip()
GOOGLE_NEWS_CEID = os.getenv("GOOGLE_NEWS_CEID", "US:en").strip()

# ปริมาณข่าว
SELECT_LIMIT = int(os.getenv("SELECT_LIMIT", "40"))
SEND_LIMIT = int(os.getenv("SEND_LIMIT", "10"))

# กัน 429
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "10"))
SLEEP_MIN = float(os.getenv("SLEEP_MIN", "6"))
SLEEP_MAX = float(os.getenv("SLEEP_MAX", "10"))

# กัน 413
THEME_BATCH_SIZE = int(os.getenv("THEME_BATCH_SIZE", "6"))
SUMMARY_CLIP = int(os.getenv("SUMMARY_CLIP", "220"))
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "17000"))

DEFAULT_HERO_URL = os.getenv("DEFAULT_HERO_URL", "").strip() or "https://i.imgur.com/4M34hi2.png"

# tracking
TRACK_DIR = os.getenv("TRACK_DIR", "sent_links").strip()

# seed (ข่าวตัวอย่าง)
SEED_NEWS_FILE = os.getenv("SEED_NEWS_FILE", "seed_news.txt").strip()
SEED_NEWS_TEXT = os.getenv("SEED_NEWS_TEXT", "").strip()

# รายชื่อโครงการ/ประเทศ/ผู้ร่วมทุน (ใช้เป็น “เงื่อนไข”)
PROJECT_LIST_FILE = os.getenv("PROJECT_LIST_FILE", "project_list.txt").strip()
PARTNER_LIST_FILE = os.getenv("PARTNER_LIST_FILE", "partner_list.txt").strip()

# คะแนน
SCORE_THEME_MIN = int(os.getenv("SCORE_THEME_MIN", "60"))      # ธีมขั้นต่ำ
SCORE_STRICT = int(os.getenv("SCORE_STRICT", "70"))            # ธีมสูง ส่งแน่
SCORE_RELAXED = int(os.getenv("SCORE_RELAXED", "50"))          # ธีมพอเกี่ยว ส่งได้ถ้ามี project/partner
BONUS_HIT = int(os.getenv("BONUS_HIT", "10"))                  # โบนัสถ้าพบชื่อ project/partner

bangkok_tz = pytz.timezone("Asia/Bangkok")

S = requests.Session()
S.headers.update({"User-Agent": USER_AGENT})


# =====================================================================================
# Helpers
# =====================================================================================

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
        if not u:
            return u
        p = urlparse(u)
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in ("utm_source","utm_medium","utm_campaign","utm_term","utm_content",
                                  "fbclid","gclid","mc_cid","mc_eid")]
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

def load_list_file(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t and not t.startswith("#"):
                out.append(t)
    # uniq (case-insensitive)
    seen = set()
    uniq = []
    for x in out:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq

PROJECT_NAMES = load_list_file(PROJECT_LIST_FILE)
PARTNER_NAMES = load_list_file(PARTNER_LIST_FILE)

def find_hits(text: str, names: List[str]) -> List[str]:
    text_l = text.lower()
    hits = []
    for n in names:
        if n.lower() in text_l:
            hits.append(n)
    return hits

def _sleep_jitter():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


# =====================================================================================
# Groq LLM
# =====================================================================================

def groq_chat(prompt: str, temperature: float = 0.25) -> str:
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "คุณคือผู้ช่วยคัดกรองข่าวแบบแม่นยำ ห้ามเดานอกข้อมูลที่ให้"},
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature
    }

    backoff = 2.0
    for attempt in range(MAX_RETRIES):
        r = S.post(GROQ_URL, headers=headers, json=payload, timeout=60)

        if r.status_code == 429:
            ra = r.headers.get("retry-after")
            try:
                wait = float(ra) if ra else backoff
            except Exception:
                wait = backoff
            print(f"[429] rate limited -> sleep {wait:.1f}s (attempt {attempt+1}/{MAX_RETRIES})")
            time.sleep(wait)
            backoff = min(backoff * 1.8, 35.0)
            continue

        if r.status_code == 413:
            raise requests.HTTPError("413 Payload Too Large", response=r)

        if r.status_code >= 500:
            print(f"[{r.status_code}] server error -> sleep {backoff:.1f}s")
            time.sleep(backoff)
            backoff = min(backoff * 1.8, 35.0)
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


# =====================================================================================
# Google News RSS
# =====================================================================================

def google_news_rss(query: str) -> str:
    q = quote_plus(query)
    return f"https://news.google.com/rss/search?q={q}&hl={GOOGLE_NEWS_HL}&gl={GOOGLE_NEWS_GL}&ceid={GOOGLE_NEWS_CEID}"

def fetch_google_news(limit: int) -> List[Dict[str, Any]]:
    url = google_news_rss(GOOGLE_NEWS_QUERY)
    d = feedparser.parse(url)
    out = []
    for e in d.entries[:max(1, limit)]:
        out.append({
            "title": clip(e.get("title", ""), 240),
            "summary": clip(e.get("summary", "") or e.get("description", ""), 900),
            "link": normalize_url(e.get("link", "")),
            "source": "GoogleNews"
        })
    return out


# =====================================================================================
# PROMPTS
# =====================================================================================

THEME_PROMPT = """
คุณจะได้รับ “ข่าวตัวอย่าง” เพื่อกำหนดแนวทางคัดข่าว
ให้สรุปเป็น Theme/Scope แบบใช้คัดข่าวได้จริง และต้องครอบคลุมข่าวพลังงานสากล

ข้อกำหนด:
- ตอบเป็น bullet 7–10 ข้อ (ขึ้นต้นด้วย "• ")
- ต้องมีอย่างน้อย 1 bullet ที่มีคำเหล่านี้ (อย่างน้อย 3 คำ): oil, crude, Brent, WTI, OPEC, sanctions, refinery
- ต้องมีอย่างน้อย 1 bullet ที่มีคำ: LNG, natural gas, gas
- ห้ามผูกประเทศใดประเทศหนึ่ง (ใช้ได้กับทุกประเทศ)
- ห้ามเล่าเป็นข่าวรายชิ้น ให้สรุปเป็น “เกณฑ์/แนวทางคัด”

ข่าวตัวอย่าง:
<<<SEED>>>

Theme/Scope:
"""

SCORE_PROMPT = """
ฉันมี Theme/Scope และมีรายการข่าวจาก Google News
ให้คุณให้คะแนนว่าแต่ละข่าว “เข้ากับ Theme/Scope แค่ไหน”

ผลลัพธ์ต้องเป็น JSON array เท่านั้น:
{
  "idx": <ลำดับข่าวเริ่ม 1>,
  "score": 0-100,
  "country": "<ประเทศหลักที่ข่าวกล่าวถึง หรือ '-' ถ้าไม่ชัด>",
  "category": "Energy|Politics|Finance|SupplyChain|Other",
  "impact": "• ...",
  "url": "<link>"
}

เกณฑ์:
- score >= 70 = เข้าธีมชัด
- score 50-69 = พอเกี่ยว
- score < 50 = ไม่เกี่ยว

impact (รูปแบบมาตรฐาน):
- bullet เดียวขึ้นต้น "• "
- 3 ประโยค:
  1) เหตุการณ์/มาตรการจากข่าว (ยึด title/summary)
  2) ผลต่อ ต้นทุนพลังงาน/ความเสี่ยง/ทิศทางนโยบาย/ความเชื่อมั่น (ห้ามเดา)
  3) สิ่งที่ควรติดตามต่อ (price/policy/regulation/timeline)

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
    return "\n".join(lines[:12]).strip()


# =====================================================================================
# 413-safe scoring
# =====================================================================================

def _slim_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    slim = []
    for it in items:
        slim.append({
            "title": clip(it.get("title", ""), 180),
            "summary": clip(it.get("summary", ""), SUMMARY_CLIP),
            "link": normalize_url(it.get("link", "")),
        })
    return slim

def _score_once(items: List[Dict[str, Any]], theme: str) -> List[Dict[str, Any]]:
    items_json = json.dumps(_slim_items(items), ensure_ascii=False)
    prompt = SCORE_PROMPT.replace("<<<THEME>>>", theme).replace("<<<ITEMS>>>", items_json)

    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS]

    _sleep_jitter()
    raw = groq_chat(prompt, temperature=0.25)
    js = parse_json_loose(raw)
    return [x for x in js if isinstance(x, dict)] if isinstance(js, list) else []

def _score_safe(items: List[Dict[str, Any]], theme: str) -> List[Dict[str, Any]]:
    if not items:
        return []
    try:
        return _score_once(items, theme)
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        code = getattr(resp, "status_code", None)
        if code != 413 and "413" not in str(e):
            raise

        if len(items) == 1:
            one = dict(items[0])
            one["summary"] = clip(one.get("summary", ""), 120)
            return _score_once([one], theme)

        mid = len(items) // 2
        print(f"[413] Payload too large -> split batch {len(items)} => {mid}+{len(items)-mid}")
        return _score_safe(items[:mid], theme) + _score_safe(items[mid:], theme)

def score_by_theme(items: List[Dict[str, Any]], theme: str) -> List[Dict[str, Any]]:
    tags_all: List[Dict[str, Any]] = []
    i = 0
    while i < len(items):
        batch = items[i:i + THEME_BATCH_SIZE]
        tags = _score_safe(batch, theme)

        if len(tags) < len(batch):
            for k in range(len(tags) + 1, len(batch) + 1):
                tags.append({
                    "idx": k,
                    "score": 0,
                    "country": "-",
                    "category": "Other",
                    "impact": "",
                    "url": batch[k-1].get("link", "")
                })

        tags_all.extend(tags[:len(batch)])
        i += len(batch)

    return tags_all


# =====================================================================================
# LINE Flex (Carousel only)
# =====================================================================================

LINE_BROADCAST = "https://api.line.me/v2/bot/message/broadcast"

def create_flex_carousel(passed: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    for n in passed:
        title = (n.get("title") or "ข่าว").strip()[:140]
        url = (n.get("url") or "https://news.google.com/").strip()
        country = (n.get("country") or "-").strip()
        impact = (n.get("impact") or "").strip()
        score = int(n.get("score", 0) or 0)

        proj_hits = n.get("project_hits") or []
        partner_hits = n.get("partner_hits") or []

        if impact and not impact.startswith("•"):
            impact = "• " + impact.lstrip("• ").strip()

        body_contents = [
            {"type": "text", "text": title, "wrap": True, "weight": "bold", "size": "lg"},
            {"type": "text", "text": f"{country}  |  score {score}", "wrap": True, "size": "sm", "color": "#1E90FF", "margin": "sm"},
        ]

        if proj_hits:
            body_contents.append({
                "type": "text",
                "text": "Project: " + ", ".join(proj_hits[:2]) + ("…" if len(proj_hits) > 2 else ""),
                "wrap": True,
                "size": "sm",
                "color": "#666666",
                "margin": "sm"
            })

        if partner_hits:
            body_contents.append({
                "type": "text",
                "text": "Partner: " + ", ".join(partner_hits[:3]) + ("…" if len(partner_hits) > 3 else ""),
                "wrap": True,
                "size": "sm",
                "color": "#666666",
                "margin": "sm"
            })

        body_contents.extend([
            {"type": "text", "text": "Impact (standard)", "size": "lg", "weight": "bold", "margin": "lg"},
            {"type": "text", "text": impact or "• (ไม่มีข้อความ)", "wrap": True, "size": "md", "weight": "bold", "margin": "xs"},
        ])

        bubbles.append({
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": DEFAULT_HERO_URL, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "contents": body_contents},
            "footer": {"type": "box", "layout": "vertical", "contents": [
                {"type": "button", "style": "primary", "color": "#1DB446",
                 "action": {"type": "uri", "label": "อ่านต่อ", "uri": url}}
            ]}
        })

    return [{
        "type": "flex",
        "altText": f"Energy News Focus {now_txt}",
        "contents": {"type": "carousel", "contents": bubbles}
    }]

def send_to_line(messages: List[Dict[str, Any]]) -> None:
    if DRY_RUN:
        print("[DRY_RUN] sending", len(messages), "messages")
        print(json.dumps(messages, ensure_ascii=False)[:2200])
        return
    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}", "Content-Type": "application/json"}
    r = S.post(LINE_BROADCAST, headers=headers, json={"messages": messages}, timeout=60)
    if r.status_code >= 400:
        print("[LINE ERROR]", r.status_code, r.text[:900])
        r.raise_for_status()


# =====================================================================================
# MAIN
# =====================================================================================

def main():
    if not PROJECT_NAMES:
        print(f"⚠️ ไม่พบรายชื่อโครงการใน {PROJECT_LIST_FILE} (จะยังทำงานได้ แต่เงื่อนไข project จะไม่ถูกใช้)")
    if not PARTNER_NAMES:
        print(f"⚠️ ไม่พบรายชื่อผู้ร่วมทุนใน {PARTNER_LIST_FILE} (จะยังทำงานได้ แต่เงื่อนไข partner จะไม่ถูกใช้)")

    seed = read_seed_text()
    if not seed:
        raise RuntimeError("ไม่พบข่าวตัวอย่าง (Seed): ใส่ SEED_NEWS_TEXT หรือสร้างไฟล์ seed_news.txt")

    print("1) สร้าง Theme/Scope จากข่าวตัวอย่าง...")
    theme = build_theme(seed)
    print("\n[THEME]\n" + theme + "\n")

    print("2) ดึงข่าวจาก Google News RSS...")
    raw_items = fetch_google_news(SELECT_LIMIT)
    print("GoogleNews items:", len(raw_items))

    sent = load_sent_links()
    candidates = []
    for it in raw_items:
        link = it.get("link", "")
        if link and link in sent:
            continue
        candidates.append(it)

    if not candidates:
        print("ไม่มีข่าวใหม่")
        return

    print("3) ให้ LLM ให้คะแนนข่าวตาม Theme (batch กัน 413)...")
    tags = score_by_theme(candidates, theme)

    scored = []
    for it, tg in zip(candidates, tags):
        if not isinstance(tg, dict):
            continue

        url = normalize_url(tg.get("url") or it.get("link") or "")
        if not url:
            continue

        title = it.get("title", "")
        summary = it.get("summary", "")
        text = f"{title} {summary}"

        # ✅ เงื่อนไขใหม่: ต้องมี Project OR Partner (พบใน title/summary)
        project_hits = find_hits(text, PROJECT_NAMES)
        partner_hits = find_hits(text, PARTNER_NAMES)
        has_entity = bool(project_hits or partner_hits)

        score = int(tg.get("score", 0) or 0)
        if has_entity:
            score += BONUS_HIT  # ดันคะแนนขึ้นเล็กน้อยเมื่อมีชื่อโครงการ/ผู้ร่วมทุน

        scored.append({
            "title": title,
            "url": url,
            "country": (tg.get("country") or "-").strip(),
            "category": tg.get("category", "Other"),
            "impact": tg.get("impact", ""),
            "score": score,
            "project_hits": project_hits,
            "partner_hits": partner_hits,
            "has_entity": has_entity,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # --------------------------
    # ✅ FINAL FILTER (ตามที่คุณขอ)
    # --------------------------
    # ผ่านได้เมื่อ:
    # 1) score >= SCORE_STRICT (เข้า Theme ชัด)  -> ส่งได้แม้ไม่เจอชื่อ แต่ในทางปฏิบัติเราจะยังบังคับ entity ตามข้อ 2
    # 2) และต้องมี (Project OR Partner) เป็นเงื่อนไขร่วม
    #
    # เพื่อให้ “ชื่อโครงการ + ผู้ร่วมทุน” อยู่ในเงื่อนไขจริง ๆ:
    # - เราจะบังคับ has_entity เสมอ
    # - ถ้าข่าวเข้า theme สูงมากแต่ไม่มี entity จะตัดออก (ตามที่คุณกำหนด)
    #
    passed = [x for x in scored if x["has_entity"] and x["score"] >= SCORE_THEME_MIN][:SEND_LIMIT]

    if not passed:
        print("ไม่มีข่าวที่เข้าเงื่อนไข (Theme + (Project OR Partner))")
        return

    print("4) ส่ง Flex carousel อย่างเดียว...")
    msgs = create_flex_carousel(passed)
    send_to_line(msgs)

    save_sent_links([p["url"] for p in passed if p.get("url")])
    print("Done")


if __name__ == "__main__":
    main()
