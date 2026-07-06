"""
Обход системного DNS для критичных хостов (api.telegram.org).

Зачем: на Render периодически намертво зависает системный резолвер —
socket.getaddrinfo() к api.telegram.org не возвращается ни успехом, ни
ошибкой. Таймауты requests это не покрывают (они начинаются ПОСЛЕ
резолва), поэтому отправка ответа клиенту висла бесконечно. Прогретое
keep-alive соединение (v7) не спасло: Telegram закрывает простаивающее
соединение примерно за минуту, а клиенты пишут реже — почти каждой
отправке снова нужен DNS.

Схема: для хостов из PINNED_HOSTS системный резолв выполняется в
отдельном потоке с потолком времени. Успех кэшируется; при зависании или
ошибке резолвера берём последний успешный результат (пусть и устаревший),
а если кэша ещё нет — известные IP Telegram (их диапазон 149.154.160.0/20
принадлежит Telegram уже много лет). Проверка TLS-сертификата не
страдает: подменяются только IP-адреса, имя хоста в запросе прежнее.

Остальные хосты (api.openai.com, Google) идут через системный резолвер
как раньше: у них нет стабильных IP, а их вызовы и так ограничены
таймаутами SDK и общим дедлайном генерации (REPLY_DEADLINE).
"""

import socket
import threading
import time

_real_getaddrinfo = socket.getaddrinfo

# Запасные IPv4-адреса — на случай «резолвер лёг, а кэша ещё нет»
# (например, сразу после деплоя). Проверить актуальность можно командой
# `nslookup api.telegram.org` с любой машины.
PINNED_HOSTS = {
    "api.telegram.org": ["149.154.166.110", "149.154.167.220"],
}

# Потолок ожидания системного резолвера, секунды. Живой резолвер отвечает
# за миллисекунды; кто не уложился — завис, и ждать его дольше нет смысла.
RESOLVE_TIMEOUT = 5.0

# Сколько секунд доверять свежему кэшу, не переспрашивая резолвер.
# Протухший кэш не выбрасывается — остаётся запасным на случай зависания.
CACHE_TTL = 3600.0

_cache: dict = {}  # ключ — аргументы getaddrinfo, значение — (результат, когда)
_cache_lock = threading.Lock()


def _fallback_addrinfo(host, port):
    """Собирает результат getaddrinfo вручную из известных IP."""
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))
        for ip in PINNED_HOSTS[host]
    ]


def _resolve_with_ceiling(args):
    """
    Системный резолв в отдельном потоке с потолком времени.

    Возвращает словарь: {"result": ...} при успехе, {"error": ...} при
    ошибке, пустой — если резолвер завис. Зависший поток остаётся зомби
    и никому не мешает (урок v6: пул потоков такие зомби забивают).
    """
    box: dict = {}

    def runner():
        try:
            box["result"] = _real_getaddrinfo(*args)
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout=RESOLVE_TIMEOUT)
    return box


def _ips(addrinfo) -> list:
    return sorted({entry[4][0] for entry in addrinfo})


def _pinned_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    # Куда не вмешиваемся: чужие хосты, IPv6-only запросы (запасные IP —
    # только IPv4) и нечисловые порты (запасные кортежи собираются с числом).
    if (host not in PINNED_HOSTS
            or family not in (0, socket.AF_INET)
            or not isinstance(port, int)):
        return _real_getaddrinfo(host, port, family, type, proto, flags)

    key = (host, port, family, type, proto, flags)
    with _cache_lock:
        cached = _cache.get(key)
    if cached and time.monotonic() - cached[1] < CACHE_TTL:
        return cached[0]

    t0 = time.monotonic()
    box = _resolve_with_ceiling(key)

    if "result" in box:
        with _cache_lock:
            _cache[key] = (box["result"], time.monotonic())
        print(f"DNS {host} → {_ips(box['result'])} "
              f"за {time.monotonic() - t0:.2f}с (кэширую)", flush=True)
        return box["result"]

    # Резолвер завис или ответил ошибкой — работаем без него
    reason = (f"ошибка: {box['error']}" if "error" in box
              else f"ЗАВИС (>{RESOLVE_TIMEOUT:.0f}с)")
    if cached:
        source, result = "устаревший кэш", cached[0]
    else:
        source, result = "известные IP Telegram", _fallback_addrinfo(host, port)
    print(f"✗ Системный DNS для {host} {reason} — "
          f"использую {source}: {_ips(result)}", flush=True)
    return result


def install_dns_bypass():
    """Включает обход: подменяет socket.getaddrinfo. Вызывать один раз при
    старте, до первого обращения к Telegram. Библиотеки (requests/urllib3)
    берут socket.getaddrinfo в момент вызова, поэтому подмена их накрывает."""
    socket.getaddrinfo = _pinned_getaddrinfo
