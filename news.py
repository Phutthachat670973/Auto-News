# news.py
# ============================================================================================================
# Energy News Bot (Groq) — “ข่าวแนว PDP/Regulator/SMR/LNG-FID/Financing/Security/Geo” ไทย+ต่างประเทศ
# - ใช้ Google News RSS 2 ชุด: ไทย (domestic) + ต่างประเทศ (international)
# - LLM-first คัดข่าว + จัดหมวด + ดึง "ประเทศ/ผู้เกี่ยวข้อง/โครงการหรือสินทรัพย์ที่ถูกกล่าวถึง"
# - สรุปผลกระทบเป็นไทย 1 bullet (25–45 คำ) และมี evidence จาก title/summary
# - Fix รูปหาย: resolve Google News ไป publisher URL แบบปลอดภัย + fallback hero เสมอ
# ============================================================================================================

import os
import re
import json
import time
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, quote_plus, unquote
from difflib import SequenceMatcher

import feedparser
import requests
from dateutil import parser as dateutil_parser
import pytz
from groq import Groq

# ============================================================================================================
# ENV
# ============================================================================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()

if not GROQ_API_KEY:
    raise RuntimeError("ไม่พบ GROQ_API_KEY")
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("ไม่พบ LINE_CHANNEL_ACCESS_TOKEN")

GROQ_MODEL_NAME = os.getenv("GROQ_MODEL_NAME", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant"
groq_client = Groq(api_key=GROQ_API_KEY)

def _as_limit(env_name: str, default: str = "0"):
    """<=0 => None (unlimited)"""
    try:
        v = int(os.getenv(env_name, default))
        return None if v <= 0 else v
    except Exception:
        return None

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "6"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "10"))

MAX_ITEMS_DOMESTIC = _as_limit("MAX_ITEMS_DOMESTIC", "0")
MAX_ITEMS_INTERNATIONAL = _as_limit("MAX_ITEMS_INTERNATIONAL", "0")
MAX_LLM_ITEMS = _as_limit("MAX_LLM_ITEMS", "0")          # cap รวมก่อนเข้า LLM (0=ไม่จำกัด)
MAX_SEND_ITEMS = _as_limit("MAX_SEND_ITEMS", "10")       # จำนวน bubble ที่ส่ง LINE

RUN_DEADLINE_MIN = int(os.getenv("RUN_DEADLINE_MIN", "0"))  # 0 = no deadline
RSS_TIMEOUT_SEC = int(os.getenv("RSS_TIMEOUT_SEC", "15"))
ARTICLE_TIMEOUT_SEC = int(os.getenv("ARTICLE_TIMEOUT_SEC", "12"))

SLEEP_MIN = float(os.getenv("SLEEP_MIN", "0.2" if os.getenv("GITHUB_ACTIONS") else "0.6"))
SLEEP_MAX = float(os.getenv("SLEEP_MAX", "0.6" if os.getenv("GITHUB_ACTIONS") else "1.2"))
SLEEP_BETWEEN_CALLS = (max(0.0, SLEEP_MIN), max(SLEEP_MIN, SLEEP_MAX))

ENABLE_IMPACT_REWRITE = os.getenv("ENABLE_IMPACT_REWRITE", "true").strip().lower() in ["1", "true", "yes", "y"]
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ["1", "true", "yes", "y"]

SHOW_SOURCE_RATING = os.getenv("SHOW_SOURCE_RATING", "true").strip().lower() in ["1", "true", "yes", "y"]
MIN_SOURCE_SCORE = int(os.getenv("MIN_SOURCE_SCORE", "0"))

MAX_ENTRIES_PER_FEED = int(os.getenv("MAX_ENTRIES_PER_FEED", "120"))

# fallback hero ที่ “ควรขึ้นแน่ๆ”
DEFAULT_HERO_URL = os.getenv(
    "DEFAULT_HERO_URL",
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/News_icon.png/640px-News_icon.png"
)

bangkok_tz = pytz.timezone("Asia/Bangkok")
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})

GROQ_CALLS = 0

# ============================================================================================================
# FEEDS (แนวเดียวกับที่คุณยกตัวอย่าง)
# ============================================================================================================

def google_news_rss(q: str, hl="en", gl="US", ceid="US:en"):
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"

# ปรับ/เติม keyword ได้ใน ENV ถ้าอยาก custom
TH_STYLE_QUERY = os.getenv("TH_STYLE_QUERY", "").strip() or """
(แผน PDP OR PDP ใหม่ OR ค่าไฟ OR โครงสร้างค่าไฟ OR กกพ OR "Direct PPA" OR PPA OR
กฟผ OR SMR OR โรงไฟฟ้านิวเคลียร์ OR นิวเคลียร์ OR Net Zero OR Carbon Neutral OR
Data Center ไฟฟ้า OR ดีมานด์ไฟ OR
LNG OR ก๊าซธรรมชาติ OR ท่อส่งก๊าซ OR ท่าเรือ OR โครงสร้างพื้นฐานพลังงาน OR
แท่นขุดเจาะ OR ความปลอดภัยทรัพย์สินพลังงาน OR โดรน OR
คว่ำบาตร OR ค่าเงิน OR มาตรการรัฐ OR ภาษีพลังงาน)
"""

