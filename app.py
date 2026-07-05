"""
Telegram + Instagram AI-бот с RAG (поиск по базе знаний)
------------------------------------
Что делает этот сервер:
1. Принимает вебхуки от Telegram и от Instagram (входящие сообщения)
2. Перед каждым ответом ищет в векторном индексе фрагменты базы знаний,
   релевантные вопросу клиента (RAG)
3. Передаёт сообщение + найденные знания + историю диалога в OpenAI API
4. Модель при необходимости сама заглядывает в Google Таблицу за точными
   данными (инструмент search_google_sheet)
5. Отправляет ответ обратно в тот же канал, откуда пришло сообщение

Логика бота живёт в пакете bot/ (см. bot/llm.py, bot/rag/).
Все настройки берутся из переменных окружения — см. env.example
"""

import hmac
import json
import os
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

# Вывод на Render идёт в пайп и буферизуется блоками: print() из фоновых
# потоков виден в логах только при переполнении буфера или смерти процесса.
# Включаем построчный сброс, иначе по логам невозможно понять, что происходит.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from flask import Flask, request, jsonify

from bot import config
from bot.channels import send_instagram_message, send_telegram_message
from bot.llm import generate_reply
from bot.rag.indexer import ensure_index

app = Flask(__name__)

# Мессенджеры повторяют доставку события, если не получили ответ 200 вовремя
# (а наш ответ занимает секунды: LLM + инструменты). Помним ID последних
# событий, чтобы не ответить клиенту дважды на одно и то же сообщение.
_seen_events = deque(maxlen=500)
_seen_lock = threading.Lock()


def _is_duplicate_event(event_id) -> bool:
    """True, если это событие уже обрабатывали (повторная доставка)."""
    if event_id is None:
        return False
    with _seen_lock:
        if event_id in _seen_events:
            return True
        _seen_events.append(event_id)
        return False

# При старте сервера строим RAG-индекс, если его ещё нет (после деплоя
# на Render диск пустой). Строго в фоновом потоке: индексация ходит в
# OpenAI и Google Таблицу, и если делать её при импорте, воркер не успевает
# начать отвечать за таймаут gunicorn — тот убивает его ещё до старта, и
# бот умирает в вечном цикле перезапусков. Пока индекс строится, бот уже
# отвечает (просто без базы знаний — llm.py это переживает).
threading.Thread(target=ensure_index, daemon=True).start()


def _reply_in_background(handler, *args):
    """
    Генерирует и отправляет ответ в фоновом потоке.

    Зачем: генерация ответа (RAG + модель + походы в Google Таблицу) может
    занимать больше 30 секунд, а gunicorn убивает воркер, который держит
    запрос дольше своего таймаута. Мессенджер при этом не получает 200 и
    начинает повторять доставку — воркеры гибнут по кругу, бот "умирает".
    Поэтому вебхук отвечает 200 сразу, а ответ клиенту уходит из потока.
    """
    threading.Thread(target=handler, args=args, daemon=True).start()


# Генерация выполняется в пуле с жёстким дедлайном (REPLY_DEADLINE): что бы
# ни зависло внутри (ретраи SDK, сеть, лимиты API) — клиент не остаётся без
# ответа навсегда, а получает извинение. Пул ограничен, чтобы зависшие
# генерации не плодили потоки без предела.
_reply_pool = ThreadPoolExecutor(max_workers=4)

FALLBACK_REPLY = (
    "Извините, я сейчас отвечаю медленнее обычного — что-то с связью. "
    "Напишите, пожалуйста, ещё раз через пару минут."
)


def _generate_with_deadline(text, chat_key) -> str:
    future = _reply_pool.submit(generate_reply, text, chat_key)
    try:
        return future.result(timeout=config.REPLY_DEADLINE)
    except FutureTimeout:
        print(f"[{chat_key}] не уложились в {config.REPLY_DEADLINE}с — "
              "отправляю запасной ответ", flush=True)
        return FALLBACK_REPLY
    except Exception as e:
        print(f"[{chat_key}] ошибка генерации ответа: {e}", flush=True)
        return FALLBACK_REPLY


def _process_telegram_message(chat_id, text):
    try:
        reply = _generate_with_deadline(text, f"tg:{chat_id}")
        send_telegram_message(chat_id, reply)
    except Exception as e:
        print("Ошибка обработки сообщения Telegram:", e, flush=True)


def _process_instagram_message(sender_id, text):
    try:
        reply = _generate_with_deadline(text, f"ig:{sender_id}")
        send_instagram_message(sender_id, reply)
    except Exception as e:
        print("Ошибка обработки сообщения Instagram:", e, flush=True)


# ---------------------------------------------------------------------------
# Вебхук: приём сообщений от Telegram
# ---------------------------------------------------------------------------
@app.route("/webhook/telegram", methods=["POST"])
def receive_telegram_webhook():
    # Проверяем секретный токен — чтобы вебхук не мог дёргать кто попало
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not hmac.compare_digest(secret or "", config.TELEGRAM_WEBHOOK_SECRET):
        return "Forbidden", 403

    data = request.get_json()
    print("Входящее событие (Telegram):", json.dumps(data, ensure_ascii=False))

    if _is_duplicate_event(data.get("update_id")):
        return jsonify({"status": "duplicate"}), 200

    try:
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text")

        if chat_id and text:
            _reply_in_background(_process_telegram_message, chat_id, text)
    except Exception as e:
        print("Ошибка обработки вебхука Telegram:", e)

    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Вебхук: подтверждение подписки (GET) и приём сообщений (POST) от Instagram
# ---------------------------------------------------------------------------
@app.route("/webhook/instagram", methods=["GET"])
def verify_instagram_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == config.IG_VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook/instagram", methods=["POST"])
def receive_instagram_webhook():
    if not config.IG_ACCESS_TOKEN:
        # Instagram ещё не настроен (нет токена) — просто игнорируем
        return jsonify({"status": "instagram not configured"}), 503

    data = request.get_json()
    print("Входящее событие (Instagram):", json.dumps(data, ensure_ascii=False))

    try:
        for entry in data.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                sender_id = messaging_event["sender"]["id"]
                message = messaging_event.get("message", {})
                text = message.get("text")

                if not text:
                    continue  # пропускаем стикеры/вложения без текста
                if message.get("is_echo"):
                    # Instagram присылает вебхук и на НАШИ исходящие сообщения.
                    # Без этой проверки бот отвечал бы сам себе по кругу.
                    continue
                mid = message.get("mid")
                if mid and _is_duplicate_event(f"ig:{mid}"):
                    continue  # Meta повторил доставку этого сообщения

                _reply_in_background(_process_instagram_message, sender_id, text)
    except Exception as e:
        print("Ошибка обработки вебхука Instagram:", e)

    return jsonify({"status": "ok"}), 200


# Метка сборки: видна на главной странице сервиса. Позволяет за секунду
# проверить, какой код реально запущен на Render (открыть URL сервиса в
# браузере). Меняйте её при заметных правках, чтобы отличать версии.
BUILD_MARKER = "florabot v4 — диагностика + предохранитель ответа (2026-07-05)"


@app.route("/", methods=["GET"])
def health_check():
    return f"Бот работает. {BUILD_MARKER}", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
