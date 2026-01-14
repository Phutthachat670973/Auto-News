# -*- coding: utf-8 -*-
"""
News Message Builder
สร้าง LINE Flex Message สำหรับข่าว
"""

from datetime import datetime
from config.settings import BUBBLES_PER_CAROUSEL, TZ
from utils.text_utils import cut, create_simple_summary

class NewsMessageBuilder:
    """สร้าง LINE Flex Message สำหรับข่าว"""
    
    @staticmethod
    def create_flex_bubble(news_item: dict) -> dict:
        """สร้าง Flex Bubble สำหรับข่าวหนึ่งข่าว"""
        title = cut(news_item.get('title', ''), 80)
        
        pub_dt = news_item.get('published_dt')
        time_str = pub_dt.strftime("%d/%m/%Y %H:%M") if pub_dt else ""
        
        colors = {
            "Thailand": "#FF6B6B",
            "Vietnam": "#4ECDC4",
            "Myanmar": "#FFD166",
            "Malaysia": "#06D6A0",
            "Indonesia": "#118AB2",
            "UAE": "#9D4EDD",
            "Oman": "#F15BB5",
            "Kazakhstan": "#00BBF9",
            "International": "#888888"
        }
        
        color = colors.get(news_item.get('country', 'International'), "#888888")
        
        contents = [
            {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": title,
                        "weight": "bold",
                        "size": "md",
                        "wrap": True,
                        "color": "#FFFFFF"
                    }
                ],
                "backgroundColor": color,
                "paddingAll": "12px",
                "cornerRadius": "8px"
            }
        ]
        
        metadata_parts = []
        if time_str:
            metadata_parts.append(time_str)
        if news_item.get('feed'):
            metadata_parts.append(news_item['feed'])
        
        if metadata_parts:
            contents.append({
                "type": "text",
                "text": " | ".join(metadata_parts),
                "size": "xs",
                "color": "#888888",
                "margin": "sm"
            })
        
        if news_item.get('source_name'):
            contents.append({
                "type": "text",
                "text": f"📰 {news_item['source_name']}",
                "size": "xs",
                "color": "#666666",
                "margin": "sm"
            })
        
        contents.append({
            "type": "text",
            "text": f"ประเทศ: {news_item.get('country', 'N/A')}",
            "size": "sm",
            "margin": "xs",
            "color": color,
            "weight": "bold"
        })
        
        if news_item.get('project_hints'):
            hints_text = ", ".join(news_item['project_hints'][:2])
            contents.append({
                "type": "text",
                "text": f"โครงการที่เกี่ยวข้อง: {hints_text}",
                "size": "sm",
                "color": "#2E7D32",
                "wrap": True,
                "margin": "xs"
            })
        
        summary_text = ""
        if news_item.get('llm_summary'):
            summary_text = news_item['llm_summary']
        elif news_item.get('simple_summary'):
            summary_text = news_item['simple_summary']
        elif news_item.get('summary'):
            summary_text = create_simple_summary(news_item['summary'], 120)
        
        if not summary_text or len(summary_text.strip()) < 10:
            summary_text = f"{news_item.get('title', 'ข่าวพลังงาน')[:60]}..."
        
        if summary_text:
            contents.append({
                "type": "text",
                "text": cut(summary_text, 120),
                "size": "sm",
                "wrap": True,
                "margin": "md",
                "color": "#424242"
            })
        
        bubble = {
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": contents,
                "paddingAll": "12px",
                "spacing": "sm"
            }
        }
        
        url = news_item.get('canon_url') or news_item.get('url')
        if url and len(url) < 1000:
            bubble["footer"] = {
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
                            "uri": url
                        }
                    }
                ]
            }
        
        return bubble
    
    @staticmethod
    def create_carousel_message(news_items: list) -> dict:
        """สร้าง Carousel Message จากข่าวหลายข่าว"""
        bubbles = []
        
        for item in news_items[:BUBBLES_PER_CAROUSEL]:
            bubble = NewsMessageBuilder.create_flex_bubble(item)
            if bubble:
                bubbles.append(bubble)
        
        if not bubbles:
            return None
        
        return {
            "type": "flex",
            "altText": f"สรุปข่าวพลังงาน {datetime.now(TZ).strftime('%d/%m/%Y')} ({len(bubbles)} ข่าว)",
            "contents": {
                "type": "carousel",
                "contents": bubbles
            }
        }
