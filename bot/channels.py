"""
Отправка ответов в каналы: Telegram и Instagram.
"""

import faulthandler
import json
import socket
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit

import requests

from bot import config

# Общая сессия с keep-alive: экономит TLS-рукопожатия. От зависшего DNS
# она НЕ спасает (Telegram закрывает простаивающее соединение примерно за
# минуту, и следующей отправке снова нужен резолв) — этим занимается
# bot/dns_cache.py: кэш адресов + известные IP вместо зависшего резолвера.
_session = requests.Session()

# Диагностика: печатает строку только когда открывается НОВОЕ TCP-соединение
# к Telegram. Если отправка виснет без этой строки прямо перед собой — значит,
# использовался старый сокет из пула _session, а не новый — подтверждение
# версии про протухшее keep-alive-соединение.
_real_create_connection = socket.create_connection


def _log_new_connection(address, *args, **kwargs):
    host, port = address[0], address[1]
    if host == "api.telegram.org":
        print(f"🔌 Новое TCP-соединение к {host}:{port}", flush=True)
    return _real_create_connection(address, *args, **kwargs)


socket.create_connection = _log_new_connection

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
    t0 = time.monotonic()
    try:
        r = _session.post(url, timeout=config.HTTP_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        # Тип исключения и время — главная улика: ConnectTimeout на 20с —
        # теряются SYN-пакеты (сеть/NAT), ReadTimeout — соединение есть,
        # но сервер молчит, SSLError — застряли на TLS-рукопожатии.
        print(f"✗ Ошибка сети при отправке в {channel} "
              f"за {time.monotonic() - t0:.1f}с: {type(e).__name__}: {e}",
              flush=True)
        return _RETRY
    if r.status_code == 200:
        print(f"✓ Отправлено в {channel} за {time.monotonic() - t0:.1f}с",
              flush=True)
        return _OK
    # Тело ответа Telegram/Meta объясняет причину (Unauthorized,
    # chat not found...) — печатаем его целиком
    print(f"✗ ОШИБКА отправки в {channel}: HTTP {r.status_code} {r.text}",
          flush=True)
    if r.status_code == 429:  # лимит запросов — временная, повторим
        return _RETRY
    # Остальные 4xx — постоянные (плохой токен, чат не найден), 5xx — временные
    return _FATAL if 400 <= r.status_code < 500 else _RETRY


def _probe_host(host: str, port: int = 443, timeout: float = 3.0):
    """
    Диагностика зависшей отправки: повторяем путь requests по слоям —
    TCP-соединение, TLS-рукопожатие, минимальный HTTP-запрос — с каждым
    адресом хоста по отдельности (как их видит отправка — через тот же
    кэш DNS) и логируем длительность каждого слоя. По логам видно, какой
    именно слой стоит:

    - TCP не открылся → SYN-пакеты теряются по пути
      (сеть/NAT хостинга или блок со стороны Telegram) — код бессилен;
    - TCP открылся, TLS не прошёл → соединения режет что-то посередине
      (DPI, прокси) или у хоста беда с TLS;
    - TLS прошёл, HTTP-ответ не пришёл → сервер принимает, но молчит;
    - все слои быстрые, а отправка всё равно висит → проблема не в сети,
      а внутри процесса (см. дамп стеков).
    """
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except Exception as e:
        print(f"⚙ Диагностика {host}: адреса не получены: {e}", flush=True)
        return
    seen = set()
    for family, _, _, _, addr in infos:
        ip = addr[0]
        if ip in seen:
            continue
        seen.add(ip)
        label = "IPv6" if family == socket.AF_INET6 else "IPv4"
        s = None
        stage = "TCP"
        t0 = time.monotonic()
        try:
            # Создание сокета тоже под try: в окружении вовсе без IPv6 оно
            # падает сразу (Address family not supported)
            s = socket.socket(family, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(addr)
            tcp_dt = time.monotonic() - t0

            stage = "TLS"
            t1 = time.monotonic()
            s = ssl.create_default_context().wrap_socket(
                s, server_hostname=host)
            tls_dt = time.monotonic() - t1

            stage = "HTTP"
            t2 = time.monotonic()
            s.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n"
                      "Connection: close\r\n\r\n".encode())
            first_bytes = s.recv(256)
            http_dt = time.monotonic() - t2

            status = first_bytes.split(b"\r\n", 1)[0].decode(
                "latin-1", "replace") or "пустой ответ"
            print(f"⚙ Диагностика {host} через {ip} ({label}): "
                  f"TCP {tcp_dt:.2f}с, TLS {tls_dt:.2f}с, "
                  f"HTTP {http_dt:.2f}с ({status})", flush=True)
        except Exception as e:
            print(f"⚙ Диагностика {host} через {ip} ({label}): слой {stage} "
                  f"НЕ прошёл за {time.monotonic() - t0:.2f}с — "
                  f"{type(e).__name__}: {e}", flush=True)
        finally:
            if s is not None:
                s.close()


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
            if attempt == 1:
                # Стек КАЖДОГО потока процесса — покажет точную строку, где
                # застряла зависшая попытка (сокет/TLS/лок). Только на первом
                # зависании: дамп многословный, дальше он не меняется.
                print(f"⚙ Стек всех потоков (отправка в {channel} зависла) — "
                      "начало", flush=True)
                faulthandler.dump_traceback(file=sys.stderr)
                print(f"⚙ Стек всех потоков — конец", flush=True)
            _probe_host(urlsplit(url).hostname)
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
