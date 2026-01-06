# -*- coding: utf-8 -*-

import os
import re
import json
import time
import random
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, unquote
from typing import List, Dict, Tuple, Optional

import requests
import feedparser
import pytz
from dateutil import parser as dateutil_parser

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# =============================================================================
# ENV / CONFIG
# =============================================================================
TZ = pytz.timezone(os.getenv("TZ", "Asia/Bangkok"))

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
if not LINE_CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("Missing LINE_CHANNEL_ACCESS_TOKEN")

# Groq (OpenAI-compatible)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile").strip()  # ใช้โมเดลที่ใหญ่ขึ้นสำหรับการวิเคราะห์ที่ซับซ้อน
GROQ_ENDPOINT = os.getenv("GROQ_ENDPOINT", "https://api.groq.com/openai/v1/chat/completions").strip()
USE_LLM_ANALYSIS = os.getenv("USE_LLM_ANALYSIS", "1").strip().lower() in ["1", "true", "yes", "y"]

WINDOW_HOURS = int(os.getenv("WINDOW_HOURS", "72"))  # เพิ่มเวลา window เป็น 72 ชม.
MAX_PER_FEED = int(os.getenv("MAX_PER_FEED", "50"))  # ลดจำนวนข่าวต่อ feed

# LLM configuration
LLM_ANALYSIS_MAX_TOKENS = int(os.getenv("LLM_ANALYSIS_MAX_TOKENS", "1500"))
LLM_BATCH_SIZE = int(os.getenv("LLM_BATCH_SIZE", "5"))  # ลด batch size สำหรับการวิเคราะห์ที่ละเอียด
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))
LLM_BASE_BACKOFF = float(os.getenv("LLM_BASE_BACKOFF", "2.0"))

# =============================================================================
# PROJECT DATABASE (ปรับให้ละเอียดขึ้น)
# =============================================================================
PROJECTS_DETAILED = {
    "Thailand": {
        "projects": [
            "โครงการจี 1/61", "โครงการจี 2/61", "โครงการอาทิตย์", "Arthit",
            "โครงการเอส 1", "S1", "โครงการสัมปทาน 4", "Contract 4",
            "โครงการพีทีทีอีพี 1", "PTTEP 1", "โครงการบี 6/27",
            "โครงการแอล 22/43", "โครงการอี 5", "E5",
            "โครงการจี 4/43", "โครงการสินภูฮ่อม", "Sinphuhorm",
            "โครงการบี 8/32", "B8/32", "9A", "9เอ",
            "โครงการจี 4/48", "โครงการจี 12/48",
            "โครงการจี 1/65", "โครงการจี 3/65",
            "โครงการแอล 53/43", "โครงการแอล 54/43"
        ],
        "keywords": [
            "กระทรวงพลังงาน", "กรมธุรกิจพลังงาน", "กฟผ.", "การไฟฟ้าส่วนภูมิภาค",
            "กองทุนน้ำมันเชื้อเพลิง", "คณะกรรมการกำกับกิจการพลังงาน",
            "สำนักงานนโยบายและแผนพลังงาน", "ราคาก๊าซธรรมชาติ",
            "แผนพัฒนากำลังผลิตไฟฟ้า", "PDP", "ค่า Ft", "อัตราค่าไฟฟ้า",
            "โรงไฟฟ้า", "พลังงานทดแทน", "โซลาร์เซลล์", "พลังงานลม",
            "พลังงานชีวมวล", "พลังงานความร้อนใต้พิภพ"
        ],
        "entities": [
            "รัฐมนตรีพลังงาน", "อธิบดีกรมธุรกิจพลังงาน", "ผู้ว่าการการไฟฟ้าส่วนภูมิภาค",
            "คณะกรรมการกกพ.", "บริษัท ปตท. จำกัด (มหาชน)", "บริษัท กฟผ. จำกัด (มหาชน)"
        ]
    },
    # ... กำหนดรายละเอียดสำหรับประเทศอื่นๆในทำนองเดียวกัน
}

# =============================================================================
# FEEDS (ปรับให้เฉพาะเจาะจงมากขึ้น)
# =============================================================================
def gnews_rss(q: str, hl="en", gl="US", ceid="US:en") -> str:
    return f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"

