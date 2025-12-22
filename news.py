# news.py
# ============================================================================================================
# AUTO NEWS BOT (RSS -> Groq LLM -> LINE Broadcast)
# - Uses GroqCloud LLM (OpenAI-compatible "chat.completions")
# - Batch tagging to reduce API calls
# - Dedup via sent_links directory
# - Bangkok timezone + time window filter (21:00 - 06:00) optional
# ============================================================================================================

import os
import re
import json
import time
import hashlib
import random
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import feedparser
import pytz
import requests
from dateutil import parser as dateutil_parser
from groq import Groq

# ============================================================================================================
# ENV / CONFIG
# ============================================================================================================

BKK_TZ = pytz.timezone(os.getenv("TIMEZONE", "Asia/Bangkok"))

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
if not GROQ_API_KEY:
    raise RuntimeError("ไม่พบ GROQ_API_KEY")

GROQ_MODEL = os.getenv("GROQ_MODEL_NAME", "llama-3.1-8b-instant").strip()

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")

# LLM behavior
BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "10"))           # 10 ข่าวต่อ 1 request
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))
SLEEP_BETWEEN_CALLS_MIN = float(os.getenv("SLEEP_MIN", "0.15"))
SLEEP_BETWEEN_CALLS_MAX = float(os.getenv("SLEEP_MAX", "0.45"))
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.35"))

# Filtering / runtime
MAX_ITEMS_TO_LLM = int(os.getenv("MAX_ITEMS_TO_LLM", "80"))   # จำกัดข่าวที่จะส่งให้ LLM (กันยาวเกิน)
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "36"))       # ข่าวย้อนหลัง (ช่วยกัน RSS ที่เวลาเพี้ยน)
USE_TIME_WINDOW = os.getenv("USE_TIME_WINDOW", "true").lower() in ("1", "true", "yes", "y")

# Time window (21:00 -> 06:00)
WINDOW_START_HOUR = int(os.getenv("WINDOW_START_HOUR", "21"))  # 21:00
WINDOW_END_HOUR = int(os.getenv("WINDOW_END_HOUR", "6"))       # 06:00

# Line options
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes", "y")
LINE_BROADCAST_ENDPOINT = "https://api.line.me/v2/bot/message/broadcast"

# Storage
SENT_DIR = os.getenv("SENT_DIR", "sent_links")
os.makedirs(SENT_DIR, exist_ok=True)

groq_client = Groq(api_key=GROQ_API_KEY)

# ============================================================================================================
# RSS SOURCES (ปรับ/เพิ่มได้)
# - feed_country: ใช้เป็นประเทศอ้างอิงตอนคัดข่าว (เข้ม: ข่าวต้องเกิดในประเทศนี้จริง)
# ============================================================================================================

RSS_SOURCES = [
    # ตัวอย่าง (แก้เป็นของคุณ)
    {"name": "OilPrice", "url": "https://oilprice.com/rss/main", "feed_country": "Global"},
    {"name": "Reuters Energy", "url": "https://www.reutersagency.com/feed/?best-topics=energy&post_type=best", "feed_country": "Global"},
    # เพิ่มของคุณตรงนี้...
]

# ============================================================================================================
# PROJECT MAP (ตัวอย่าง) : ประเทศ -> รายชื่อโครงการ
# - ใส่ของจริงของคุณได้เลย
# ============================================================================================================

PROJECTS_BY_COUNTRY = {
    "Thailand": ["Erawan", "Bongkot", "Arthit", "G1/61", "G2/61"],
    "Malaysia": ["Block H", "SK410B", "SK417"],
    "UAE": ["Sharjah", "Abu Dhabi (Onshore/Offshore)"],
    "Oman": ["Block 61", "Block 6"],
    "Qatar": ["Qatar LNG Related"],
    "Indonesia": ["Natuna", "Bongkot (TH) - example remove", "Mahakam"],
    "Global": ["ALL"],
}

def projects_for_country(country: str):
    c = (country or "").strip()
    return PROJECTS_BY_COUNTRY.get(c, [])

# ============================================================================================================
# UTIL: URL normalize / dedupe
# ============================================================================================================

TRACKING_PARAMS_PREFIX = ("utm_", "fbclid", "gclid", "yclid", "mc_cid", "mc_eid")

def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        u = url.strip()
        parsed = urlparse(u)
        q = parse_qsl(parsed.query, keep_blank_values=True)
        q2 = []
        for k, v in q:
            lk = k.lower()
            if lk.startswith(TRACKING_PARAMS_PREFIX) or lk in TRACKING_PARAMS_PREFIX:
                continue
            q2.append((k, v))
        new_query = urlencode(q2, doseq=True)
        normalized = parsed._replace(query=new_query, fragment="")
        return urlunparse(normalized)
    except Exception:
        return url.strip()

