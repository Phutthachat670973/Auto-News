# -*- coding: utf-8 -*-
"""
News Processor Service
ประมวลผลและกรองข่าว
"""

import time
import feedparser
from datetime import datetime, timedelta
from dateutil import parser as dateutil_parser

from config.settings import (
    TZ, MAX_PER_FEED, WINDOW_HOURS, DEBUG_FILTERING,
    USE_LLM_SUMMARY, GROQ_API_KEY, GROQ_MODEL, GROQ_ENDPOINT
)
from data.feeds import FEEDS
from data.projects import PROJECTS_BY_COUNTRY
from filters.keyword_filter import KeywordFilter
from filters.deduplication import EnhancedDeduplication
from utils.storage import read_sent_links
from utils.url_utils import normalize_url, shorten_google_news_url, extract_domain
from utils.text_utils import create_simple_summary

# News sources mapping
NEWS_SOURCES = {
    'reuters.com': 'Reuters',
    'bloomberg.com': 'Bloomberg',
    'bangkokpost.com': 'Bangkok Post',
    'thansettakij.com': 'ฐานเศรษฐกิจ',
    'posttoday.com': 'Post Today',
    'prachachat.net': 'ประชาชาติธุรกิจ',
    'mgronline.com': 'ผู้จัดการออนไลน์',
    'komchadluek.net': 'คมชัดลึก',
    'nationthailand.com': 'The Nation Thailand',
}

