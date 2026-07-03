"""
Самопроверка при старте сервера.

Прогоняет каждый внешний компонент (OpenAI chat-модель, embeddings,
Telegram, Instagram, Google Таблица) с реальными настройками и пишет
в лог результат по каждому: OK или точная ошибка.

Зачем: когда бот молчит, по логам Render сразу видно, ЧТО именно
сломано — не тот ключ, опечатка в названии модели, отозванный токен,
закрытая таблица — без гадания и добавления print'ов наощупь.

Ищите в логах строки, начинающиеся с "SELF-CHECK".
"""

import requests
from openai import OpenAI

from bot import config

# Отдельный клиент без повторных попыток: самопроверке нужен быстрый
# честный ответ, а не минуты ретраев
_client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=30, max_retries=0)


def _check_openai_chat() -> str:
    _client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=1,
    )
    return "модель отвечает"


def _check_openai_embeddings() -> str:
    _client.embeddings.create(model=config.EMBEDDING_MODEL, input=["ping"])
    return "модель отвечает"


def _check_telegram() -> str:
    r = requests.get(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getMe",
        timeout=config.HTTP_TIMEOUT,
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"getMe вернул {r.status_code}: {r.text[:200]}")
    return f"токен принадлежит боту @{data['result'].get('username')}"


def _check_instagram() -> str:
    if not config.IG_ACCESS_TOKEN:
        return "не настроен (это нормально, если пока не используете)"
    r = requests.get(
        "https://graph.instagram.com/v21.0/me",
        params={"access_token": config.IG_ACCESS_TOKEN},
        timeout=config.HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        raise RuntimeError(f"проверка токена вернула {r.status_code}: {r.text[:200]}")
    return "токен действителен"


def _check_sheet() -> str:
    if not (config.GOOGLE_SHEET_ID and config.GOOGLE_CREDENTIALS_JSON):
        return "не настроена (это нормально, если пока не используете)"
    from bot.sheets import get_all_rows

    rows = get_all_rows()
    return f"таблица читается, строк с данными: {len(rows)}"


def run_selfcheck():
    """Проверяет все компоненты по очереди, пишет результат в лог."""
    checks = [
        (f"OpenAI chat ({config.OPENAI_MODEL})", _check_openai_chat),
        (f"OpenAI embeddings ({config.EMBEDDING_MODEL})", _check_openai_embeddings),
        ("Telegram", _check_telegram),
        ("Instagram", _check_instagram),
        ("Google Таблица", _check_sheet),
    ]
    failures = 0
    for name, check in checks:
        try:
            print(f"SELF-CHECK OK   — {name}: {check()}")
        except Exception as e:
            failures += 1
            print(f"SELF-CHECK FAIL — {name}: {e}")
    if failures:
        print(f"SELF-CHECK: обнаружено проблем: {failures} — см. строки FAIL выше")
    else:
        print("SELF-CHECK: все компоненты работают")
