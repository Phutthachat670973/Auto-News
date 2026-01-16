#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WTI Price Alert Checker
ตรวจสอบราคา WTI และส่ง Alert ทันทีเมื่อราคาต่ำกว่าเกณฑ์
"""

import os
import sys
import json
from datetime import datetime

# เพิ่ม path เพื่อให้ import ได้
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import LINE_CHANNEL_ACCESS_TOKEN, EIA_API_KEY, TZ
from services.wti_fetcher import WTIFuturesFetcher
from services.line_sender import LineSender
from builders.alert_message import WTIPriceAlert
from builders.alert_config import AlertConfig


def main():
    """ตรวจสอบราคาและส่ง alert"""
    print("="*60)
    print("WTI Price Alert Monitor - Real-time Check")
    print(f"Time: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("="*60)
    
    # ตรวจสอบ config
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("[ERROR] LINE_CHANNEL_ACCESS_TOKEN is required")
        return 1
    
    if not EIA_API_KEY:
        print("[ERROR] EIA_API_KEY is required")
        return 1
    
    # โหลด alert config
    alert_config = AlertConfig()
    print(f"\n{alert_config.get_alert_summary()}")
    
    # ตรวจสอบ test mode
    test_mode = os.getenv("TEST_MODE", "false").lower() in ["true", "1", "yes"]
    
    try:
        # 1. ดึงราคา WTI
        print("\n[1] Fetching WTI price...")
        wti_fetcher = WTIFuturesFetcher(api_key=EIA_API_KEY)
        wti_data = wti_fetcher.get_current_and_futures()
        
        current_price = wti_data.get("current", {}).get("current_price", 0)
        source = wti_data.get("current", {}).get("source", "Unknown")
        
        print(f"[WTI] Current Price: ${current_price:.2f}/barrel")
        print(f"[WTI] Source: {source}")
        
        if current_price <= 0:
            print("[ERROR] Invalid price data")
            return 1
        
        # 2. ตรวจสอบว่าควรส่ง alert หรือไม่
        print("\n[2] Checking alert conditions...")
        
        if test_mode:
            print("[TEST MODE] Forcing alert send...")
            should_send = True
            triggered_alert = alert_config.config["wti_alerts"][0]
            reason = "Test mode enabled"
        else:
            should_send, triggered_alert, reason = alert_config.should_send_alert(current_price)
        
        print(f"[ALERT] Should send: {should_send}")
        print(f"[ALERT] Reason: {reason}")
        
        # 3. ส่ง alert (ถ้าจำเป็น)
        if should_send:
            print("\n[3] Sending alert...")
            
            line_sender = LineSender(LINE_CHANNEL_ACCESS_TOKEN)
            alert_message = WTIPriceAlert.create_alert_message(wti_data, triggered_alert)
            
            if line_sender.send_message(alert_message):
                print(f"[SUCCESS] ✓ Alert sent: {triggered_alert['name']}")
                print(f"           Price: ${current_price:.2f}")
                print(f"           Threshold: ${triggered_alert['threshold']:.2f}")
                
                # บันทึกประวัติ
                if not test_mode:
                    alert_config.record_alert_sent(triggered_alert["name"], current_price)
                    print("[SUCCESS] ✓ Alert history recorded")
                
                return 0
            else:
                print("[ERROR] ✗ Failed to send alert")
                return 1
        else:
            print("\n[3] No alert needed")
            print(f"    Current price: ${current_price:.2f}")
            print(f"    Status: {reason}")
            return 0
            
    except Exception as e:
        print(f"\n[ERROR] Exception: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = main()
    
    print("\n" + "="*60)
    print(f"Finished with exit code: {exit_code}")
    print("="*60)
    
    sys.exit(exit_code)