WORLD_STYLE_QUERY = os.getenv("WORLD_STYLE_QUERY", "").strip() or """
(energy policy OR power tariff OR regulator OR direct PPA OR
SMR OR small modular reactor OR nuclear restart OR
LNG project OR FID OR liquefaction OR export terminal OR import terminal OR
structured financing OR project financing OR EPC contract OR equipment orders OR
offshore wind halted OR national security OR sanctions OR crude flows OR LNG flows OR
OPEC policy OR gas development OR pipeline)
"""

NEWS_FEEDS = [
    ("GoogleNewsTH", "domestic", google_news_rss(TH_STYLE_QUERY, hl="th", gl="TH", ceid="TH:th")),
    ("GoogleNewsEN", "international", google_news_rss(WORLD_STYLE_QUERY, hl="en", gl="US", ceid="US:en")),
    # จะเก็บ legacy global feed ไว้ก็ได้ (มักได้ข่าว LNG/energy)
    ("Oilprice", "international", "https://oilprice.com/rss/main"),
    ("Economist", "international", "https://www.economist.com/latest/rss.xml"),
    ("YahooFinance", "international", "https://finance.yahoo.com/news/rssindex"),
]

# ============================================================================================================
# URL normalize + sent_links
# ============================================================================================================

def _normalize_link(url: str) -> str:
    try:
        p = urlparse(url)
        scheme = (p.scheme or "https").lower()
        netloc = (p.netloc or "").lower()
        path = p.path or ""
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        for k in list(q.keys()):
            lk = k.lower()
            if lk.startswith("utm_") or lk in ["fbclid", "gclid", "mc_cid", "mc_eid"]:
                q.pop(k, None)
        query = urlencode(sorted(q.items()))
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url or ""

def get_sent_links_file():
    d = datetime.now(bangkok_tz).strftime("%Y-%m-%d")
    os.makedirs("sent_links", exist_ok=True)
    return os.path.join("sent_links", f"sent_links_{d}.txt")

def load_sent_links():
    fp = get_sent_links_file()
    if not os.path.exists(fp):
        return set()
    with open(fp, "r", encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())

def save_sent_links(links):
    fp = get_sent_links_file()
    existing = load_sent_links()
    existing.update(_normalize_link(x) for x in links if x)
    with open(fp, "w", encoding="utf-8") as f:
        for x in sorted(existing):
            f.write(x + "\n")

# ============================================================================================================
# Resolve Google News -> publisher (กัน tracking/asset)
# ============================================================================================================

def _get_domain(u: str) -> str:
    try:
        p = urlparse(u)
        host = (p.netloc or "").lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico")

DISALLOWED_HOSTS = {
    "lh3.googleusercontent.com",
    "googleusercontent.com",
    "gstatic.com",
    "accounts.google.com",
    "support.google.com",
}

TRACKER_HOSTS = {
    "google-analytics.com",
    "www.google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "stats.g.doubleclick.net",
    "t.co",
}

def _is_good_publisher_url(u: str) -> bool:
    if not u or not u.startswith(("http://", "https://")):
        return False
    host = _get_domain(u)
    if not host:
        return False
    if host in DISALLOWED_HOSTS or any(host.endswith(x) for x in DISALLOWED_HOSTS):
        return False
    if host in TRACKER_HOSTS or any(host.endswith(x) for x in TRACKER_HOSTS):
        return False
    path = (urlparse(u).path or "").lower()
    if any(path.endswith(ext) for ext in IMAGE_EXTS):
        return False
    return True

def resolve_final_url(url: str) -> str:
    """
    เป้าหมาย: ได้ publisher URL จริง
    - ห้ามหลุดเป็น tracking/asset (google-analytics / googleusercontent / gstatic)
    - ถ้าแกะไม่ได้จริงๆ ให้คืน url เดิม
    """
    if not url:
        return url

    try:
        r = S.get(url, timeout=min(ARTICLE_TIMEOUT_SEC, 10), allow_redirects=True)
        final = r.url or url
        host = _get_domain(final)

        if host in TRACKER_HOSTS or any(host.endswith(x) for x in TRACKER_HOSTS):
            try:
                r.close()
            except Exception:
                pass
            return url

        if host == "news.google.com":
            html = r.text or ""

            # canonical
            m = re.search(r'rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']', html, re.I)
            if m:
                cand = m.group(1).strip()
                if _is_good_publisher_url(cand):
                    try:
                        r.close()
                    except Exception:
                        pass
                    return cand

            # url=
            m = re.search(r"(?:\?|&|amp;)url=(https?%3A%2F%2F[^&\"']+)", html, re.I)
            if m:
                cand = unquote(m.group(1))
                if _is_good_publisher_url(cand):
                    try:
                        r.close()
                    except Exception:
                        pass
                    return cand

            # first good https href
            hrefs = re.findall(r'href=["\'](https?://[^"\']+)["\']', html, flags=re.I)
            for cand in hrefs[:200]:
                cand = cand.strip()
                if _is_good_publisher_url(cand):
                    try:
                        r.close()
                    except Exception:
                        pass
                    return cand

            try:
                r.close()
            except Exception:
                pass
            return final

        try:
            r.close()
        except Exception:
            pass
        return final

    except Exception:
        return url

