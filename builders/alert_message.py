# -*- coding: utf-8 -*-
"""
Alert Message Builder
สร้าง LINE Flex Message สำหรับการแจ้งเตือน WTI Price (รองรับ Dynamic Config)
"""

import os
from typing import Optional

class WTIPriceAlert:
    """ระบบแจ้งเตือนราคา WTI แบบ Dynamic"""
    
    @staticmethod
    def create_alert_message(data: dict, alert_config: Optional[dict] = None) -> dict:
        """
        สร้าง LINE Flex Message สำหรับการแจ้งเตือนราคา
        
        Args:
            data: ข้อมูลราคา WTI
            alert_config: การตั้งค่า alert (ถ้าไม่ระบุจะใช้ค่า default)
        """
        current = data.get("current", {})
        current_price = current.get("current_price", 0)
        source = current.get("source", "Unknown")
        updated_at = data.get("updated_at", "")
        
        # ใช้ config จากที่ส่งมา หรือค่า default
        if alert_config:
            threshold = alert_config.get("threshold", 58.0)
            alert_name = alert_config.get("name", "Price Alert")
            emoji = alert_config.get("emoji", "⚠️")
            color = alert_config.get("color", "#DC2626")
            operator = alert_config.get("operator", "less_than")
        else:
            # ค่า default (backward compatible) - อ่านจาก Environment
            threshold = float(os.getenv("WTI_ALERT_THRESHOLD", "60.0"))
            alert_name = "Price Alert"
            emoji = "⚠️"
            color = "#DC2626"
            operator = "less_than"
        
        # คำนวณส่วนต่าง
        diff = abs(threshold - current_price)
        diff_pct = (diff / threshold) * 100
        
        # กำหนดข้อความตาม operator
        if operator == "less_than":
            alert_title = f"{emoji} WTI PRICE ALERT"
            alert_subtitle = "ราคาต่ำกว่าระดับกำหนด"
            status_text = "ต่ำกว่า:"
            warning_text = f"ราคา WTI Crude Oil ปัจจุบันอยู่ที่ ${current_price:.2f}/barrel ซึ่งต่ำกว่าระดับแจ้งเตือนที่ ${threshold:.2f}/barrel"
        else:
            alert_title = f"{emoji} WTI PRICE ALERT"
            alert_subtitle = "ราคาสูงกว่าระดับกำหนด"
            status_text = "สูงกว่า:"
            warning_text = f"ราคา WTI Crude Oil ปัจจุบันอยู่ที่ ${current_price:.2f}/barrel ซึ่งสูงกว่าระดับแจ้งเตือนที่ ${threshold:.2f}/barrel"
        
        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": alert_title,
                        "weight": "bold",
                        "size": "xl",
                        "color": "#FFFFFF",
                        "align": "center"
                    },
                    {
                        "type": "text",
                        "text": alert_subtitle,
                        "size": "sm",
                        "color": "#FFFFFF",
                        "align": "center",
                        "margin": "xs"
                    },
                    {
                        "type": "text",
                        "text": f"({alert_name})",
                        "size": "xs",
                        "color": "#FFFFFF",
                        "align": "center",
                        "margin": "xs"
                    }
                ],
                "backgroundColor": color,
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
                                "color": color,
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
                        "backgroundColor": "#FEE2E2" if operator == "less_than" else "#D1FAE5",
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
                                        "text": f"${threshold:.2f}",
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
                                        "text": status_text,
                                        "size": "sm",
                                        "color": "#666666",
                                        "flex": 3
                                    },
                                    {
                                        "type": "text",
                                        "text": f"${diff:.2f} ({diff_pct:.1f}%)",
                                        "size": "sm",
                                        "color": color,
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
                                "text": f"🔔 การแจ้งเตือน",
                                "size": "sm",
                                "color": color,
                                "weight": "bold"
                            },
                            {
                                "type": "text",
                                "text": warning_text,
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
            "altText": f"{emoji} WTI Price Alert: ${current_price:.2f}/barrel ({status_text} ${threshold:.2f})",
            "contents": bubble
        }
