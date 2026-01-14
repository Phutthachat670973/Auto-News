# -*- coding: utf-8 -*-
"""
Storage Utilities
ฟังก์ชันจัดการไฟล์และการจัดเก็บ
"""

import os
from config.settings import SENT_DIR, TZ
from utils.url_utils import normalize_url
from datetime import datetime

def read_sent_links() -> set:
    """อ่าน URLs ที่เคยส่งไปแล้ว"""
    sent = set()
    if not os.path.exists(SENT_DIR):
        return sent
    
    for fn in os.listdir(SENT_DIR):
        if not fn.endswith(".txt"):
            continue
        fp = os.path.join(SENT_DIR, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        sent.add(line)
        except Exception:
            continue
    return sent

def append_sent_link(url: str):
    """บันทึก URL ที่ส่งไปแล้ว"""
    url = normalize_url(url)
    if not url:
        return
    
    fn = os.path.join(SENT_DIR, datetime.now(TZ).strftime("%Y-%m-%d") + ".txt")
    with open(fn, "a", encoding="utf-8") as f:
        f.write(url + "\n")
