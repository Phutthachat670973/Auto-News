# ------------------- ฟังก์ชันวิเคราะห์ผลกระทบระดับโลก -------------------
def analyze_impact(summary_en):
    prompt = f"""
    Analyze the global impact of the following news article.
    Identify specific countries or global regions that are affected most by this news.
    Also summarize the nature of the impact on them.
    Respond in the following format:
    Country/Region(s): <affected>
    Impact: <impact>

    Article:
    {summary_en}
    """
    try:
        response = summarizer(prompt, max_length=100, min_length=30, do_sample=False)
        return response[0]['summary_text']
    except:
        return ""

# ------------------- Flex Message -------------------
def create_flex_message(news_items):
    bubbles = []
    for item in news_items:
        title_th, summary_th, impact_th = summarize_and_translate(item['title'], item['summary'], item.get('link'))

        # แยกส่วน Country/Region และ Impact
        affected_area = ""
        impact_detail = ""
        if "Country/Region(s):" in impact_th and "Impact:" in impact_th:
            try:
                parts = impact_th.split("Country/Region(s):", 1)[1].strip()
                affected_area, impact_detail = parts.split("Impact:", 1)
            except:
                affected_area = ""
                impact_detail = impact_th.strip()
        else:
            affected_area = ""
            impact_detail = impact_th.strip()

        bubble_contents = [
            {"type": "text", "text": title_th, "weight": "bold", "size": "md", "wrap": True},
            {"type": "text", "text": f"🗓 {item['published'].strftime('%d/%m/%Y')}", "size": "xs", "color": "#888888", "margin": "sm"},
            {"type": "text", "text": f"📌 {item['category']}", "size": "xs", "color": "#AAAAAA", "margin": "xs"},
            {"type": "text", "text": f"📣 {item['source']}", "size": "xs", "color": "#AAAAAA", "margin": "xs"},
        ]

        if affected_area.strip():
            bubble_contents.append({"type": "text", "text": f"🌍 ประเทศ/ภูมิภาคที่ได้รับผลกระทบ: {affected_area.strip()}", "size": "xs", "color": "#888888", "wrap": True, "margin": "sm"})
        if impact_detail.strip():
            bubble_contents.append({"type": "text", "text": "📉 ผลกระทบที่เกิดขึ้น:", "size": "xs", "color": "#888888", "wrap": True, "margin": "xs"})
            bubble_contents.append({"type": "text", "text": impact_detail.strip(), "size": "xs", "color": "#444444", "wrap": True, "margin": "xs"})

        bubble_contents.append({"type": "text", "text": summary_th.strip(), "size": "sm", "wrap": True, "margin": "md"})

        bubble = {
            "type": "bubble",
            "size": "mega",
            "hero": {
                "type": "image",
                "url": item.get("image", "https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png"),
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": bubble_contents
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "link",
                        "height": "sm",
                        "action": {"type": "uri", "label": "อ่านต่อ", "uri": item['link']}
                    }
                ]
            }
        }
        if bubble["hero"]["url"].startswith("http"):
            bubbles.append(bubble)

    return [{
        "type": "flex",
        "altText": f"ข่าวประจำวันที่ {now_thai.strftime('%d/%m/%Y')}",
        "contents": {
            "type": "carousel",
            "contents": bubbles[i:i+10]
        }
    } for i in range(0, len(bubbles), 10)]
