def main():
    print("="*60)
    print("ระบบติดตามข่าวพลังงาน - Energy News Center เท่านั้น")
    print("="*60)
    
    # Configuration check
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return
    
    print(f"\n[CONFIG] โหมด: Energy News Center เท่านั้น")
    print(f"[CONFIG] Time window: {WINDOW_HOURS} hours")
    print(f"[CONFIG] Dry run: {'Yes' if DRY_RUN else 'No'}")
    print(f"[CONFIG] Use LLM: {'Yes' if USE_LLM_SUMMARY and GROQ_API_KEY else 'No (simple summary)'}")
    
    # Initialize components
    processor = NewsProcessor()
    line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
    
    # Step 1: Fetch and filter news
    print("\n[1] กำลังดึงข่าวจาก Energy News Center...")
    news_items = processor.fetch_and_filter_news()
    
    if not news_items:
        print("\n[INFO] ไม่พบข่าวใหม่จาก Energy News Center")
        
        # ถ้าเป็น Dry run อาจลองวิธีอื่น
        if DRY_RUN:
            print("\n[DEBUG] ลองใช้วิธีทดสอบ...")
            # สร้างตัวอย่างข่าวสำหรับ testing
            news_items = [{
                'title': 'ตัวอย่างข่าวพลังงานจาก Energy News Center',
                'url': 'https://www.energynewscenter.com/test-article',
                'canon_url': 'https://www.energynewscenter.com/test-article',
                'source_name': 'Energy News Center',
                'domain': 'energynewscenter.com',
                'summary': 'นี่คือตัวอย่างข่าวเกี่ยวกับพลังงานทดแทนในประเทศไทย',
                'published_dt': now_tz(),
                'country': 'Thailand',
                'project_hints': ['โครงการจี 1/61', 'โครงการอาทิตย์'],
                'llm_summary': 'ข่าวเกี่ยวกับพลังงานทดแทนในประเทศไทย',
                'feed': 'EnergyNewsCenter',
                'feed_type': 'direct',
                'simple_summary': 'ตัวอย่างข่าวพลังงาน'
            }]
            print("[DEBUG] สร้างตัวอย่างข่าวสำเร็จ")
        
        else:
            return
    
    print(f"\n[2] พบข่าวที่เกี่ยวข้องทั้งหมด {len(news_items)} ข่าว")
    
    # แสดงรายละเอียดข่าว
    for i, item in enumerate(news_items[:5]):  # แสดงเฉพาะ 5 ข่าวแรก
        pub_time = item['published_dt'].strftime("%H:%M") if item.get('published_dt') else "N/A"
        print(f"  {i+1}. [{pub_time}] {item['title'][:70]}...")
    
    if len(news_items) > 5:
        print(f"  ... และอีก {len(news_items) - 5} ข่าว")
    
    # Step 2: Create LINE message
    print("\n[3] กำลังสร้างข้อความ LINE...")
    line_message = LineMessageBuilder.create_carousel_message(news_items)
    
    if not line_message:
        print("[ERROR] ไม่สามารถสร้างข้อความได้")
        return
    
    # Step 3: Send message
    print("\n[4] กำลังส่งข้อความ...")
    success = line_sender.send_message(line_message)
    
    # Step 4: Mark as sent if successful
    if success and not DRY_RUN:
        for item in news_items:
            append_sent_link(item.get('canon_url') or item.get('url'))
        print("\n[SUCCESS] อัปเดตฐานข้อมูลข่าวที่ส่งแล้ว")
    
    print("\n" + "="*60)
    print("ดำเนินการเสร็จสิ้น")
    print("="*60)
