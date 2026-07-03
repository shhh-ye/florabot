"""
Telegram + Instagram AI-бот с RAG (поиск по базе знаний)
------------------------------------
Что делает этот сервер:
1. Принимает вебхуки от Telegram и от Instagram (входящие сообщения)
2. Перед каждым ответом ищет в векторной базе (ChromaDB) фрагменты
   базы знаний, релевантные вопросу клиента (RAG)
3. Передаёт сообщение + найденные знания + историю диалога в OpenAI API
4. Модель при необходимости сама заглядывает в Google Таблицу за точными
   данными (инструмент search_google_sheet)
5. Отправляет ответ обратно в тот же канал, откуда пришло сообщение

Логика бота живёт в пакете bot/ (см. bot/llm.py, bot/rag/).
Все настройки берутся из переменных окружения — см. env.example
"""

import json
import os

from flask import Flask, request, jsonify

from bot import config
from bot.channels import send_instagram_message, send_telegram_message
from bot.llm import generate_reply
from bot.rag.indexer import ensure_index

app = Flask(__name__)

# При старте сервера строим RAG-индекс, если его ещё нет (после деплоя
# на Render диск пустой). Если индекс уже построен — шаг мгновенный.
ensure_index()


# ---------------------------------------------------------------------------
# Вебхук: приём сообщений от Telegram
# ---------------------------------------------------------------------------
@app.route("/webhook/telegram", methods=["POST"])
def receive_telegram_webhook():
    # Проверяем секретный токен — чтобы вебхук не мог дёргать кто попало
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != config.TELEGRAM_WEBHOOK_SECRET:
        return "Forbidden", 403

    data = request.get_json()
    print("Входящее событие (Telegram):", json.dumps(data, ensure_ascii=False))

    try:
        message = data.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        text = message.get("text")

        if chat_id and text:
            reply = generate_reply(text, chat_id=f"tg:{chat_id}")
            send_telegram_message(chat_id, reply)
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

                reply = generate_reply(text, chat_id=f"ig:{sender_id}")
                send_instagram_message(sender_id, reply)
    except Exception as e:
        print("Ошибка обработки вебхука Instagram:", e)

    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def health_check():
    return "Бот работает", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
