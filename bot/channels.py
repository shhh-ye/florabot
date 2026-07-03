"""
Отправка ответов в каналы: Telegram и Instagram.
"""

import requests

from bot import config


def send_telegram_message(chat_id, text: str):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        print("Ошибка отправки в Telegram:", r.status_code, r.text)


def send_instagram_message(recipient_id: str, text: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    params = {"access_token": config.IG_ACCESS_TOKEN}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    r = requests.post(url, params=params, json=payload)
    if r.status_code != 200:
        print("Ошибка отправки в Instagram:", r.status_code, r.text)
