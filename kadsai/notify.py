"""通知。LINE（Messaging API の push/broadcast、外向きだけなので公開サーバー不要）とメール。"""
import os
import smtplib
from email.mime.text import MIMEText

import requests

LINE_API = "https://api.line.me/v2/bot/message"


def line_send(cfg, text):
    conf = cfg.line
    if not conf or not conf.get("enabled"):
        return None
    token = os.environ.get(conf.get("channel_access_token_env", "LINE_CHANNEL_ACCESS_TOKEN"), "")
    if not token:
        raise RuntimeError("LINE のチャネルアクセストークンが環境変数にありません")
    text = text[:4900]
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = conf.get("to") or []
    results = []
    if to:
        for uid in to:
            r = requests.post(f"{LINE_API}/push", headers=h, json={"to": uid, "messages": [{"type": "text", "text": text}]}, timeout=30)
            results.append((uid, r.status_code, r.text[:200]))
    else:
        r = requests.post(f"{LINE_API}/broadcast", headers=h, json={"messages": [{"type": "text", "text": text}]}, timeout=30)
        results.append(("broadcast", r.status_code, r.text[:200]))
    bad = [x for x in results if x[1] != 200]
    if bad:
        raise RuntimeError(f"LINE 送信失敗: {bad}")
    return results


def email_send(cfg, subject, body):
    conf = cfg.email
    if not conf or not conf.get("enabled"):
        return None
    user = os.environ.get(conf.get("user_env", "SMTP_USER"), "")
    pw = os.environ.get(conf.get("password_env", "SMTP_PASSWORD"), "")
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = conf.get("from") or user
    msg["To"] = ", ".join(conf.get("to") or [])
    with smtplib.SMTP(conf["smtp_host"], int(conf.get("smtp_port", 587)), timeout=30) as s:
        s.starttls()
        if user:
            s.login(user, pw)
        s.send_message(msg)
    return conf.get("to")