def link_key(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8", errors="ignore")).hexdigest()
    return h

def was_sent(url: str) -> bool:
    k = link_key(url)
    return os.path.exists(os.path.join(SENT_DIR, k + ".txt"))

def mark_sent(url: str):
    k = link_key(url)
    path = os.path.join(SENT_DIR, k + ".txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(url)

# ============================================================================================================
# UTIL: time filtering
# ============================================================================================================

def now_bkk() -> datetime:
    return datetime.now(BKK_TZ)

def within_time_window(dt: datetime) -> bool:
    if not USE_TIME_WINDOW:
        return True
    # window 21:00 -> 06:00 (cross midnight)
    h = dt.hour
    if WINDOW_START_HOUR <= WINDOW_END_HOUR:
        return WINDOW_START_HOUR <= h < WINDOW_END_HOUR
    return (h >= WINDOW_START_HOUR) or (h < WINDOW_END_HOUR)

def parse_entry_datetime(entry) -> datetime | None:
    # Try published / updated fields
    for key in ("published", "updated", "created"):
        if getattr(entry, key, None):
            try:
                d = dateutil_parser.parse(getattr(entry, key))
                if d.tzinfo is None:
                    d = BKK_TZ.localize(d)
                return d.astimezone(BKK_TZ)
            except Exception:
                pass

    # Try feedparser struct_time
    for key in ("published_parsed", "updated_parsed"):
        if getattr(entry, key, None):
            try:
                d = datetime.fromtimestamp(time.mktime(getattr(entry, key)), tz=pytz.UTC).astimezone(BKK_TZ)
                return d
            except Exception:
                pass
    return None

# ============================================================================================================
# GROQ: JSON extraction + retries
# ============================================================================================================

def _extract_json_object(text: str):
    """
    Robust JSON extractor:
    - find first {...} block
    - remove trailing junk
    """
    if not text:
        return None
    s = text.strip()

    # Remove code fences if any
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE | re.MULTILINE).strip()

    # Fast path
    try:
        return json.loads(s)
    except Exception:
        pass

    # Find first json object
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return None
    candidate = m.group(0).strip()

    # Try clean common trailing commas
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)

    try:
        return json.loads(candidate)
    except Exception:
        return None

def _is_429(e: Exception) -> bool:
    s = str(e).lower()
    return ("429" in s) or ("too many requests" in s) or ("rate limit" in s)

def call_groq(prompt: str, temperature: float = TEMPERATURE) -> str:
    time.sleep(random.uniform(SLEEP_BETWEEN_CALLS_MIN, SLEEP_BETWEEN_CALLS_MAX))
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": "You must output STRICT JSON only. No markdown, no prose."},
            {"role": "user", "content": prompt},
        ],
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()