class NewsProcessor:
    """ประมวลผลและกรองข่าว"""
    
    def __init__(self):
        self.sent_links = read_sent_links()
        self.dedup = EnhancedDeduplication()
        self.filter_stats = {
            'total_processed': 0,
            'filtered_by': {
                'no_title': 0,
                'no_url': 0,
                'already_sent': 0,
                'out_of_window': 0,
                'not_allowed_source': 0,
                'invalid_energy_news': 0,
                'no_country': 0,
                'duplicate': 0,
                'passed': 0
            }
        }
    
    def fetch_and_filter_news(self):
        """ดึงและกรองข่าวจากทุก feeds"""
        all_news = []
        
        for feed_name, feed_type, feed_url in FEEDS:
            print(f"\n[Fetching] {feed_name} ({feed_type})...")
            
            try:
                entries = self._fetch_feed_with_retry(feed_name, feed_url)
                
                for entry in entries[:MAX_PER_FEED]:
                    self.filter_stats['total_processed'] += 1
                    news_item, filter_reason = self._process_entry(entry, feed_name, feed_type)
                    
                    if news_item:
                        all_news.append(news_item)
                        self.filter_stats['filtered_by']['passed'] += 1
                        print(f"  ✓ {news_item['title'][:50]}...")
                    elif filter_reason and DEBUG_FILTERING:
                        print(f"  ✗ {filter_reason}")
                        
            except Exception as e:
                print(f"  ✗ Error: {str(e)}")
        
        # Sort by published date
        all_news.sort(key=lambda x: -((x.get('published_dt') or datetime.min).timestamp()))
        
        return all_news
    
    def _fetch_feed_with_retry(self, name: str, url: str, retries: int = 3):
        """ดึง feed พร้อมระบบ retry"""
        for attempt in range(retries):
            try:
                print(f"[FEED] ดึงข้อมูลจาก {name} (ครั้งที่ {attempt+1}/{retries})...")
                d = feedparser.parse(url)
                entries = d.entries or []
                print(f"[FEED] {name}: พบ {len(entries)} entries")
                return entries
            except Exception as e:
                print(f"[FEED] {name}: เกิดข้อผิดพลาด - {str(e)}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    return []
    
    def _process_entry(self, entry, feed_name: str, feed_type: str):
        """ประมวลผล entry หนึ่งรายการ"""
        # Parse entry
        item = self._parse_entry(entry, feed_name, feed_type)
        
        # Filter 1: ไม่มีหัวข้อ
        if not item["title"]:
            self.filter_stats['filtered_by']['no_title'] += 1
            return None, "ไม่มีหัวข้อข่าว"
        
        # Filter 2: ไม่มี URL
        if not item["url"]:
            self.filter_stats['filtered_by']['no_url'] += 1
            return None, "ไม่มี URL"
        
        # Filter 3: ส่งแล้ว
        if item["canon_url"] in self.sent_links or item["url"] in self.sent_links:
            self.filter_stats['filtered_by']['already_sent'] += 1
            return None, f"ส่งแล้ว: {item['title'][:30]}..."
        
        # Filter 4: นอกช่วงเวลา
        if item["published_dt"] and not self._in_time_window(item["published_dt"]):
            self.filter_stats['filtered_by']['out_of_window'] += 1
            return None, f"เกินเวลา: {item['title'][:30]}..."
        
        # Filter 5: ตรวจสอบคำสำคัญ
        full_text = f"{item['title']} {item['summary']}"
        is_valid, reason, details = KeywordFilter.check_valid_energy_news(full_text)
        
        if not is_valid:
            self.filter_stats['filtered_by']['invalid_energy_news'] += 1
            return None, f"{reason}: {item['title'][:30]}..."
        
        # Filter 6: ตรวจสอบประเทศ
        country = KeywordFilter.detect_country(full_text)
        
        if not country:
            if feed_type == "direct":
                country = "Thailand"
            else:
                self.filter_stats['filtered_by']['no_country'] += 1
                return None, f"ไม่พบประเทศที่เกี่ยวข้อง: {item['title'][:30]}..."
        
        # เพิ่มข้อมูลเพิ่มเติม
        project_hints = PROJECTS_BY_COUNTRY.get(country, [])[:2]
        display_url = item["canon_url"] or item["url"]
        source_name = self._get_source_name(display_url)
        
        final_item = {
            'title': item['title'][:100],
            'url': item['url'],
            'canon_url': item['canon_url'],
            'source_name': source_name,
            'domain': extract_domain(display_url),
            'summary': item['summary'][:200],
            'published_dt': item['published_dt'],
            'country': country,
            'project_hints': project_hints,
            'llm_summary': '',
            'feed': feed_name,
            'feed_type': feed_type,
            'simple_summary': create_simple_summary(full_text, 100)
        }
        
        # Filter 7: ตรวจสอบซ้ำ
        if not self.dedup.add_item(final_item):
            self.filter_stats['filtered_by']['duplicate'] += 1
            return None, f"ข่าวซ้ำ: {item['title'][:30]}..."
        
        return final_item, None
    
    def _parse_entry(self, e, feed_name: str, section: str):
        """แปลง feedparser entry เป็น dict"""
        title = (getattr(e, "title", "") or "").strip()
        link = (getattr(e, "link", "") or "").strip()
        summary = (getattr(e, "summary", "") or "").strip()
        published = getattr(e, "published", None) or getattr(e, "updated", None)

        if not published and hasattr(e, 'published_parsed'):
            try:
                import time as time_module
                published = time_module.strftime('%Y-%m-%dT%H:%M:%SZ', e.published_parsed)
            except:
                pass

        try:
            published_dt = dateutil_parser.parse(published) if published else None
            if published_dt and published_dt.tzinfo is None:
                published_dt = TZ.localize(published_dt)
            if published_dt:
                published_dt = published_dt.astimezone(TZ)
        except Exception:
            published_dt = None

        canon = shorten_google_news_url(link)

        return {
            "title": title,
            "url": normalize_url(link),
            "canon_url": normalize_url(canon),
            "summary": summary,
            "published_dt": published_dt,
            "feed": feed_name,
            "section": section,
        }
    
    def _in_time_window(self, published_dt: datetime) -> bool:
        """ตรวจสอบว่าอยู่ในช่วงเวลาที่กำหนดหรือไม่"""
        if not published_dt:
            return False
        return published_dt >= (datetime.now(TZ) - timedelta(hours=WINDOW_HOURS))
    
    def _get_source_name(self, url: str) -> str:
        """ดึงชื่อเว็บข่าวจาก URL"""
        domain = extract_domain(url)
        if not domain:
            return domain
        
        for source_domain, source_name in NEWS_SOURCES.items():
            if source_domain in domain:
                return source_name
        
        return domain
