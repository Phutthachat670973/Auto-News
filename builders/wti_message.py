# -*- coding: utf-8 -*-
"""
WTI Message Builder
สร้าง LINE Flex Message สำหรับ WTI Futures
"""

class WTIMessageBuilder:
    """สร้าง LINE Flex Message สำหรับ WTI Futures"""
    
    @staticmethod
    def create_wti_futures_message(data: dict) -> dict:
        """สร้าง Flex Message แสดงราคา WTI Futures ครบ 12 เดือน"""
        current = data.get("current", {})
        futures = data.get("futures", [])
        updated_at = data.get("updated_at", "")
        current_price = current.get("current_price", 0)
        is_estimated = data.get("is_estimated", True)
        source = current.get("source", "Unknown")
        
        header_contents = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "🛢️ WTI Crude Oil Futures",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#FFFFFF"
                },
                {
                    "type": "text",
                    "text": "ราคาน้ำมันล่วงหน้า 12 เดือน",
                    "size": "sm",
                    "color": "#FFFFFF",
                    "margin": "xs"
                }
            ],
            "backgroundColor": "#1E3A8A",
            "paddingAll": "20px"
        }
        
        current_box = {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "ราคาปัจจุบัน (Front Month)",
                    "size": "sm",
                    "color": "#8B8B8B",
                    "weight": "bold"
                },
                {
                    "type": "text",
                    "text": f"${current_price:.2f}",
                    "size": "xxl",
                    "weight": "bold",
                    "color": "#1E3A8A",
                    "margin": "xs"
                },
                {
                    "type": "text",
                    "text": "per barrel",
                    "size": "xs",
                    "color": "#8B8B8B",
                    "margin": "xs"
                }
            ],
            "backgroundColor": "#F0F9FF",
            "cornerRadius": "10px",
            "paddingAll": "15px",
            "margin": "md"
        }
        
        table_header = {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {
                    "type": "text",
                    "text": "เดือน",
                    "size": "xs",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": "ราคา",
                    "size": "xs",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "align": "end",
                    "flex": 2
                },
                {
                    "type": "text",
                    "text": "เปลี่ยน",
                    "size": "xs",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "align": "end",
                    "flex": 1
                }
            ],
            "backgroundColor": "#3B82F6",
            "paddingAll": "10px",
            "cornerRadius": "5px",
            "margin": "lg"
        }
        
        futures_rows = []
        for i, future in enumerate(futures[:12]):
            month = future.get("month", "")
            price = future.get("price", 0)
            change_pct = future.get("change_pct", 0)
            
            change_color = "#16A34A" if change_pct >= 0 else "#DC2626"
            change_symbol = "+" if change_pct >= 0 else ""
            
            row = {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                    {
                        "type": "text",
                        "text": month,
                        "size": "sm",
                        "color": "#333333",
                        "flex": 2
                    },
                    {
                        "type": "text",
                        "text": f"${price:.2f}",
                        "size": "sm",
                        "color": "#333333",
                        "align": "end",
                        "weight": "bold",
                        "flex": 2
                    },
                    {
                        "type": "text",
                        "text": f"{change_symbol}{change_pct:.1f}%",
                        "size": "xs",
                        "color": change_color,
                        "align": "end",
                        "weight": "bold",
                        "flex": 1
                    }
                ],
                "paddingAll": "8px",
                "backgroundColor": "#F9FAFB" if i % 2 == 0 else "#FFFFFF"
            }
            futures_rows.append(row)
        
        footer_contents = [
            {
                "type": "separator",
                "margin": "md"
            },
            {
                "type": "text",
                "text": f"อัปเดต: {updated_at}",
                "size": "xs",
                "color": "#8B8B8B",
                "align": "center",
                "margin": "md"
            },
            {
                "type": "text",
                "text": f"📡 ข้อมูลจาก {source}",
                "size": "xxs",
                "color": "#8B8B8B",
                "align": "center",
                "margin": "xs"
            }
        ]
        
        if is_estimated:
            footer_contents.append({
                "type": "text",
                "text": "⚠️ ราคา Futures เป็นการประมาณการ",
                "size": "xxs",
                "color": "#F59E0B",
                "align": "center",
                "margin": "xs"
            })
        else:
            footer_contents.append({
                "type": "text",
                "text": "✅ ราคาจริงจากตลาด NYMEX",
                "size": "xxs",
                "color": "#16A34A",
                "align": "center",
                "margin": "xs"
            })
        
        footer = {
            "type": "box",
            "layout": "vertical",
            "contents": footer_contents
        }
        
        bubble = {
            "type": "bubble",
            "size": "mega",
            "header": header_contents,
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    current_box,
                    table_header,
                    *futures_rows,
                    footer
                ],
                "paddingAll": "0px"
            }
        }
        
        return {
            "type": "flex",
            "altText": f"ราคา WTI Crude Oil Futures: ${current_price:.2f}/barrel",
            "contents": bubble
        }