def call_groq_with_retries(prompt: str, temperature: float = TEMPERATURE) -> str:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return call_groq(prompt, temperature=temperature)
        except Exception as e:
            last_err = e
            if _is_429(e):
                # backoff harder on 429
                sleep_s = min(20.0, 1.8 ** attempt + random.uniform(0.5, 1.5))
                print(f"[Groq] 429 TooManyRequests -> backoff {sleep_s:.1f}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(sleep_s)
                continue
            sleep_s = min(12.0, 1.5 ** attempt + random.uniform(0.3, 1.0))
            print(f"[Groq] error -> sleep {sleep_s:.1f}s (attempt {attempt}/{MAX_RETRIES}) : {e}")
            time.sleep(sleep_s)

    # If still failing, raise (caller can catch)
    raise last_err

# ============================================================================================================
# LLM: Batch tagging & impact
# ============================================================================================================

def diversify_bullets(bullets: list[str]) -> list[str]:
    # simple dedupe by lowercase
    seen = set()
    out = []
    for b in bullets:
        k = re.sub(r"\s+", " ", b.strip().lower())
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(b.strip())
    return out

def has_meaningful_impact(bullets: list[str]) -> bool:
    if not bullets:
        return False
    joined = " ".join(bullets).lower()
    bad = ["no impact", "none", "unclear", "not relevant", "n/a"]
    return not any(x in joined for x in bad)

def groq_batch_tag_and_filter(news_list: list[dict], chunk_size: int = BATCH_SIZE) -> list[dict]:
    """
    1 request ต่อ chunk (เช่น 10 ข่าว) -> ลดจำนวน requests แบบมหาศาล
    """
    results: list[dict] = []
    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]

        payload = []
        for idx, n in enumerate(chunk):
            payload.append({
                "id": idx,
                "feed_country": (n.get("feed_country") or "").strip(),
                "title": n.get("title", ""),
                "summary": n.get("summary", ""),
            })

        prompt = f"""
คุณเป็นผู้ช่วยคัดกรองข่าวเพื่อการดำเนินงานด้านพลังงานและโครงการสำรวจ/ผลิต

กติกาเข้ม:
- รับเฉพาะข่าวที่มีนัยสำคัญด้าน: พลังงาน, นโยบายรัฐ/กฎระเบียบ/ภาษี/PSC, ความมั่นคง, ความปลอดภัย, โลจิสติกส์/ท่าเรือ, คว่ำบาตร, ค่าเงิน/เงินทุน, เหตุขัดข้องซัพพลายเชน
- "เหตุการณ์ต้องเกิดในประเทศ feed_country ของข่าวนั้นจริง ๆ" ไม่ใช่แค่พูดถึง
- ถ้า is_relevant=true:
  - country ต้องเท่ากับ feed_country เท่านั้น (strict)
  - projects เลือก 0–2 โครงการในประเทศนั้น (ถ้าไม่ชัดเจนให้ ["ALL"])
  - evidence 1–2 วลีสั้น ๆ ต้องพบใน title/summary (ห้ามแต่งเพิ่ม)
  - impact_bullets 2–4 bullets ต้องอธิบาย "กลไกผลกระทบ" อย่างน้อย 1 เช่น ใบอนุญาต/ภาษี-PSC/โลจิสติกส์/ผู้รับเหมา/ประกัน/FX/ศุลกากร/คว่ำบาตร/ความปลอดภัย
  - why_relevant สั้น ๆ 1 ประโยค

ให้ตอบเป็น JSON เท่านั้น รูปแบบ:
{{
  "items": [
    {{
      "id": 0,
      "is_relevant": true/false,
      "country": "Thailand",
      "projects": ["ALL"] or ["..."],
      "impact_bullets": ["...","..."],
      "impact_level": "low|medium|high|unknown",
      "evidence": ["...","..."],
      "why_relevant": "..."
    }}
  ]
}}

ข่าวชุดนี้:
{json.dumps(payload, ensure_ascii=False)}
"""
        text = call_groq_with_retries(prompt, temperature=TEMPERATURE)
        data = _extract_json_object(text)

        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            # ถ้าพัง ให้ถือว่าไม่ผ่านทั้งหมดใน chunk นี้ (ไม่ล้มทั้งงาน)
            for _ in chunk:
                results.append({"is_relevant": False})
            continue

        by_id = {}
        for it in data["items"]:
            if isinstance(it, dict) and "id" in it:
                by_id[it.get("id")] = it

        for idx, _n in enumerate(chunk):
            results.append(by_id.get(idx, {"is_relevant": False}))

    return results

# ============================================================================================================
# RSS FETCH
# ============================================================================================================

def fetch_rss() -> list[dict]:
    items: list[dict] = []
    cutoff = now_bkk() - timedelta(hours=LOOKBACK_HOURS)

    for src in RSS_SOURCES:
        url = src["url"]
        name = src.get("name", "RSS")
        feed_country = src.get("feed_country", "Global")

        try:
            d = feedparser.parse(url)
        except Exception as e:
            print(f"[RSS] failed: {name} -> {e}")
            continue

        for e in (d.entries or []):
            link = normalize_url(getattr(e, "link", "") or "")
            if not link:
                continue

            dt = parse_entry_datetime(e)
            if dt is None:
                dt = now_bkk()

            if dt < cutoff:
                continue

            title = (getattr(e, "title", "") or "").strip()
            summary = (getattr(e, "summary", "") or "").strip()

            if not title and not summary:
                continue

            # window filter based on entry time (optional)
            if not within_time_window(dt):
                continue

            items.append({
                "source": name,
                "feed_country": feed_country,
                "title": title,
                "summary": summary[:1200],  # กันยาว
                "link": link,
                "published_at": dt.isoformat(),
            })

    # dedupe by normalized link
    seen = set()
    deduped = []
    for n in items:
        if n["link"] in seen:
            continue
        seen.add(n["link"])
        deduped.append(n)
    return deduped

# ============================================================================================================
# LINE SEND
# ============================================================================================================

def line_broadcast_text(messages: list[dict]):
    if DRY_RUN:
        print("[DRY_RUN] LINE broadcast payload:")
        print(json.dumps({"messages": messages}, ensure_ascii=False, indent=2))
        return

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"messages": messages}
    r = requests.post(LINE_BROADCAST_ENDPOINT, headers=headers, json=payload, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"LINE error {r.status_code}: {r.text}")

