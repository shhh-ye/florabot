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
import threading
import traceback
from collections import deque

from flask import Flask, request, jsonify

from bot import config
from bot.channels import send_instagram_message, send_telegram_message
from bot.llm import generate_reply
from bot.rag.indexer import ensure_index
from bot.selfcheck import run_selfcheck

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

def _startup_background():
    """
    Стартовые задачи, которым нужна сеть. Строго в фоновом потоке: если
    выполнять их при импорте, воркер не успевает начать отвечать за
    таймаут gunicorn — тот убивает его ещё до старта, и бот умирает в
    вечном цикле перезапусков. Пока они идут, бот уже отвечает.
    """
    # Самопроверка ключей/токенов — результат в логе, строки SELF-CHECK
    if config.STARTUP_SELF_CHECK:
        try:
            run_selfcheck()
        except Exception:
            traceback.print_exc()
    # RAG-индекс строим, если его ещё нет (после деплоя на Render диск
    # пустой). Если индекс уже есть — шаг мгновенный. Пока строится,
    # бот отвечает без базы знаний — llm.py это переживает.
    ensure_index()


threading.Thread(target=_startup_background, daemon=True).start()


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


# Что сказать клиенту, если генерация ответа упала (OpenAI недоступен,
# кончился баланс API и т.п.) — лучше честное «попозже», чем молчание
FALLBACK_REPLY = (
    "Извините, у нас небольшие технические неполадки. "
    "Напишите, пожалуйста, чуть позже — мы обязательно ответим!"
)


def _process_telegram_message(chat_id, text):
    try:
        reply = generate_reply(text, chat_id=f"tg:{chat_id}")
    except Exception:
        print("Ошибка генерации ответа (Telegram):")
        traceback.print_exc()
        reply = FALLBACK_REPLY
    send_telegram_message(chat_id, reply)


def _process_instagram_message(sender_id, text):
    try:
        reply = generate_reply(text, chat_id=f"ig:{sender_id}")
    except Exception:
        print("Ошибка генерации ответа (Instagram):")
        traceback.print_exc()
        reply = FALLBACK_REPLY
    send_instagram_message(sender_id, reply)


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


@app.route("/", methods=["GET"])
def health_check():
    return "Бот работает", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
