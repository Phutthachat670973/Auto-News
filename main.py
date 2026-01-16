# -*- coding: utf-8 -*-
"""
Enhanced News Aggregator - Main Entry Point
ระบบรวบรวมข่าวพลังงาน + ราคา WTI Futures + Dynamic Price Alert
"""

from config.settings import (
    LINE_CHANNEL_ACCESS_TOKEN, 
    EIA_API_KEY,
    USE_LLM_SUMMARY,
    GROQ_API_KEY,
    WINDOW_HOURS,
    DRY_RUN
)
from services.news_processor import NewsProcessor
from services.wti_fetcher import WTIFuturesFetcher
from services.line_sender import LineSender
from builders.news_message import NewsMessageBuilder
from builders.wti_message import WTIMessageBuilder
from builders.alert_message import WTIPriceAlert
from builders.alert_config import AlertConfig  # ← เพิ่มบรรทัดนี้
from utils.storage import append_sent_link

def main():
    """Main function"""
    print("="*60)
    print("ระบบติดตามข่าวพลังงาน + WTI Futures + Dynamic Price Alert")
    print("="*60)
    
    # ตรวจสอบ configuration
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    if not EIA_API_KEY:
        print("[ERROR] EIA_API_KEY is required")
        print("        Get one from: https://www.eia.gov/opendata/")
        return
    
    # ← เพิ่มส่วนนี้: โหลด Alert Config
    alert_config = AlertConfig()
    print(f"\n[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No'}")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"\n{alert_config.get_alert_summary()}")
    
    # Initialize services
    processor = NewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # STEP 1: ตรวจสอบราคา WTI และส่ง Alert (ถ้าจำเป็น)
    print("\n[1] กำลังตรวจสอบราคา WTI...")
    wti_alert_sent = False
    wti_data = None
    
    try:
        wti_fetcher = WTIFuturesFetcher(api_key=EIA_API_KEY)
        wti_data = wti_fetcher.get_current_and_futures()
        current_price = wti_data.get("current", {}).get("current_price", 0)
        
        print(f"[WTI] ราคาปัจจุบัน: ${current_price:.2f}/barrel")
        
        # ← แก้ไขส่วนนี้: ใช้ AlertConfig แทน
        should_send, triggered_alert, reason = alert_config.should_send_alert(current_price)
        
        if should_send:
            print(f"[ALERT] {triggered_alert['emoji']} {triggered_alert['name']} triggered!")
            print(f"[ALERT] Reason: {reason}")
            
            # สร้างข้อความ alert พร้อม config
            alert_message = WTIPriceAlert.create_alert_message(wti_data, triggered_alert)
            
            if line_sender.send_message(alert_message):
                wti_alert_sent = True
                
                # บันทึกว่าส่ง alert แล้ว
                alert_config.record_alert_sent(triggered_alert["name"], current_price)
                
                print(f"[ALERT] ✓ ส่งการแจ้งเตือนสำเร็จ")
            else:
                print("[ALERT] ✗ ส่งการแจ้งเตือนไม่สำเร็จ")
        else:
            print(f"[ALERT] ✓ ราคาปกติ - {reason}")
    
    except Exception as e:
        print(f"[ALERT] ✗ ตรวจสอบราคาไม่สำเร็จ: {str(e)}")
    
    # STEP 2: ดึงและกรองข่าว
    print("\n[2] กำลังดึงและกรองข่าว...")
    news_items = processor.fetch_and_filter_news()
    
    print(f"\n[FILTER STATISTICS]")
    print(f"  รวมข่าวที่ประมวลผล: {processor.filter_stats['total_processed']}")
    print(f"  ผ่านการกรอง: {processor.filter_stats['filtered_by']['passed']}")
    
    # แยกข่าวเป็น 2 กลุ่ม
    country_news = []
    international_news = []
    
    for item in news_items:
        country = item.get('country', '')
        if country == 'International':
            international_news.append(item)
        elif country:
            country_news.append(item)
    
    print(f"\n[3] แยกข่าวตามประเภท:")
    print(f"   - ข่าวประเทศเฉพาะ: {len(country_news)} ข่าว")
    print(f"   - ข่าวระดับโลก: {len(international_news)} ข่าว")
    
    # นับจำนวนข้อความที่ส่ง
    success_count = 1 if wti_alert_sent else 0
    total_messages = 1 if wti_alert_sent else 0
    
    # STEP 3: ส่งข่าวประเทศเฉพาะ
    if country_news:
        print("\n[4] กำลังส่งข่าวประเทศเฉพาะ...")
        country_message = NewsMessageBuilder.create_carousel_message(country_news)
        
        if country_message:
            total_messages += 1
            if line_sender.send_message(country_message):
                success_count += 1
                print("   ✓ ส่งข่าวประเทศเฉพาะสำเร็จ")
    
    # STEP 4: ส่งข่าว International
    if international_news:
        print("\n[5] กำลังส่งข่าวระดับโลก...")
        intl_message = NewsMessageBuilder.create_carousel_message(international_news)
        
        if intl_message:
            total_messages += 1
            if line_sender.send_message(intl_message):
                success_count += 1
                print("   ✓ ส่งข่าวระดับโลกสำเร็จ")
    
    # STEP 5: ส่งข้อมูล WTI Futures ปกติ
    print("\n[6] กำลังส่งข้อมูล WTI Futures...")
    try:
        if not wti_data:
            wti_fetcher = WTIFuturesFetcher(api_key=EIA_API_KEY)
            wti_data = wti_fetcher.get_current_and_futures()
        
        wti_message = WTIMessageBuilder.create_wti_futures_message(wti_data)
        
        total_messages += 1
        if line_sender.send_message(wti_message):
            success_count += 1
            print("   ✓ ส่ง WTI Futures สำเร็จ")
    
    except Exception as e:
        print(f"   ✗ WTI ERROR: {str(e)}")
    
    # STEP 6: บันทึกข่าวที่ส่งแล้ว
    if (country_news or international_news) and not DRY_RUN:
        all_sent_news = country_news + international_news
        for item in all_sent_news:
            append_sent_link(item.get('canon_url') or item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    # สรุปผล
    print("\n" + "="*60)
    print(f"ดำเนินการเสร็จสิ้น - ส่งสำเร็จ {success_count}/{total_messages} ข้อความ")
    if wti_alert_sent:
        print(f"  ⚠️ WTI Price Alert: ส่งแล้ว")
    print(f"  • ข่าวประเทศเฉพาะ: {len(country_news)} ข่าว")
    print(f"  • ข่าวระดับโลก: {len(international_news)} ข่าว")
    print(f"  • WTI Futures: 12 เดือน")
    print("="*60)


if __name__ == "__main__":
    main()