# ============================================================================================================
# Credibility scoring (เบา ๆ)
# ============================================================================================================

def _is_https(u: str) -> bool:
    try:
        return (urlparse(u).scheme or "").lower() == "https"
    except Exception:
        return False

HIGH_TRUST_DOMAINS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "economist.com",
    "apnews.com", "bbc.co.uk", "bbc.com", "nytimes.com", "washingtonpost.com",
    "theguardian.com", "aljazeera.com", "nhk.or.jp", "nikkei.com",
    "spglobal.com", "iea.org", "opec.org", "worldbank.org", "imf.org", "un.org",
}

ENERGY_TRADE_DOMAINS = {
    "oilprice.com", "rigzone.com", "argusmedia.com", "energyintel.com",
}

LOW_TRUST_HINTS = ["click", "viral", "rumor", "shocking", "unbelievable", "exposed", "หวย", "ทำนาย", "ดวง", "แจก", "เครดิตฟรี"]

def assess_source_credibility(original_url: str, final_url: str, title: str) -> Dict[str, Any]:
    score = 0
    signals = []
    fu = final_url or original_url
    domain = _get_domain(fu)

    if domain in TRACKER_HOSTS or any(domain.endswith(x) for x in TRACKER_HOSTS):
        return {
            "domain": domain,
            "final_url": original_url,
            "score": 0,
            "rating": "low",
            "rating_th": "ต่ำ",
            "signals": ["tracker-url"],
        }

    if _is_https(fu):
        score += 1
        signals.append("https")

    if domain.endswith(".gov") or domain.endswith(".edu"):
        score += 2
        signals.append("gov/edu")

    def _is_high_trust(d: str) -> bool:
        if d in HIGH_TRUST_DOMAINS:
            return True
        return any(d.endswith(hd) for hd in HIGH_TRUST_DOMAINS)

    if domain and _is_high_trust(domain):
        score += 3
        signals.append("major-domain")

    if domain and (domain in ENERGY_TRADE_DOMAINS or any(domain.endswith(x) for x in ENERGY_TRADE_DOMAINS)):
        score += 2
        signals.append("energy-trade-press")

    if domain == "news.google.com":
        score += 2
        signals.append("google-news-aggregator")

    t = (title or "").lower()
    if any(h in t for h in LOW_TRUST_HINTS):
        score -= 2
        signals.append("clickbait-terms-in-title")

    if domain.count("-") >= 3:
        score -= 1
        signals.append("many-hyphens-domain")

    if score >= 5:
        rating, rating_th = "high", "สูง"
    elif score >= 3:
        rating, rating_th = "medium", "กลาง"
    else:
        rating, rating_th = "low", "ต่ำ"

    return {
        "domain": domain,
        "final_url": fu,
        "score": score,
        "rating": rating,
        "rating_th": rating_th,
        "signals": signals,
    }

# ============================================================================================================
# Feed parsing + image fetch
# ============================================================================================================

def parse_feed_with_timeout(url: str):
    r = S.get(url, timeout=RSS_TIMEOUT_SEC, allow_redirects=True)
    r.raise_for_status()
    return feedparser.parse(r.text)

def _is_good_image_url(u: str) -> bool:
    if not u or not isinstance(u, str):
        return False
    if not u.startswith("https://"):
        return False
    host = _get_domain(u)
    if host in DISALLOWED_HOSTS or any(host.endswith(x) for x in DISALLOWED_HOSTS):
        return False
    if host in TRACKER_HOSTS or any(host.endswith(x) for x in TRACKER_HOSTS):
        return False
    if len(u) > 1200:
        return False
    return True

def fetch_article_image(url: str):
    try:
        if not url or not url.startswith(("http://", "https://")):
            return None
        if _get_domain(url) == "news.google.com":
            return None

        r = S.get(url, timeout=ARTICLE_TIMEOUT_SEC, allow_redirects=True)
        if r.status_code >= 300:
            return None
        html = r.text or ""

        m = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            u = m.group(1).strip()
            if _is_good_image_url(u):
                return u

        m = re.search(r'name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']', html, re.I)
        if m:
            u = m.group(1).strip()
            if _is_good_image_url(u):
                return u

        return None
    except Exception:
        return None

# ============================================================================================================
# Dedupe near-duplicate titles
# ============================================================================================================

