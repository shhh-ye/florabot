"""
Отправка ответов в каналы: Telegram и Instagram.
"""

import json
import threading
import time

import requests

from bot import config

# Общая сессия с keep-alive. Зачем: на Render иногда намертво зависает
# системный DNS-запрос (getaddrinfo) к api.telegram.org — его не покрывает
# таймаут requests, и отправка виснет навсегда. Сессия переиспользует уже
# установленное соединение (его прогревает проверка токена при старте),
# поэтому обычной отправке DNS вообще не нужен.
_session = requests.Session()

# Итоги одной попытки отправки
_OK = "ok"          # доставлено
_RETRY = "retry"    # временная ошибка (сеть, 5xx, лимит) — можно повторить
_FATAL = "fatal"    # постоянная ошибка (плохой токен, чата нет) — повтор не поможет

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


def _post_once(channel: str, url: str, kwargs: dict) -> str:
    """Одна попытка отправки. Логируем и успех, и ошибку — иначе по логам
    невозможно отличить «отправили» от «отправка молча провалилась»."""
    try:
        r = _session.post(url, timeout=config.HTTP_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        print(f"✗ Ошибка сети при отправке в {channel}: {e}", flush=True)
        return _RETRY
    if r.status_code == 200:
        print(f"✓ Отправлено в {channel}", flush=True)
        return _OK
    # Тело ответа Telegram/Meta объясняет причину (Unauthorized,
    # chat not found...) — печатаем его целиком
    print(f"✗ ОШИБКА отправки в {channel}: HTTP {r.status_code} {r.text}",
          flush=True)
    if r.status_code == 429:  # лимит запросов — временная, повторим
        return _RETRY
    # Остальные 4xx — постоянные (плохой токен, чат не найден), 5xx — временные
    return _FATAL if 400 <= r.status_code < 500 else _RETRY


def _attempt_with_deadline(channel: str, url: str, kwargs: dict, limit: float):
    """
    Одна попытка в отдельном потоке с жёстким потолком времени.

    Именно отдельный поток на каждую попытку (а не пул): зависшая попытка
    остаётся висеть в своём потоке-зомби и никому не мешает. Пул же такие
    зомби постепенно забивают, и новые отправки перестают запускаться.
    Возвращает исход или None, если попытка не уложилась в лимит.
    """
    box: dict = {}

    def runner():
        box["outcome"] = _post_once(channel, url, kwargs)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=limit)
    return box.get("outcome")


def _post(channel: str, url: str, **kwargs):
    """
    Отправка с повторами. Даже намертво зависший запрос (например, на
    зависшем DNS, который таймаут requests не покрывает) не заблокирует
    доставку: попытку бросаем и запускаем новую, с растущей паузой —
    чтобы сеть/резолвер успели ожить.
    """
    attempt_limit = config.HTTP_TIMEOUT + 10  # запас поверх таймаута requests
    for attempt in range(1, config.SEND_RETRIES + 1):
        print(f"→ Отправка в {channel} (попытка {attempt})...", flush=True)
        outcome = _attempt_with_deadline(channel, url, kwargs, attempt_limit)
        if outcome in (_OK, _FATAL):
            return
        if outcome is None:
            print(f"✗ Отправка в {channel} зависла дольше {attempt_limit:.0f}с "
                  "— бросаю эту попытку, пробую заново", flush=True)
        time.sleep(2 * attempt)  # растущая пауза: даём сети время ожить
    print(f"✗ Не удалось отправить в {channel} за {config.SEND_RETRIES} попыток",
          flush=True)


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
        # Через общую сессию: заодно ПРОГРЕВАЕТ соединение с Telegram,
        # которым потом пользуется отправка ответов (без нового DNS)
        me = _session.get(f"{base}/getMe", timeout=config.HTTP_TIMEOUT).json()
        if me.get("ok"):
            print(f"Telegram-токен OK: бот @{me['result'].get('username')}",
                  flush=True)
        else:
            print(f"✗ Telegram-токен НЕ РАБОТАЕТ: {me} — отправка ответов "
                  "невозможна, получите новый токен у @BotFather и обновите "
                  "TELEGRAM_BOT_TOKEN в Render", flush=True)

        info = _session.get(f"{base}/getWebhookInfo",
                            timeout=config.HTTP_TIMEOUT).json()
        # last_error_message внутри — последняя ошибка доставки по мнению
        # самого Telegram, самая ценная строка для диагностики
        print("Telegram webhook:",
              json.dumps(info.get("result", info), ensure_ascii=False),
              flush=True)
    except requests.RequestException as e:
        print(f"Не удалось проверить Telegram-токен (сеть): {e}", flush=True)
