"""
Отправка ответов в каналы: Telegram и Instagram.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import requests

from bot import config

# Telegram не принимает сообщения длиннее 4096 символов
TELEGRAM_MAX_LEN = 4096

# Сколько раз пытаемся доставить одно сообщение при временном сбое.
SEND_ATTEMPTS = 3

# Отдельный пул под исходящие HTTP-запросы — нужен, чтобы поставить ЖЁСТКИЙ
# потолок по времени на каждую отправку. requests(timeout=...) на это не
# способен: он не ограничивает DNS-резолв (getaddrinfo вызывается до того,
# как таймаут сокета вступает в силу), поэтому зависший запрос к
# api.telegram.org может висеть минутами. Именно так бот однажды молча
# «проглотил» ответ: текст сгенерировался («ответ готов»), а строки об
# отправке (ни ✓, ни ✗) в логах не появилось вовсе — поток встал внутри
# requests.post и не отпустил его до перезапуска воркера. Выполняем запрос
# в отдельном потоке и отказываемся ждать дольше лимита.
_send_pool = ThreadPoolExecutor(max_workers=4)


def _post_once(url: str, **kwargs):
    """requests.post с жёстким потолком по времени поверх собственного
    таймаута requests — чтобы зависание (в т.ч. на DNS) не длилось вечно."""
    future = _send_pool.submit(
        requests.post, url, timeout=config.HTTP_TIMEOUT, **kwargs
    )
    # Ждём чуть дольше таймаута самого requests: даём ему шанс завершиться
    # штатной ошибкой сети, но не позволяем висеть бесконечно.
    return future.result(timeout=config.HTTP_TIMEOUT + 5)


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
    """POST с жёстким таймаутом и повторами. Логируем и успех, и ошибку —
    иначе по логам невозможно отличить «отправили» от «отправка молча
    провалилась». Разовый сбой сети/зависание не должны оставлять клиента
    без ответа, поэтому пробуем несколько раз с нарастающей паузой."""
    for attempt in range(1, SEND_ATTEMPTS + 1):
        tag = f"(попытка {attempt}/{SEND_ATTEMPTS})"
        try:
            r = _post_once(url, **kwargs)
        except FutureTimeout:
            # Запрос завис дольше собственного таймаута requests — бросаем
            # его (фоновый поток умрёт сам) и пробуем заново.
            print(f"✗ Отправка в {channel} зависла дольше "
                  f"{config.HTTP_TIMEOUT + 5:.0f}с {tag}", flush=True)
        except requests.RequestException as e:
            print(f"✗ ОШИБКА сети при отправке в {channel} {tag}: {e}",
                  flush=True)
        else:
            if r.status_code == 200:
                print(f"✓ Отправлено в {channel}", flush=True)
                return
            # 4xx, кроме 429, — постоянная ошибка (Unauthorized, chat not
            # found, bad request): повтор не поможет, выходим сразу. Тело
            # ответа Telegram/Meta объясняет причину — печатаем целиком.
            if r.status_code != 429 and 400 <= r.status_code < 500:
                print(f"✗ ОШИБКА отправки в {channel}: HTTP {r.status_code} "
                      f"{r.text}", flush=True)
                return
            # 429 (лимит) и 5xx (сбой на стороне сервиса) — временные,
            # имеет смысл повторить.
            print(f"✗ Отправка в {channel} отклонена: HTTP {r.status_code} "
                  f"{r.text} {tag}", flush=True)

        if attempt < SEND_ATTEMPTS:
            time.sleep(2 ** (attempt - 1))  # 1с, 2с

    print(f"✗ Не удалось доставить сообщение в {channel} за "
          f"{SEND_ATTEMPTS} попытки", flush=True)


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