def normalize_title(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[\W_]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def dedupe_near_titles(items: list, threshold: float = 0.88) -> list:
    kept = []
    seen_titles = []
    for n in items:
        nt = normalize_title(n.get("title", ""))
        if not nt:
            continue
        dup = False
        for st in seen_titles:
            if SequenceMatcher(None, nt, st).ratio() >= threshold:
                dup = True
                break
        if not dup:
            kept.append(n)
            seen_titles.append(nt)
    return kept

# ============================================================================================================
# JSON extractor + bullet helpers
# ============================================================================================================

def _extract_json_object(raw: str):
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(json)?", "", s, flags=re.I).strip()
        s = re.sub(r"```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    first = s.find("{")
    last = s.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidate = s[first:last + 1]
        candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            return json.loads(candidate)
        except Exception:
            return None
    return None

def clean_bullets(bullets):
    if not isinstance(bullets, list):
        bullets = [str(bullets)]
    out = []
    for b in bullets:
        s = str(b).strip()
        if not s or s == "•":
            continue
        out.append(s)
    return out

def diversify_bullets(bullets):
    if not bullets:
        return bullets
    seen = set()
    out = []
    for b in bullets:
        k = re.sub(r"\s+", " ", (b or "").strip().lower())
        if not k or k in seen:
            continue
        seen.add(k)
        out.append((b or "").strip())
    return out

def has_meaningful_impact(bullets) -> bool:
    if not bullets or not isinstance(bullets, list):
        return False
    bullets = [str(x).strip() for x in bullets if str(x).strip()]
    if len(bullets) < 1:
        return False
    txt = " ".join(bullets)
    bad = ["ยังไม่พบผลกระทบ", "ไม่พบผลกระทบ", "ไม่ระบุผลกระทบ", "ไม่เกี่ยวข้อง", "ข้อมูลไม่เพียงพอ"]
    t = txt.lower().replace(" ", "")
    if any(x.replace(" ", "") in t for x in bad):
        return False
    return len(txt.strip()) >= 70

def validate_evidence_in_text(title: str, summary: str, evidence_list: list) -> bool:
    text = f"{title or ''} {summary or ''}".lower()
    if not evidence_list:
        return False
    ok = 0
    for ev in evidence_list[:2]:
        ev = str(ev).strip().lower()
        if len(ev) >= 4 and ev in text:
            ok += 1
    return ok >= 1

CROSS_TOPIC_GUARD_TERMS = ["cambodia", "กัมพูชา", "casino", "คาสิโน", "bridge", "สะพาน"]
def guard_cross_topic(title: str, summary: str, bullets: list[str]) -> bool:
    text = f"{title or ''} {summary or ''}".lower()
    for b in bullets[:1]:
        bl = (b or "").lower()
        for term in CROSS_TOPIC_GUARD_TERMS:
            if term in bl and term not in text:
                return False
    return True

def english_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = sum(1 for c in text if c.isalpha())
    ascii_letters = sum(1 for c in text if ('A' <= c <= 'Z') or ('a' <= c <= 'z'))
    return (ascii_letters / max(1, letters))

def is_mostly_english(text: str) -> bool:
    return english_ratio(text) >= 0.55

# ============================================================================================================
# GROQ calls + rewrite bullet (Thai only)
# ============================================================================================================

def _is_429(e: Exception) -> bool:
    s = str(e).lower()
    return ("429" in s) or ("too many requests" in s) or ("rate limit" in s)

def call_groq(prompt: str, temperature: float = 0.35) -> str:
    global GROQ_CALLS
    time.sleep(random.uniform(*SLEEP_BETWEEN_CALLS))
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL_NAME,
        messages=[
            {"role": "system", "content": "ตอบเป็น JSON เท่านั้น ห้ามมี markdown ห้ามมีข้อความอื่น"},
            {"role": "user", "content": prompt},
        ],
        temperature=float(temperature),
    )
    GROQ_CALLS += 1
    return (resp.choices[0].message.content or "").strip()

def call_groq_with_retries(prompt: str, temperature: float = 0.35) -> str:
    last = None
    for i in range(1, MAX_RETRIES + 1):
        try:
            return call_groq(prompt, temperature=temperature)
        except Exception as e:
            last = e
            if _is_429(e) and i < MAX_RETRIES:
                sleep_s = min(25.0, (1.8 ** i) + random.uniform(0.5, 1.5))
                print(f"[Groq] 429 -> retry {i}/{MAX_RETRIES} in {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
            if i < MAX_RETRIES:
                sleep_s = min(12.0, (1.5 ** i) + random.uniform(0.3, 1.0))
                print(f"[Groq] error -> retry {i}/{MAX_RETRIES} in {sleep_s:.1f}s: {type(e).__name__}: {e}")
                time.sleep(sleep_s)
                continue
            raise
    raise last

GENERIC_PATTERNS = ["อาจกระทบต้นทุน", "อาจกระทบกฎระเบียบ", "อาจกระทบตารางงาน", "อาจส่งผลกระทบ", "อาจกระทบต่อโครงการ"]
SPECIFIC_HINTS = ["ใบอนุญาต", "ภาษี", "psc", "สัมปทาน", "ประกัน", "ผู้รับเหมา", "แรงงาน", "ท่าเรือ", "ขนส่ง", "ศุลกากร", "ค่าเงิน", "คว่ำบาตร", "นัดหยุดงาน", "ความไม่สงบ", "ความปลอดภัย", "Direct PPA", "PDP", "SMR", "LNG", "FID"]
def looks_generic_or_short_one(bullets) -> bool:
    if not bullets or not isinstance(bullets, list):
        return True
    bullets = [str(x).strip() for x in bullets if str(x).strip()]
    joined = " ".join(bullets).lower()
    generic_hit = any(p.replace(" ", "") in joined.replace(" ", "") for p in GENERIC_PATTERNS)
    specific_hit = any(k.lower() in joined for k in SPECIFIC_HINTS)
    too_short = len(joined) < 70
    return (generic_hit and not specific_hit) or too_short

def rewrite_impact_bullet_one_thai(news, bullets):
    prompt = f"""
คุณคือ Analyst ด้านพลังงาน
ช่วยเขียน "ผลกระทบ" ให้เหลือ 1 bullet เท่านั้น (1–2 ประโยค) โดยต้องเฉพาะเจาะจงและโยงกับข่าวจริง

ข้อกำหนด:
- เขียนเป็นภาษาไทยเท่านั้น ห้ามใช้ภาษาอังกฤษ
- ต้องอธิบายเส้นทางผลกระทบ: ประเด็นข่าว -> กลไก -> ผลกระทบต่อการลงทุน/โครงการ/ความเสี่ยง
- ต้องมีคำ/วลีจาก title/summary อย่างน้อย 1 จุด (ห้ามเดา)
- ห้ามปนบริบท: ห้ามพูดถึงประเทศ/เหตุการณ์/คำสำคัญที่ไม่อยู่ในหัวข้อ/สรุป
- ความยาวประมาณ 25–45 คำ

หัวข้อ: {news.get("title","")}
สรุป: {news.get("summary","")}

bullet เดิม:
{json.dumps(bullets, ensure_ascii=False)}

ตอบเป็น JSON เท่านั้น:
{{"impact_bullets": ["..."]}}
"""
    text = call_groq_with_retries(prompt, temperature=0.55)
    data = _extract_json_object(text)
    if isinstance(data, dict) and isinstance(data.get("impact_bullets"), list):
        out = diversify_bullets(clean_bullets(data["impact_bullets"])[:1])
        return out
    return diversify_bullets(clean_bullets(bullets))[:1]

# ============================================================================================================
# LLM selection (ไทย-only) — ดึง หมวด/ประเทศ/ผู้เกี่ยวข้อง/โครงการหรือสินทรัพย์ + bullet
# ============================================================================================================

def groq_batch_tag_and_filter(news_list: List[Dict[str, Any]], chunk_size: int = 10) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for i in range(0, len(news_list), chunk_size):
        chunk = news_list[i:i + chunk_size]
        payload = []
        for idx, n in enumerate(chunk):
            payload.append({
                "id": idx,
                "feed_section": (n.get("feed_section") or "").strip(),  # domestic / international
                "title": n.get("title", ""),
                "summary": n.get("summary", ""),
            })

        prompt = f"""
คุณเป็นผู้ช่วยคัดเลือกข่าว “พลังงานเชิงนโยบาย/โครงสร้างตลาด/โครงการลงทุน/ความเสี่ยง” เพื่อส่งสรุปให้ผู้บริหาร

คัดเฉพาะข่าวที่เข้ากลุ่มนี้:
1) นโยบาย/กำกับดูแลพลังงาน-ไฟฟ้า (PDP, tariff, regulator, Direct PPA)
2) โครงการลงทุนพลังงาน/โครงสร้างพื้นฐาน (LNG project/FID, terminal, pipeline, financing, EPC, supply chain)
3) ความมั่นคง/ความปลอดภัยสินทรัพย์พลังงาน (แท่นขุด/ท่อ/ท่าเรือ/โดรน/ความไม่สงบ)
4) เทคโนโลยีพลังงานเปลี่ยนผ่าน (SMR/นิวเคลียร์/CCUS/ไฮโดรเจน/AI-energy)
5) ภูมิรัฐศาสตร์-การค้า-คว่ำบาตรที่กระทบพลังงาน (crude/LNG flows)

ตัดทิ้งทันทีถ้าเป็น:
- lifestyle/บันเทิง/กีฬา/ไวรัล
- ข่าวเชิงโฆษณาไร้สาระ ไม่มีผลต่อกติกา/การลงทุน/ความเสี่ยง
- ข่าว “ราคา” แบบวันต่อวัน ที่ไม่มีประเด็นโครงสร้าง/นโยบาย/โครงการ/ความเสี่ยง

กติกาผลลัพธ์:
- เขียนภาษาไทยเท่านั้น
- evidence ต้องเป็น “วลีที่คัดมาจาก title/summary” เท่านั้น (ห้ามแต่งเพิ่ม)
- impact_bullets ต้องมี 1 bullet (1–2 ประโยค, 25–45 คำ) และต้องโยง: ประเด็นข่าว -> กลไก -> ผลกระทบ
- projects ให้ดึง “ชื่อโครงการ/สินทรัพย์/แผน/มาตรการ” ที่ถูกกล่าวถึง (เช่น PDP, Direct PPA, SMR 600MW, LNG export terminal, Hail and Ghasha) ถ้ามี
- partners ให้ดึง “บริษัท/องค์กร/ผู้เกี่ยวข้อง” ที่ถูกกล่าวถึง (เช่น EGCO, GPSC, ADNOC, Baker Hughes) ถ้ามี
- country: ประเทศหลักของข่าว (ถ้าเป็นข่าวไทยให้เป็น Thailand)
- section: domestic ถ้าเป็นข่าวไทย/บริบทไทย, international ถ้าเป็นต่างประเทศ (ถ้าไม่แน่ใจ ใช้ feed_section เป็นค่าเริ่มต้น)

ตอบเป็น JSON เท่านั้น:
{{
 "items":[
  {{
   "id":0,
   "is_relevant": true/false,
   "section":"domestic|international",
   "country":"Thailand|...",
   "topic_category":"policy_regulatory|security|gas_lng|tech_transition|macro_geo|other",
   "projects":["...","..."],
   "partners":["...","..."],
   "impact_bullets":["..."],
   "impact_level":"low|medium|high|unknown",
   "evidence":["...","..."],
   "why_relevant":"..."
  }}
 ]
}}

ข่าวชุดนี้:
{json.dumps(payload, ensure_ascii=False)}
"""
        text = call_groq_with_retries(prompt, temperature=0.35)
        data = _extract_json_object(text)

        if not (isinstance(data, dict) and isinstance(data.get("items"), list)):
            for _ in chunk:
                results.append({"is_relevant": False})
            continue

        by_id = {}
        for it in data["items"]:
            if isinstance(it, dict) and "id" in it:
                by_id[it.get("id")] = it

        for idx, _n in enumerate(chunk):
            t = by_id.get(idx, {"is_relevant": False})
            if not isinstance(t, dict):
                t = {"is_relevant": False}
            results.append(t)

    return results

# ============================================================================================================
# Window: 21:00 yesterday -> 06:00 today (Bangkok)
# ============================================================================================================

def fetch_news_window():
    now_local = datetime.now(bangkok_tz)
    start = (now_local - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
    end = now_local.replace(hour=6, minute=0, second=0, microsecond=0)

    out = []
    for site, feed_section, url in NEWS_FEEDS:
        try:
            feed = parse_feed_with_timeout(url)
            entries = list(feed.entries or [])[:MAX_ENTRIES_PER_FEED]

            for e in entries:
                pub = getattr(e, "published", None) or getattr(e, "updated", None)
                if not pub:
                    continue

                dt = dateutil_parser.parse(pub)
                if dt.tzinfo is None:
                    dt = bangkok_tz.localize(dt)
                dt_local = dt.astimezone(bangkok_tz)

                if not (start <= dt_local <= end):
                    continue

                link = _normalize_link(getattr(e, "link", "") or "")
                if not link:
                    continue

                title = (getattr(e, "title", "") or "").strip()
                summary_raw = getattr(e, "summary", "") or ""
                summary = re.sub(r"\s+", " ", re.sub("<.*?>", " ", summary_raw)).strip()

                out.append({
                    "site": site,
                    "feed_section": feed_section,  # domestic / international
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": dt_local,
                })
        except Exception as ex:
            print(f"[WARN] feed failed: {site}/{feed_section} -> {type(ex).__name__}: {ex}")
            continue

    # unique by link
    uniq, seen = [], set()
    for n in out:
        k = _normalize_link(n["link"])
        if k and k not in seen:
            seen.add(k)
            uniq.append(n)

    uniq.sort(key=lambda x: x["published"], reverse=True)
    return uniq

# ============================================================================================================
# FLEX (ไทย + bullet เดียว + fallback รูปเสมอ) + projects/partners
# ============================================================================================================

def _shorten(items, take=4):
    items = items or []
    items = [str(x).strip() for x in items if str(x).strip()]
    if not items:
        return "-"
    if len(items) <= take:
        return ", ".join(items)
    return ", ".join(items[:take]) + f" +{len(items)-take}"

def create_flex(news_items):
    now_txt = datetime.now(bangkok_tz).strftime("%d/%m/%Y")
    bubbles = []

    def _section_th(s: str) -> str:
        s = (s or "").strip().lower()
        return "ข่าวในประเทศ" if s == "domestic" else "ข่าวต่างประเทศ"

    def _cat_th(c: str) -> str:
        c = (c or "").strip().lower()
        mp = {
            "policy_regulatory": "นโยบาย/กำกับดูแล",
            "security": "ความมั่นคง/ความปลอดภัย",
            "gas_lng": "ก๊าซธรรมชาติ/ LNG",
            "tech_transition": "เทคโนโลยีพลังงาน",
            "macro_geo": "ภูมิรัฐศาสตร์/มหภาค",
            "other": "อื่นๆ",
        }
        return mp.get(c, "อื่นๆ")

    for n in news_items:
        bullets = clean_bullets(n.get("impact_bullets") or [])[:1]
        section = (n.get("section") or n.get("feed_section") or "international").strip().lower()
        country = (n.get("country") or "ไม่ระบุ").strip()
        cat = _cat_th(n.get("topic_category") or "other")

        projects = n.get("projects") or []
        partners = n.get("partners") or []

        link = n.get("final_url") or n.get("link") or "https://news.google.com/"
        img = n.get("image") or DEFAULT_HERO_URL
        if not _is_good_image_url(img):
            img = DEFAULT_HERO_URL

        cred_txt = ""
        if SHOW_SOURCE_RATING:
            rating_th = (n.get("source_rating_th") or "").strip()
            domain = (n.get("source_domain") or "").strip()
            score = n.get("source_score")
            if rating_th:
                cred_txt = f"ความน่าเชื่อถือ: {rating_th} (score {score}) · {domain}"

        contents = [
            {"type": "text", "text": (n.get("title", "")[:140]), "wrap": True, "weight": "bold", "size": "lg"},
            {
                "type": "box",
                "layout": "baseline",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": n.get("published").strftime("%d/%m/%Y %H:%M"), "size": "sm", "color": "#666666", "flex": 0},
                    {"type": "text", "text": f"{_section_th(section)} | {country} | {cat}", "size": "sm", "color": "#1E90FF", "wrap": True},
                ],
            },
        ]

        if projects:
            contents.append({"type": "text", "text": f"ประเด็น/โครงการ/สินทรัพย์: {_shorten(projects, 4)}", "size": "sm", "color": "#666666", "wrap": True, "margin": "sm"})
        if partners:
            contents.append({"type": "text", "text": f"ผู้เกี่ยวข้อง: {_shorten(partners, 6)}", "size": "sm", "color": "#666666", "wrap": True, "margin": "xs"})
        if cred_txt:
            contents.append({"type": "text", "text": cred_txt, "size": "xs", "color": "#666666", "wrap": True, "margin": "sm"})

        contents.append({"type": "text", "text": "ผลกระทบ", "size": "lg", "weight": "bold", "color": "#000000", "margin": "lg"})

        if bullets:
            contents.append({"type": "text", "text": f"• {bullets[0]}", "wrap": True, "size": "md", "color": "#000000", "weight": "bold", "margin": "xs"})
        else:
            contents.append({"type": "text", "text": "• (ไม่มีข้อความผลกระทบ)", "wrap": True, "size": "md", "color": "#000000", "weight": "bold", "margin": "xs"})

        bubbles.append({
            "type": "bubble",
            "size": "mega",
            "hero": {"type": "image", "url": img, "size": "full", "aspectRatio": "16:9", "aspectMode": "cover"},
            "body": {"type": "box", "layout": "vertical", "contents": contents},
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "button", "style": "primary", "color": "#1DB446",
                     "action": {"type": "uri", "label": "อ่านต่อ", "uri": link}}
                ],
            },
        })

    return [{
        "type": "flex",
        "altText": f"สรุปข่าวพลังงาน {now_txt}",
        "contents": {"type": "carousel", "contents": bubbles},
    }]

