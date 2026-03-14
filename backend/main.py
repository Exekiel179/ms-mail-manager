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
from datetime import datetime, timedelta
from urllib.parse import unquote

app = FastAPI()

# Mount frontend directory
frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
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

# DB_FILE points to accounts.json in the root directory
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "accounts.json")

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
    match = re.search(r'\b\d{4,7}\b', text)
    return match.group(0) if match else "---"

def get_access_token(client_id: str, refresh_token: str):
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
            return resp.json().get("access_token"), resp.json().get("refresh_token")
    except: pass
    return None, None

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
        if len(parts) >= 4:
            email, pwd, cid, rtoken = [p.strip() for p in parts[:4]]
            if email not in db:
                db[email] = {
                    "id": email,
                    "password": pwd,
                    "client_id": cid,
                    "refresh_token": rtoken,
                    "latest_code": "---",
                    "last_update": "Never",
                    "perplexity_used_date": None,
                    "tavily_used_date": None
                }
            else:
                db[email].update({"password": pwd, "client_id": cid, "refresh_token": rtoken})
    save_db(db)
    return {"status": "success"}

@app.post("/mark_used/{email}")
def mark_used(email: str):
    email = unquote(email)
    db = load_db()
    if email not in db: raise HTTPException(status_code=404)
    
    today = datetime.now().strftime("%Y-%m-%d")
    if db[email].get("perplexity_used_date") == today:
        db[email]["perplexity_used_date"] = None
    else:
        db[email]["perplexity_used_date"] = today
        
    save_db(db)
    return {"status": "success", "date": db[email]["perplexity_used_date"]}

@app.post("/mark_tavily_used/{email}")
def mark_tavily_used(email: str):
    email = unquote(email)
    db = load_db()
    if email not in db: raise HTTPException(status_code=404)
    
    today = datetime.now().strftime("%Y-%m-%d")
    if db[email].get("tavily_used_date") == today:
        db[email]["tavily_used_date"] = None
    else:
        db[email]["tavily_used_date"] = today
        
    save_db(db)
    return {"status": "success", "date": db[email]["tavily_used_date"]}

@app.post("/refresh/{email}")
def refresh_single(email: str):
    email = unquote(email)
    db = load_db()
    if email not in db: raise HTTPException(status_code=404)
    acc = db[email]
    access_token, new_refresh_token = get_access_token(acc["client_id"], acc["refresh_token"])
    if not access_token: return {"status": "failed"}
    if new_refresh_token: db[email]["refresh_token"] = new_refresh_token
    try:
        graph_url = "https://graph.microsoft.com/v1.0/me/messages?$top=1"
        mail_resp = requests.get(graph_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        if mail_resp.ok:
            mails = mail_resp.json().get("value", [])
            if mails:
                code = extract_code(mails[0].get("subject", "") + " " + mails[0].get("bodyPreview", ""))
                db[email]["latest_code"] = code
                db[email]["last_update"] = time.strftime("%H:%M:%S")
                save_db(db)
                return {"status": "success", "code": code}
    except: pass
    return {"status": "failed"}

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
