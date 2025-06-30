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

# ------------------- ฟังก์ชันสรุป + แปล + วิเคราะห์ผลกระทบ -------------------
def summarize_and_translate(title, full_text, link=None):
    # ถ้าเนื้อหาน้อยเกินไป ให้พยายาม fetch จากเว็บใหม่
    if len(full_text.split()) < 50 and link:
        full_text = fetch_full_article_text(link)

    # ถ้าไม่มีเนื้อหาเลย
    if not full_text or len(full_text.strip()) < 30:
        return title, "ไม่สามารถดึงเนื้อหาข่าวได้", ""

    # จำกัดความยาว input ไม่เกิน 600 คำ
    input_words = full_text.split()
    input_trimmed = "".join(input_words[:600])

    try:
        token_count = len(input_trimmed.split())
        max_len = max(40, min(200, int(token_count * 0.5)))
        result = summarizer(input_trimmed, max_length=max_len, min_length=40, do_sample=False)
        summary_en = result[0]['summary_text']
    except Exception as e:
        summary_en = f"{title}\nเนื้อหาบทความไม่สามารถสรุปได้อัตโนมัติ โปรดคลิกลิงก์เพื่ออ่านเพิ่มเติม"

    try:
        translated = translate_en_to_th(summary_en)
    except Exception as e:
        translated = f"[แปลไม่ได้] {e}"

    # วิเคราะห์ผลกระทบ
    try:
        impact_en = analyze_impact(summary_en)
        impact_th = translate_en_to_th(impact_en) if impact_en else ""
    except:
        impact_th = ""

    translated = translated.replace("<n>", "\n").strip()
    if "\n" in translated:
        title_th, summary_th = translated.split("\n", 1)
    else:
        title_th, summary_th = title, translated

    return title_th.strip(), summary_th.strip(), impact_th.strip()

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
