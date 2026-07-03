"""
Отправка ответов в каналы: Telegram и Instagram.
"""

import requests

from bot import config

# Telegram не принимает сообщения длиннее 4096 символов
TELEGRAM_MAX_LEN = 4096


def split_long_message(text: str, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Режет длинный текст на части не длиннее limit, по возможности по абзацам."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:  # переносов нет (или слишком рано) — режем жёстко
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


def _post(channel: str, url: str, **kwargs):
    """POST с таймаутом; сетевая ошибка логируется, а не роняет поток."""
    try:
        r = requests.post(url, timeout=config.HTTP_TIMEOUT, **kwargs)
        if r.status_code != 200:
            print(f"Ошибка отправки в {channel}:", r.status_code, r.text)
    except requests.RequestException as e:
        print(f"Ошибка сети при отправке в {channel}:", e)


def send_telegram_message(chat_id, text: str):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    for part in split_long_message(text):
        _post("Telegram", url, json={"chat_id": chat_id, "text": part})


def send_instagram_message(recipient_id: str, text: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    params = {"access_token": config.IG_ACCESS_TOKEN}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    _post("Instagram", url, params=params, json=payload)
