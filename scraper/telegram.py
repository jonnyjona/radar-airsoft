"""Envío y recepción de mensajes con la API de bots de Telegram."""
import os

import requests

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"


def enabled():
    return bool(TOKEN)


def send(chat_id, text):
    if not TOKEN or not chat_id:
        return False
    ok = True
    # Telegram limita cada mensaje a 4096 caracteres: partir por líneas
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        try:
            r = requests.post(f"{API}/sendMessage", json={
                "chat_id": chat_id, "text": c, "parse_mode": "HTML", "disable_web_page_preview": True,
            }, timeout=20)
            ok = ok and r.ok
            if not r.ok:
                print("Telegram sendMessage:", r.status_code, r.text[:200])
        except requests.RequestException as e:
            print("Telegram sendMessage error:", e)
            ok = False
    return ok


def confirm(offset):
    """Marca como leídos todos los mensajes anteriores a offset (Telegram deja de devolverlos)."""
    if TOKEN:
        try:
            requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 0, "limit": 1}, timeout=30)
        except requests.RequestException as e:
            print("Telegram confirm error:", e)


def updates(offset):
    if not TOKEN:
        return []
    try:
        r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 0}, timeout=30)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print("Telegram getUpdates error:", e)
        return []
    if not data.get("ok"):
        print("Telegram getUpdates:", data)
        return []
    return data.get("result", [])
