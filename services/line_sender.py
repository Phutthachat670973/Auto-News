# -*- coding: utf-8 -*-
"""
LINE Sender Service
ส่งข้อความผ่าน LINE Messaging API
"""

import json
import requests
from config.settings import LINE_CHANNEL_ACCESS_TOKEN, DRY_RUN

class LineSender:
    """ส่งข้อความผ่าน LINE"""
    
    def __init__(self, access_token: str = None):
        self.access_token = access_token or LINE_CHANNEL_ACCESS_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
    
    def send_message(self, message_obj: dict) -> bool:
        """ส่งข้อความไปยัง LINE"""
        if DRY_RUN:
            print("\n" + "="*60)
            print("DRY RUN - Would send message")
            print("="*60)
            print(json.dumps(message_obj, indent=2, ensure_ascii=False)[:500])
            return True
        
        url = "https://api.line.me/v2/bot/message/broadcast"
        
        try:
            response = requests.post(
                url,
                headers=self.headers,
                json={"messages": [message_obj]},
                timeout=30
            )
            
            if response.status_code == 200:
                print("[LINE] Message sent successfully!")
                return True
            else:
                print(f"[LINE] Error {response.status_code}: {response.text[:200]}")
                return False
                
        except Exception as e:
            print(f"[LINE] Exception: {str(e)}")
            return False
