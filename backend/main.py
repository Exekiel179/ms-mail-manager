from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import requests
import re
import json
from typing import Optional, Dict
import os
import time
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from urllib.parse import unquote

app = FastAPI()

# 路径配置
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DB_FILE = os.path.join(BASE_DIR, "accounts.json")
frontend_path = os.path.join(BASE_DIR, "frontend")

app.mount("/static", StaticFiles(directory=frontend_path), name="static")

@app.get("/")
def read_index():
    return FileResponse(os.path.join(frontend_path, "index.html"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_db():
    if not os.path.exists(DB_FILE): return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
            # 自动迁移旧数据：修复缺失 type 字段的问题
            changed = False
            for email_addr, acc in db.items():
                if "type" not in acc:
                    if acc.get("refresh_token") and len(acc["refresh_token"]) > 20:
                        acc["type"] = "outlook"
                    else:
                        acc["type"] = "imap"
                        if "imap_host" not in acc:
                            domain = email_addr.split("@")[-1]
                            acc["imap_host"] = f"imap.{domain}"
                    changed = True
            if changed: save_db(db)
            return db
    except Exception as e:
        print(f"Load DB Error: {e}")
        return {}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def extract_code(text):
    if not text: return "---"
    
    # 0. 优先从 HTML href 属性中提取完整链接
    href_links = re.findall(r'href=["\'](https?://[^"\']+)["\']', text)
    for link in href_links:
        l_lower = link.lower()
        if "ticket=" in l_lower or any(k in l_lower for k in ["verify", "email-verification", "confirm", "auth"]):
            return link.strip()

    # 1. 预处理：清理空白
    clean_text = " ".join(text.split())
    
    # 2. 引导词匹配
    patterns = [
        r'code is[:\s]+([A-Z0-9]{4,8})',
        r'verification code[:\s]+([A-Z0-9]{4,8})',
        r'login code[:\s]+([A-Z0-9]{4,8})',
        r'码是[:\s]+([A-Z0-9]{4,8})',
        r'验证码[:\s]+(\d+)'
    ]
    for p in patterns:
        match = re.search(p, clean_text, re.IGNORECASE)
        if match: return match.group(1).strip()
    
    # 3. 独立数字块
    match_anyd = re.search(r'\b(\d{4,8})\b', clean_text)
    if match_anyd: return match_anyd.group(1)
    
    # 4. 纯文本中的链接
    urls = re.findall(r'https?://[^\s<>"]+', text)
    for url in urls:
        if any(k in url.lower() for k in ["verify", "login", "confirm", "auth", "ticket="]):
            return url.strip()
            
    # 5. 兜底摘要 (2000字)
    snippet = re.sub(r'<[^>]+>', ' ', text)
    snippet = " ".join(snippet.split()).strip()
    return snippet[:2000] if snippet else "---"


def get_ms_token(client_id: str, refresh_token: str):
    url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "https://graph.microsoft.com/Mail.Read offline_access",
    }
    try:
        resp = requests.post(url, data=data, timeout=10)
        if resp.ok:
            d = resp.json()
            return d.get("access_token"), d.get("refresh_token")
    except Exception as e:
        print(f"MS Token Error: {e}")
    return None, None

def get_imap_code(host, user, password):
    try:
        # 针对 Gmail/QQ 等可能需要 SSL 的情况
        mail = imaplib.IMAP4_SSL(host)
        mail.login(user, password)
        mail.select("inbox")
        status, messages = mail.search(None, "ALL")
        if status != 'OK' or not messages[0]: return "---"
        
        last_msg_id = messages[0].split()[-1]
        res, msg_data = mail.fetch(last_msg_id, "(RFC822)")
        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                
                # 尝试解码标题
                subject_raw = decode_header(msg["Subject"])
                subject = ""
                for part, enc in subject_raw:
                    if isinstance(part, bytes):
                        subject += part.decode(enc or 'utf-8', errors='ignore')
                    else:
                        subject += part
                
                # 尝试解码正文
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() in ["text/plain", "text/html"]:
                            try:
                                body += part.get_payload(decode=True).decode(errors='ignore')
                            except: pass
                else:
                    body = msg.get_payload(decode=True).decode(errors='ignore')
                
                mail.logout()
                return extract_code(subject + " " + body)
    except Exception as e:
        print(f"IMAP Error ({user}): {e}")
    return "---"

@app.get("/accounts")
def get_accounts():
    db = load_db()
    return sorted(list(db.values()), key=lambda x: x['id'])

@app.post("/import")
def import_accounts(bulk_data: dict):
    lines = bulk_data.get("data", "").strip().split("\n")
    db = load_db()
    for line in lines:
        parts = line.strip().split("----")
        if not parts: continue
        email_addr = parts[0].strip()
        
        if len(parts) >= 4 and len(parts[2]) > 20: # Outlook OAuth
            pwd, cid, rtoken = parts[1], parts[2], parts[3]
            acc_type = "outlook"
            host = ""
        else: # IMAP
            pwd = parts[1] if len(parts) > 1 else ""
            cid, rtoken = "", ""
            acc_type = "imap"
            domain = email_addr.split("@")[-1]
            if "gmail" in domain: host = "imap.gmail.com"
            elif "qq.com" in domain: host = "imap.qq.com"
            elif "163.com" in domain: host = "imap.163.com"
            else: host = f"imap.{domain}"
        
        db[email_addr] = {
            "id": email_addr,
            "password": pwd,
            "client_id": cid,
            "refresh_token": rtoken,
            "type": acc_type,
            "imap_host": host,
            "latest_code": "---",
            "last_update": "Never",
            "perplexity_used_date": db.get(email_addr, {}).get("perplexity_used_date"),
            "tavily_used_date": db.get(email_addr, {}).get("tavily_used_date")
        }
    save_db(db)
    return {"status": "success"}

@app.post("/mark_used/{email}")
def mark_used(email: str):
    email = unquote(email)
    db = load_db()
    if email not in db: raise HTTPException(status_code=404)
    today = datetime.now().strftime("%Y-%m-%d")
    db[email]["perplexity_used_date"] = None if db[email].get("perplexity_used_date") == today else today
    save_db(db)
    return {"status": "success"}

@app.post("/mark_tavily_used/{email}")
def mark_tavily_used(email: str):
    email = unquote(email)
    db = load_db()
    if email not in db: raise HTTPException(status_code=404)
    today = datetime.now().strftime("%Y-%m-%d")
    db[email]["tavily_used_date"] = None if db[email].get("tavily_used_date") == today else today
    save_db(db)
    return {"status": "success"}

@app.post("/refresh/{email}")
def refresh_single(email: str):
    email = unquote(email)
    db = load_db()
    if email not in db: return {"status": "not_found"}
    acc = db[email]
    
    code = "---"
    try:
        if acc.get("type") == "outlook":
            access_token, new_rtoken = get_ms_token(acc["client_id"], acc["refresh_token"])
            if access_token:
                if new_rtoken: db[email]["refresh_token"] = new_rtoken
                # 关键：请求 body 字段以获取 HTML 全文
                res = requests.get("https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=subject,body,bodyPreview", headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
                if res.ok:
                    m_list = res.json().get("value", [])
                    if m_list:
                        m = m_list[0]
                        # 优先从 body.content 中提取，那里有完整的 HTML 和链接
                        full_content = m.get("body", {}).get("content", "") or m.get("bodyPreview", "")
                        code = extract_code(m.get("subject", "") + " " + full_content)
            else:
                print(f"MS Auth Failed for {email}")
        else: # IMAP
            code = get_imap_code(acc.get("imap_host", "imap.qq.com"), acc["id"], acc["password"])
    except Exception as e:
        print(f"Refresh Error ({email}): {e}")

    db[email]["latest_code"] = code
    db[email]["last_update"] = time.strftime("%H:%M:%S")
    save_db(db)
    return {"status": "success", "code": code}

@app.delete("/account/{email}")
def delete_account(email: str):
    email = unquote(email)
    db = load_db()
    if email in db:
        del db[email]
        save_db(db)
        return {"status": "success"}
    return {"status": "not_found"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