# ============================================================================================================
# LINE send
# ============================================================================================================

def send_to_line(messages):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}

    for i, msg in enumerate(messages, 1):
        payload = {"messages": [msg]}
        print("=== LINE PAYLOAD(meta) ===")
        print(json.dumps({"messages": [{"type": msg.get("type"), "altText": msg.get("altText")}]} , ensure_ascii=False))

        if DRY_RUN:
            print("[DRY_RUN] ไม่ส่งจริง")
            continue

        r = S.post(url, headers=headers, json=payload, timeout=15)
        print(f"Send {i}: {r.status_code}")
        if r.status_code >= 300:
            print("Response:", r.text[:1200])
            break

# ============================================================================================================
# MAIN
# ============================================================================================================

def main():
    deadline = None
    if RUN_DEADLINE_MIN > 0:
        deadline = time.time() + RUN_DEADLINE_MIN * 60

    print("ดึงข่าว...")
    all_news = fetch_news_window()
    print("จำนวนข่าวดิบทั้งหมด:", len(all_news))
    if not all_news:
        print("ไม่พบข่าวในช่วงเวลา")
        return

    sent = load_sent_links()

    # ---------- Pre-filter (ตัดซ้ำ/ตัดที่ส่งแล้ว + จำกัดแยก domestic/international) ----------
    domestic, international = [], []
    for n in all_news:
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่าง pre-filter (หยุด)")
            break

        if _normalize_link(n["link"]) in sent:
            continue

        sec = (n.get("feed_section") or "international").strip().lower()
        if sec == "domestic":
            domestic.append(n)
        else:
            international.append(n)

    if MAX_ITEMS_DOMESTIC is not None:
        domestic = domestic[:MAX_ITEMS_DOMESTIC]
    if MAX_ITEMS_INTERNATIONAL is not None:
        international = international[:MAX_ITEMS_INTERNATIONAL]

    selected = domestic + international
    selected.sort(key=lambda x: x["published"], reverse=True)
    selected = dedupe_near_titles(selected, threshold=0.88)
    print("จำนวนข่าวหลังตัดซ้ำใกล้เคียง:", len(selected))

    if MAX_LLM_ITEMS is not None:
        selected = selected[:MAX_LLM_ITEMS]

    print("จำนวนข่าวที่จะประเมินด้วย LLM:", len(selected))
    if not selected:
        print("ไม่มีข่าวให้ประเมิน")
        return

    # ---------- Resolve final URL + credibility ----------
    for n in selected:
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่าง resolve url (หยุด)")
            break

        original = n.get("link", "")
        final_url = resolve_final_url(original)

        # กัน final_url หลุดเป็น tracker
        if _get_domain(final_url) in TRACKER_HOSTS or any(_get_domain(final_url).endswith(x) for x in TRACKER_HOSTS):
            final_url = original

        n["final_url"] = final_url

        cred = assess_source_credibility(original, final_url, n.get("title", ""))
        n["source_domain"] = cred["domain"]
        n["source_score"] = cred["score"]
        n["source_rating"] = cred["rating"]
        n["source_rating_th"] = cred["rating_th"]
        n["source_signals"] = cred["signals"]

    if MIN_SOURCE_SCORE > 0:
        before = len(selected)
        selected = [n for n in selected if int(n.get("source_score", 0)) >= MIN_SOURCE_SCORE]
        print(f"กรองตามความน่าเชื่อถือ (score>={MIN_SOURCE_SCORE}): {before} -> {len(selected)}")
        if not selected:
            print("ไม่เหลือข่าวหลังกรองความน่าเชื่อถือ")
            return

    # ---------- LLM selection ----------
    try:
        tags = groq_batch_tag_and_filter(selected, chunk_size=LLM_BATCH_SIZE)
    except Exception as e:
        if _is_429(e):
            print("Groq 429: งด LLM รอบนี้ (ไม่ล้มทั้งงาน)")
            tags = [{"is_relevant": False} for _ in selected]
        else:
            raise

    final = []
    for n, tag in zip(selected, tags):
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่าง LLM apply (หยุด)")
            break

        if not isinstance(tag, dict) or not tag.get("is_relevant"):
            continue

        topic_category = (tag.get("topic_category") or "").strip().lower()
        if topic_category == "other":
            continue

        title = n.get("title", "")
        summary = n.get("summary", "")

        evidence = tag.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        evidence = [str(x).strip() for x in evidence if str(x).strip()][:2]
        if not validate_evidence_in_text(title, summary, evidence):
            continue

        bullets = diversify_bullets(clean_bullets(tag.get("impact_bullets") or [])[:1])
        if not bullets:
            continue

        # กันอังกฤษหลุด
        if is_mostly_english(bullets[0]):
            bullets = rewrite_impact_bullet_one_thai(n, bullets)

        # ถ้ายัง generic/สั้น -> rewrite ไทย
        if ENABLE_IMPACT_REWRITE and (looks_generic_or_short_one(bullets) or is_mostly_english(bullets[0])):
            bullets = rewrite_impact_bullet_one_thai(n, bullets)

        bullets = diversify_bullets(clean_bullets(bullets)[:1])
        if not bullets:
            continue

        if is_mostly_english(bullets[0]):
            continue

        if not guard_cross_topic(title, summary, bullets):
            continue

        if not has_meaningful_impact(bullets):
            continue

        # enrich fields from LLM
        n["section"] = (tag.get("section") or n.get("feed_section") or "international").strip().lower()
        n["country"] = (tag.get("country") or ("Thailand" if n["section"] == "domestic" else "Unknown")).strip()

        projects = tag.get("projects") or []
        partners = tag.get("partners") or []
        if not isinstance(projects, list):
            projects = [str(projects)]
        if not isinstance(partners, list):
            partners = [str(partners)]
        projects = [str(x).strip() for x in projects if str(x).strip()][:8]
        partners = [str(x).strip() for x in partners if str(x).strip()][:10]

        n["topic_category"] = topic_category
        n["projects"] = projects
        n["partners"] = partners
        n["impact_bullets"] = bullets[:1]
        n["impact_level"] = (tag.get("impact_level") or "unknown")
        n["evidence"] = evidence
        n["why_relevant"] = (tag.get("why_relevant") or "").strip()

        final.append(n)

    print("จำนวนข่าวผ่านเงื่อนไข:", len(final))
    if not final:
        print("ไม่มีข่าวที่ผ่านเงื่อนไขวันนี้")
        return

    # ---------- Images ----------
    for n in final:
        if deadline and time.time() > deadline:
            print("ถึง deadline ระหว่างหา image (หยุด)")
            break

        img = fetch_article_image(n.get("final_url") or n.get("link", ""))
        if _is_good_image_url(img or ""):
            n["image"] = img
        else:
            n["image"] = DEFAULT_HERO_URL

        time.sleep(0.10)

    final.sort(key=lambda x: x["published"], reverse=True)
    send_cap = len(final) if MAX_SEND_ITEMS is None else min(MAX_SEND_ITEMS, len(final))
    msgs = create_flex(final[:send_cap])
    send_to_line(msgs)

    save_sent_links([n.get("final_url") or n.get("link") for n in final])
    print("เสร็จสิ้น (Groq calls:", GROQ_CALLS, ")")

if __name__ == "__main__":
    main()
