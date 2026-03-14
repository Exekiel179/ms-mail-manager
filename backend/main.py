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

# 获取项目根目录下的 accounts.json
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
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def extract_code(text):
    if not text: return "---"
    # 优先匹配常见的 6 位数字，然后是 4-7 位
    patterns = [
        r'\b(\d{6})\b',
        r'\b(\d{4,7})\b',
        r'code is[:\s]+([A-Z0-9]{4,8})', # 针对某些字母数字混合的验证码
        r'verification code[:\s]+(\d+)'
    ]
    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match: return match.group(1 if len(match.groups()) > 0 else 0)
    return "---"

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
    except: pass
    return None, None

def get_imap_code(host, user, password):
    try:
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
                subject = decode_header(msg["Subject"])[0][0]
                if isinstance(subject, bytes): subject = subject.decode()
                
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode()
                            break
                else:
                    body = msg.get_payload(decode=True).decode()
                
                mail.logout()
                return extract_code(subject + " " + body)
    except Exception as e:
        print(f"IMAP Error: {e}")
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
        email_addr = parts[0].strip()
        
        # 判断类型
        if len(parts) >= 4 and "----" in line and len(parts[2]) > 20: # 微软 OAuth 格式
            pwd, cid, rtoken = parts[1], parts[2], parts[3]
            acc_type = "outlook"
        else: # 账号密码格式 (IMAP)
            pwd = parts[1] if len(parts) > 1 else ""
            cid, rtoken = "", ""
            acc_type = "imap"
            # 自动识别 IMAP 服务器
            domain = email_addr.split("@")[-1]
            if "gmail" in domain: host = "imap.gmail.com"
            elif "qq.com" in domain: host = "imap.qq.com"
            elif "163.com" in domain: host = "imap.163.com"
            else: host = f"imap.{domain}"
        
        if email_addr not in db:
            db[email_addr] = {
                "id": email_addr,
                "password": pwd,
                "client_id": cid,
                "refresh_token": rtoken,
                "type": acc_type,
                "imap_host": host if acc_type == "imap" else "",
                "latest_code": "---",
                "last_update": "Never",
                "perplexity_used_date": None,
                "tavily_used_date": None
            }
        else:
            db[email_addr].update({"password": pwd, "client_id": cid, "refresh_token": rtoken})
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
    if email not in db: raise HTTPException(status_code=404)
    acc = db[email]
    
    code = "---"
    if acc.get("type") == "outlook":
        access_token, new_rtoken = get_ms_token(acc["client_id"], acc["refresh_token"])
        if access_token:
            if new_rtoken: db[email]["refresh_token"] = new_rtoken
            try:
                res = requests.get("https://graph.microsoft.com/v1.0/me/messages?$top=1", headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
                if res.ok:
                    m = res.json().get("value", [])[0]
                    code = extract_code(m.get("subject", "") + " " + m.get("bodyPreview", ""))
            except: pass
    else: # IMAP
        code = get_imap_code(acc.get("imap_host", "imap.qq.com"), acc["id"], acc["password"])

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