FEEDS = [
    ("GoogleNewsTH_EnergyOfficial", "official_thai", gnews_rss(
        '(site:ratchakitcha.soc.go.th OR site:energy.go.th OR site:egat.co.th OR site:pptplc.com OR site:pttep.com) AND (พลังงาน OR ก๊าซ OR ไฟฟ้า OR น้ำมัน)',
        hl="th", gl="TH", ceid="TH:th"
    )),
    ("GoogleNewsTH_FinanceEnergy", "finance_thai", gnews_rss(
        '(site:bangkokbiznews.com OR site:thunhoon.com OR site:posttoday.com OR site:manager.co.th) AND (พลังงาน OR พลังงานไฟฟ้า OR โรงไฟฟ้า)',
        hl="th", gl="TH", ceid="TH:th"
    )),
    ("GoogleNewsEN_EnergyPolicy", "policy_international", gnews_rss(
        '(energy policy OR electricity tariff OR power regulation OR LNG OR natural gas) AND (Thailand OR Malaysia OR Vietnam OR Indonesia OR Middle East)',
        hl="en", gl="US", ceid="US:en"
    )),
    ("Reuters_Energy", "international", "https://www.reutersagency.com/feed/?best-topics=energy-environment&post_type=best"),
    ("Bloomberg_Energy", "international", "https://news.google.com/rss/search?q=site:bloomberg.com+energy+policy&hl=en&gl=US&ceid=US:en"),
]

