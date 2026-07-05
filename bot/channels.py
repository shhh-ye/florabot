"""
Отправка ответов в каналы: Telegram и Instagram.
"""

import json

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
    """POST с таймаутом. Логируем и успех, и ошибку — иначе по логам
    невозможно отличить «отправили» от «отправка молча провалилась»."""
    try:
        r = requests.post(url, timeout=config.HTTP_TIMEOUT, **kwargs)
        if r.status_code == 200:
            print(f"✓ Отправлено в {channel}", flush=True)
        else:
            # Тело ответа Telegram/Meta объясняет причину (Unauthorized,
            # chat not found...) — печатаем его целиком
            print(f"✗ ОШИБКА отправки в {channel}: HTTP {r.status_code} {r.text}",
                  flush=True)
    except requests.RequestException as e:
        print(f"✗ ОШИБКА сети при отправке в {channel}: {e}", flush=True)


def send_telegram_message(chat_id, text: str):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    for part in split_long_message(text):
        _post("Telegram", url, json={"chat_id": chat_id, "text": part})


def send_instagram_message(recipient_id: str, text: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    params = {"access_token": config.IG_ACCESS_TOKEN}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    _post("Instagram", url, params=params, json=payload)


def log_telegram_status():
    """
    Самодиагностика при старте: работает ли токен бота и что Telegram
    знает о вебхуке. Приём сообщений работает даже с мёртвым токеном
    (Telegram сам их пушит), а вот отправка — нет; без этой проверки
    «сообщения приходят, ответы не уходят» выглядит мистикой.
    """
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    try:
        me = requests.get(f"{base}/getMe", timeout=config.HTTP_TIMEOUT).json()
        if me.get("ok"):
            print(f"Telegram-токен OK: бот @{me['result'].get('username')}",
                  flush=True)
        else:
            print(f"✗ Telegram-токен НЕ РАБОТАЕТ: {me} — отправка ответов "
                  "невозможна, получите новый токен у @BotFather и обновите "
                  "TELEGRAM_BOT_TOKEN в Render", flush=True)

        info = requests.get(f"{base}/getWebhookInfo",
                            timeout=config.HTTP_TIMEOUT).json()
        # last_error_message внутри — последняя ошибка доставки по мнению
        # самого Telegram, самая ценная строка для диагностики
        print("Telegram webhook:",
              json.dumps(info.get("result", info), ensure_ascii=False),
              flush=True)
    except requests.RequestException as e:
        print(f"Не удалось проверить Telegram-токен (сеть): {e}", flush=True)
