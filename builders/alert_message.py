# -*- coding: utf-8 -*-
"""
Alert Message Builder
สร้าง LINE Flex Message สำหรับการแจ้งเตือน WTI Price
"""

from config.settings import WTI_ALERT_THRESHOLD, WTI_ALERT_ENABLED

class WTIPriceAlert:
    """ระบบแจ้งเตือนราคา WTI"""
    
    ALERT_THRESHOLD = WTI_ALERT_THRESHOLD
    ALERT_ENABLED = WTI_ALERT_ENABLED
    
    @staticmethod
    def should_send_alert(current_price: float) -> bool:
        """ตรวจสอบว่าควรส่งการแจ้งเตือนหรือไม่"""
        if not WTIPriceAlert.ALERT_ENABLED:
            return False
        
        if current_price <= 0:
            return False
        
        return current_price < WTIPriceAlert.ALERT_THRESHOLD
    
    @staticmethod
    def create_alert_message(data: dict) -> dict:
        """สร้าง LINE Flex Message สำหรับการแจ้งเตือนราคาต่ำ"""
        current = data.get("current", {})
        current_price = current.get("current_price", 0)
        source = current.get("source", "Unknown")
        updated_at = data.get("updated_at", "")
        
        diff = WTIPriceAlert.ALERT_THRESHOLD - current_price
        diff_pct = (diff / WTIPriceAlert.ALERT_THRESHOLD) * 100
        
        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "⚠️ WTI PRICE ALERT",
                        "weight": "bold",
                        "size": "xl",
                        "color": "#FFFFFF",
                        "align": "center"
                    },
                    {
                        "type": "text",
                        "text": "ราคาต่ำกว่าระดับกำหนด",
                        "size": "sm",
                        "color": "#FFFFFF",
                        "align": "center",
                        "margin": "xs"
                    }
                ],
                "backgroundColor": "#DC2626",
                "paddingAll": "20px"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "text",
                                "text": "ราคาปัจจุบัน",
                                "size": "sm",
                                "color": "#888888",
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": f"${current_price:.2f}",
                                "size": "xxl",
                                "weight": "bold",
                                "color": "#DC2626",
                                "align": "center",
                                "margin": "md"
                            },
                            {
                                "type": "text",
                                "text": "per barrel",
                                "size": "xs",
                                "color": "#888888",
                                "align": "center"
                            }
                        ],
                        "backgroundColor": "#FEE2E2",
                        "cornerRadius": "10px",
                        "paddingAll": "20px",
                        "margin": "md"
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {
                                        "type": "text",
                                        "text": "ระดับแจ้งเตือน:",
                                        "size": "sm",
                                        "color": "#666666",
                                        "flex": 3
                                    },
                                    {
                                        "type": "text",
                                        "text": f"${WTIPriceAlert.ALERT_THRESHOLD:.2f}",
                                        "size": "sm",
                                        "color": "#333333",
                                        "weight": "bold",
                                        "align": "end",
                                        "flex": 2
                                    }
                                ]
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {
                                        "type": "text",
                                        "text": "ต่ำกว่า:",
                                        "size": "sm",
                                        "color": "#666666",
                                        "flex": 3
                                    },
                                    {
                                        "type": "text",
                                        "text": f"${diff:.2f} ({diff_pct:.1f}%)",
                                        "size": "sm",
                                        "color": "#DC2626",
                                        "weight": "bold",
                                        "align": "end",
                                        "flex": 2
                                    }
                                ],
                                "margin": "md"
                            }
                        ],
                        "backgroundColor": "#F9FAFB",
                        "cornerRadius": "10px",
                        "paddingAll": "15px",
                        "margin": "lg"
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "text",
                                "text": "🔔 การแจ้งเตือน",
                                "size": "sm",
                                "color": "#DC2626",
                                "weight": "bold"
                            },
                            {
                                "type": "text",
                                "text": f"ราคา WTI Crude Oil ปัจจุบันอยู่ที่ ${current_price:.2f}/barrel ซึ่งต่ำกว่าระดับแจ้งเตือนที่ ${WTIPriceAlert.ALERT_THRESHOLD:.2f}/barrel",
                                "size": "xs",
                                "color": "#666666",
                                "wrap": True,
                                "margin": "sm"
                            }
                        ],
                        "backgroundColor": "#FEF3C7",
                        "cornerRadius": "8px",
                        "paddingAll": "12px",
                        "margin": "lg"
                    },
                    {
                        "type": "separator",
                        "margin": "lg"
                    },
                    {
                        "type": "box",
                        "layout": "vertical",
                        "contents": [
                            {
                                "type": "text",
                                "text": f"อัปเดต: {updated_at}",
                                "size": "xs",
                                "color": "#888888",
                                "align": "center"
                            },
                            {
                                "type": "text",
                                "text": f"📡 ข้อมูลจาก {source}",
                                "size": "xxs",
                                "color": "#888888",
                                "align": "center",
                                "margin": "xs"
                            }
                        ],
                        "margin": "md"
                    }
                ],
                "paddingAll": "20px"
            }
        }
        
        return {
            "type": "flex",
            "altText": f"⚠️ WTI Price Alert: ${current_price:.2f}/barrel (ต่ำกว่า ${WTIPriceAlert.ALERT_THRESHOLD:.2f})",
            "contents": bubble
        }