# =============================================================================
# LLM ANALYZER CLASS
# =============================================================================
class LLMNewsAnalyzer:
    def __init__(self, api_key: str, endpoint: str, model: str):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        
    def _call_api_with_retry(self, messages: List[Dict], max_tokens: int = 1500) -> str:
        """เรียกใช้ Groq API พร้อม retry mechanism"""
        if not self.api_key:
            return ""
            
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,  # ลด temperature เพื่อให้ผลลัพธ์มีความเป็นทางการมากขึ้น
            "max_tokens": max_tokens,
            "top_p": 0.95
        }
        
        for attempt in range(LLM_MAX_RETRIES):
            try:
                response = requests.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=payload,
                    timeout=60
                )
                
                if response.status_code == 429:
                    wait_time = (LLM_BASE_BACKOFF ** (attempt + 1)) + random.uniform(0.0, 1.0)
                    print(f"[LLM] Rate limited, waiting {wait_time:.1f}s (attempt {attempt + 1})")
                    time.sleep(wait_time)
                    continue
                    
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
                
            except requests.exceptions.RequestException as e:
                if attempt == LLM_MAX_RETRIES - 1:
                    print(f"[LLM] Error after {LLM_MAX_RETRIES} attempts: {e}")
                    return ""
                wait_time = (LLM_BASE_BACKOFF ** (attempt + 1))
                time.sleep(wait_time)
                
        return ""
    
    def analyze_news_relevance(self, title: str, summary: str, full_text: str = "") -> Dict:
        """
        วิเคราะห์ข่าวด้วย LLM เพื่อประเมินความเกี่ยวข้องกับพลังงานและการลงทุน
        """
        if not self.api_key:
            return self._get_fallback_analysis()
            
        system_prompt = """คุณเป็นผู้เชี่ยวชาญด้านการวิเคราะห์ข่าวพลังงานและการลงทุน
        หน้าที่ของคุณคือวิเคราะห์ข่าวและตอบกลับเป็น JSON เท่านั้นตาม format ด้านล่าง:
        
        {
            "is_relevant": boolean,  // ข่าวเกี่ยวข้องกับพลังงาน/การลงทุนด้านพลังงานหรือไม่
            "relevance_score": 0-100,  // คะแนนความเกี่ยวข้อง (สูง = เกี่ยวข้องมาก)
            "country": "ประเทศที่เกี่ยวข้อง",  // เช่น Thailand, Malaysia, Vietnam
            "project_names": ["ชื่อโครงการที่เกี่ยวข้อง"],  // ชื่อโครงการพลังงานที่เกี่ยวข้อง
            "topics": ["หัวข้อหลัก"],  // เช่น นโยบายพลังงาน, ราคาก๊าซ, การไฟฟ้า
            "is_official_news": boolean,  // เป็นข่าวทางการจากหน่วยงานรัฐหรือไม่
            "impact_level": "high|medium|low",  // ระดับผลกระทบต่อโครงการ
            "summary_analysis": "สรุปการวิเคราะห์สั้นๆ"  // ไม่เกิน 2 ประโยค
        }
        
        เกณฑ์การประเมิน:
        1. ข่าวทางการ: ประกาศราชกิจจา, มติคณะรัฐมนตรี, ประกาศกระทรวง, การแถลงข่าวทางการ
        2. ข่าวนโยบาย: นโยบายพลังงานใหม่, การปรับอัตราค่าไฟฟ้า, การเปลี่ยนแปลงกฎระเบียบ
        3. ข่าวโครงการ: การอนุมัติโครงการ, การเปลี่ยนแปลงแผน, ความคืบหน้าการก่อสร้าง
        4. ข่าวเศรษฐกิจ: ราคาก๊าซ/น้ำมัน, สัญญาซื้อขาย, การลงทุนใหม่
        """
        
        user_content = f"""โปรดวิเคราะห์ข่าวต่อไปนี้:
        
        หัวข้อ: {title}
        
        เนื้อหาสรุป: {summary}
        
        {f'เนื้อหาเพิ่มเติม: {full_text[:1000]}' if full_text else ''}
        
        ระบุเฉพาะข้อมูลที่แน่ชัดจากเนื้อหาข่าวเท่านั้น ห้ามเดาหรือสรุปข้อมูลนอกเหนือจากที่ให้ไว้"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
        result = self._call_api_with_retry(messages, LLM_ANALYSIS_MAX_TOKENS)
        
        if result:
            try:
                # แยก JSON ออกจากข้อความอื่นๆ
                json_match = re.search(r'\{.*\}', result, re.DOTALL)
                if json_match:
                    analysis = json.loads(json_match.group())
                    return self._validate_analysis(analysis)
            except json.JSONDecodeError:
                print(f"[LLM] Failed to parse JSON: {result[:200]}")
                
        return self._get_fallback_analysis()
    
    def _validate_analysis(self, analysis: Dict) -> Dict:
        """ตรวจสอบและแก้ไขผลลัพธ์จาก LLM"""
        validated = {
            "is_relevant": bool(analysis.get("is_relevant", False)),
            "relevance_score": min(100, max(0, int(analysis.get("relevance_score", 0)))),
            "country": str(analysis.get("country", "")).strip(),
            "project_names": [str(p).strip() for p in analysis.get("project_names", []) if p],
            "topics": [str(t).strip() for t in analysis.get("topics", []) if t],
            "is_official_news": bool(analysis.get("is_official_news", False)),
            "impact_level": str(analysis.get("impact_level", "low")).lower(),
            "summary_analysis": str(analysis.get("summary_analysis", "")).strip()[:200]
        }
        
        # ตรวจสอบประเทศ
        if validated["country"] and validated["country"] not in PROJECTS_DETAILED:
            validated["country"] = ""
            
        return validated
    
    def _get_fallback_analysis(self) -> Dict:
        """ใช้เมื่อ LLM ไม่สามารถวิเคราะห์ได้"""
        return {
            "is_relevant": False,
            "relevance_score": 0,
            "country": "",
            "project_names": [],
            "topics": [],
            "is_official_news": False,
            "impact_level": "low",
            "summary_analysis": ""
        }

# =============================================================================
# CONTENT FETCHER (ดึงเนื้อหาจริงจาก URL)
# =============================================================================
class ContentFetcher:
    @staticmethod
    def fetch_article_content(url: str) -> Tuple[str, bool]:
        """ดึงเนื้อหาจริงจาก URL และตรวจสอบว่าเป็นแหล่งข่าวทางการหรือไม่"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'th,en-US;q=0.7,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            html_content = response.text
            
            # ตรวจสอบว่าเป็นเว็บทางการหรือไม่
            is_official_source = ContentFetcher._check_official_source(url, html_content)
            
            # สกัดเนื้อหาหลัก
            content = ContentFetcher._extract_main_content(html_content)
            
            return content[:3000], is_official_source  # จำกัดความยาว
            
        except Exception as e:
            print(f"[Fetcher] Error fetching {url}: {e}")
            return "", False
    
    @staticmethod
    def _check_official_source(url: str, html: str) -> bool:
        """ตรวจสอบว่าเป็นแหล่งข่าวทางการ"""
        official_domains = [
            'ratchakitcha.soc.go.th',
            'energy.go.th',
            'egat.co.th',
            'pptplc.com',
            'pttep.com',
            'reuters.com',
            'bloomberg.com',
            'iea.org',
            'worldbank.org'
        ]
        
        domain = urlparse(url).netloc.lower()
        return any(domain.endswith(official_domain) for official_domain in official_domains)
    
    @staticmethod
    def _extract_main_content(html: str) -> str:
        """สกัดเนื้อหาหลักจาก HTML"""
        # ใช้ regex pattern ง่ายๆ สำหรับดึงเนื้อหา
        patterns = [
            r'<article[^>]*>(.*?)</article>',
            r'<div[^>]*class=["\'][^"\']*article[^"\']*["\'][^>]*>(.*?)</div>',
            r'<div[^>]*class=["\'][^"\']*content[^"\']*["\'][^>]*>(.*?)</div>',
            r'<main[^>]*>(.*?)</main>',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                # ลบ tag HTML
                content = re.sub(r'<[^>]+>', ' ', match.group(1))
                content = re.sub(r'\s+', ' ', content)
                return content.strip()
        
        return ""

# =============================================================================
# MAIN PROCESSING PIPELINE
# =============================================================================
class NewsProcessor:
    def __init__(self):
        self.llm_analyzer = LLMNewsAnalyzer(GROQ_API_KEY, GROQ_ENDPOINT, GROQ_MODEL)
        self.content_fetcher = ContentFetcher()
        self.sent_links = set()
        
    def process_feeds(self) -> List[Dict]:
        """ประมวลผลข่าวทั้งหมด"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"[Processing] Feed: {feed_name}")
            entries = self._fetch_feed_entries(feed_url)
            
            for entry in entries[:MAX_PER_FEED]:
                news_item = self._process_news_entry(entry, feed_name, feed_type)
                if news_item and self._should_include(news_item):
                    all_news.append(news_item)
        
        # เรียงลำดับตามความเกี่ยวข้อง
        all_news.sort(key=lambda x: (
            -x.get('analysis', {}).get('relevance_score', 0),
            -x.get('analysis', {}).get('is_official_news', False)
        ))
        
        return all_news[:MAX_MESSAGES_PER_RUN]
    
    def _fetch_feed_entries(self, feed_url: str):
        """ดึงข้อมูลจาก RSS feed"""
        try:
            feed = feedparser.parse(feed_url)
            return feed.entries
        except Exception as e:
            print(f"[Error] Failed to parse feed {feed_url}: {e}")
            return []
    
    def _process_news_entry(self, entry, feed_name: str, feed_type: str) -> Optional[Dict]:
        """ประมวลผลแต่ละข่าว"""
        title = getattr(entry, 'title', '').strip()
        url = getattr(entry, 'link', '').strip()
        summary = getattr(entry, 'summary', '').strip()
        
        if not title or not url:
            return None
        
        # ดึงเนื้อหาจริง
        full_content, is_official_source = self.content_fetcher.fetch_article_content(url)
        
        # วิเคราะห์ด้วย LLM
        if USE_LLM_ANALYSIS:
            analysis = self.llm_analyzer.analyze_news_relevance(title, summary, full_content)
        else:
            analysis = self.llm_analyzer._get_fallback_analysis()
        
        # เพิ่ม flag ว่าเป็นแหล่งข่าวทางการ
        analysis['is_official_source'] = is_official_source
        
        # ถ้าไม่เกี่ยวข้องข้าม
        if not analysis['is_relevant'] or analysis['relevance_score'] < 40:
            return None
        
        # สร้างรายการข่าว
        return {
            'title': title,
            'url': url,
            'summary': summary,
            'analysis': analysis,
            'feed_name': feed_name,
            'feed_type': feed_type,
            'timestamp': datetime.now(TZ)
        }
    
    def _should_include(self, news_item: Dict) -> bool:
        """ตรวจสอบว่าควรรวมข่าวนี้หรือไม่"""
        analysis = news_item.get('analysis', {})
        
        # เกณฑ์การกรอง
        criteria = [
            analysis.get('relevance_score', 0) >= 50,  # คะแนนความเกี่ยวข้อง
            analysis.get('is_official_news', False) or analysis.get('is_official_source', False),  # เป็นข่าวทางการ
            len(analysis.get('project_names', [])) > 0 or analysis.get('impact_level') in ['high', 'medium'],  # มีโครงการหรือผลกระทบ
        ]
        
        return any(criteria)

# =============================================================================
# LINE MESSAGE BUILDER (ปรับปรุง)
# =============================================================================
class LineMessageBuilder:
    @staticmethod
    def create_flex_message(news_items: List[Dict]) -> Dict:
        """สร้าง Flex Message สำหรับ LINE"""
        bubbles = []
        
        for item in news_items:
            bubble = LineMessageBuilder._create_news_bubble(item)
            if bubble:
                bubbles.append(bubble)
        
        if not bubbles:
            return None
        
        return {
            "type": "flex",
            "altText": f"สรุปข่าวพลังงานทางการ {datetime.now(TZ).strftime('%d/%m/%Y')}",
            "contents": {
                "type": "carousel",
                "contents": bubbles[:BUBBLES_PER_CAROUSEL]
            }
        }
    
    @staticmethod
    def _create_news_bubble(news_item: Dict) -> Dict:
        """สร้าง bubble สำหรับแต่ละข่าว"""
        analysis = news_item.get('analysis', {})
        
        # กำหนดสีตามระดับผลกระทบ
        color_map = {
            'high': '#FF6B6B',
            'medium': '#FFA726',
            'low': '#42A5F5'
        }
        impact_color = color_map.get(analysis.get('impact_level', 'low'), '#42A5F5')
        
        # ส่วนหัว
        header = {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": impact_color,
            "paddingAll": "10px",
            "contents": [
                {
                    "type": "text",
                    "text": "📰 ข่าวทางการ",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "sm"
                } if analysis.get('is_official_news') else {
                    "type": "text",
                    "text": "📊 ข่าววิเคราะห์",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "sm"
                }
            ]
        }
        
        # ส่วนเนื้อหา
        body_contents = [
            {
                "type": "text",
                "text": news_item['title'],
                "weight": "bold",
                "size": "lg",
                "wrap": True,
                "margin": "md"
            },
            {
                "type": "text",
                "text": f"ประเทศ: {analysis.get('country', 'N/A')}",
                "size": "sm",
                "color": "#666666",
                "margin": "sm"
            }
        ]
        
        # เพิ่มรายการโครงการ
        if analysis.get('project_names'):
            projects_text = ", ".join(analysis['project_names'][:3])
            body_contents.append({
                "type": "text",
                "text": f"โครงการ: {projects_text}",
                "size": "sm",
                "color": "#2E7D32",
                "wrap": True,
                "margin": "sm"
            })
        
        # เพิ่มหัวข้อ
        if analysis.get('topics'):
            topics_text = ", ".join(analysis['topics'][:3])
            body_contents.append({
                "type": "text",
                "text": f"หัวข้อ: {topics_text}",
                "size": "sm",
                "color": "#5D4037",
                "wrap": True,
                "margin": "sm"
            })
        
        # เพิ่มการวิเคราะห์สรุป
        if analysis.get('summary_analysis'):
            body_contents.append({
                "type": "text",
                "text": analysis['summary_analysis'],
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#424242"
            })
        
        # เพิ่มคะแนนความเกี่ยวข้อง
        body_contents.append({
            "type": "box",
            "layout": "baseline",
            "margin": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "ความเกี่ยวข้อง:",
                    "size": "sm",
                    "color": "#666666",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": f"{analysis.get('relevance_score', 0)}/100",
                    "size": "sm",
                    "color": impact_color,
                    "weight": "bold",
                    "flex": 1,
                    "align": "end"
                }
            ]
        })
        
        body = {
            "type": "box",
            "layout": "vertical",
            "contents": body_contents
        }
        
        # ส่วนล่าง (ปุ่ม)
        footer = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "อ่านข่าวเต็ม",
                        "uri": news_item['url']
                    }
                }
            ]
        }
        
        return {
            "type": "bubble",
            "size": "kilo",
            "header": header,
            "body": body,
            "footer": footer
        }

# =============================================================================
# MAIN EXECUTION
# =============================================================================
def main():
    print("=" * 60)
    print("เริ่มระบบติดตามข่าวพลังงานด้วย LLM")
    print("=" * 60)
    
    # ตรวจสอบ configuration
    if not GROQ_API_KEY and USE_LLM_ANALYSIS:
        print("[Warning] GROQ_API_KEY ไม่ได้กำหนดไว้ แต่ USE_LLM_ANALYSIS เปิดอยู่")
        print("[Info] จะใช้การกรองแบบพื้นฐานแทน")
    
    # เริ่มต้น processor
    processor = NewsProcessor()
    
    # ดึงและประมวลผลข่าว
    print("\n[Status] กำลังดึงและวิเคราะห์ข่าว...")
    relevant_news = processor.process_feeds()
    
    print(f"\n[Result] พบข่าวที่เกี่ยวข้องทั้งหมด: {len(relevant_news)} ข่าว")
    
    if not relevant_news:
        print("[Info] ไม่พบข่าวที่เกี่ยวข้องในวันนี้")
        return
    
    # สร้างข้อความสำหรับ LINE
    print("\n[Status] กำลังสร้างข้อความสำหรับ LINE...")
    message_builder = LineMessageBuilder()
    flex_message = message_builder.create_flex_message(relevant_news)
    
    if not flex_message:
        print("[Error] ไม่สามารถสร้างข้อความได้")
        return
    
    # ส่งไปยัง LINE
    if DRY_RUN:
        print("\n=== DRY RUN - Flex Message Preview ===")
        print(json.dumps(flex_message, ensure_ascii=False, indent=2))
    else:
        print("\n[Status] กำลังส่งข้อความไปยัง LINE...")
        success = send_line_message(flex_message)
        if success:
            print("[Success] ส่งข้อความสำเร็จ!")
        else:
            print("[Error] การส่งข้อความล้มเหลว")

if __name__ == "__main__":
    main()