def build_line_messages(final_items: list[dict]) -> list[dict]:
    """
    สร้างข้อความแบบ Text (ง่ายสุด/เสถียรสุด)
    ถ้าคุณอยากส่ง Flex Message เดิม บอกได้ เดี๋ยวผมแปลงให้
    """
    messages = []
    for n in final_items:
        country = n.get("country") or n.get("feed_country") or "-"
        projects = n.get("projects") or ["ALL"]
        level = n.get("impact_level", "unknown")
        evidence = n.get("evidence") or []
        bullets = n.get("impact_bullets") or []

        text_lines = []
        text_lines.append(f"🗞️ {n.get('title','').strip()}")
        text_lines.append(f"🌍 Country: {country}")
        text_lines.append(f"🧩 Project: {', '.join(projects[:6])}")
        text_lines.append(f"⚠️ Impact Level: {level}")
        if evidence:
            text_lines.append(f"🔎 Evidence: {', '.join(evidence[:2])}")
        if bullets:
            text_lines.append("📌 Impacts:")
            for b in bullets[:6]:
                text_lines.append(f"- {b}")
        text_lines.append(f"🔗 {n.get('link','')}")
        messages.append({"type": "text", "text": "\n".join(text_lines)})

    # LINE broadcast จำกัดจำนวนข้อความต่อ request (ปกติ <= 5 ข้อความ/ครั้ง)
    # ถ้าคุณมีเยอะ ให้ส่งทีละชุด
    return messages

# ============================================================================================================
# MAIN
# ============================================================================================================

def main():
    print("ดึงข่าวจาก RSS ...")
    news = fetch_rss()
    print(f"จำนวนข่าวหลัง dedupe+เวลา: {len(news)}")

    # ตัดข่าวที่เคยส่งแล้วออก
    fresh = []
    for n in news:
        if was_sent(n["link"]):
            continue
        fresh.append(n)

    print(f"จำนวนข่าวใหม่ (ยังไม่เคยส่ง): {len(fresh)}")
    if not fresh:
        print("ไม่มีข่าวใหม่")
        return

    # จำกัดจำนวนที่จะส่งเข้า LLM กันยาวเกิน
    selected = fresh[:MAX_ITEMS_TO_LLM]
    print(f"จำนวนข่าวที่จะส่งเข้า LLM: {len(selected)} (MAX_ITEMS_TO_LLM={MAX_ITEMS_TO_LLM})")

    # เรียก LLM แบบ batch
    final_items: list[dict] = []
    try:
        tags = groq_batch_tag_and_filter(selected, chunk_size=BATCH_SIZE)
    except Exception as e:
        # ถ้าโควต้าเต็ม/429 ก็ไม่ล้มทั้งงาน
        if _is_429(e):
            print("Groq 429: โควต้า/เรทลิมิตเต็ม -> จะงด LLM รอบนี้")
            tags = [{"is_relevant": False} for _ in selected]
        else:
            raise

    for n, tag in zip(selected, tags):
        if not isinstance(tag, dict) or not tag.get("is_relevant"):
            continue

        feed_country = (n.get("feed_country") or "").strip()
        country = (tag.get("country") or "").strip()

        # strict: country must match feed_country
        if country != feed_country:
            continue

        projects = tag.get("projects") or ["ALL"]
        if projects == ["ALL"]:
            cp = projects_for_country(country)
            if cp:
                projects = cp

        bullets = tag.get("impact_bullets") or []
        if not isinstance(bullets, list):
            bullets = [str(bullets)]
        bullets = diversify_bullets([str(x).strip() for x in bullets if str(x).strip()])

        if not has_meaningful_impact(bullets):
            continue

        n2 = dict(n)
        n2["country"] = country
        n2["projects"] = projects[:6] if isinstance(projects, list) else ["ALL"]
        n2["impact_level"] = tag.get("impact_level", "unknown")
        n2["evidence"] = (tag.get("evidence") or [])[:2]
        n2["why_relevant"] = (tag.get("why_relevant") or "").strip()
        n2["impact_bullets"] = bullets[:6]
        final_items.append(n2)

    print(f"จำนวนข่าวที่ผ่าน LLM: {len(final_items)}")
    if not final_items:
        print("ไม่มีข่าวที่ผ่านเกณฑ์")
        return

    # ส่ง LINE (ถ้ามาก ให้แบ่งเป็นชุด ๆ)
    messages = build_line_messages(final_items)

    # LINE broadcast ปกติส่งได้หลาย message ต่อครั้ง แต่เพื่อชัวร์ แบ่งทีละ 5
    CHUNK = 5
    for i in range(0, len(messages), CHUNK):
        batch = messages[i:i + CHUNK]
        line_broadcast_text(batch)

    # mark sent เฉพาะที่ส่งสำเร็จ
    for n in final_items:
        mark_sent(n["link"])

    print("ส่ง LINE สำเร็จ และบันทึก sent_links แล้ว")

if __name__ == "__main__":
    main()
