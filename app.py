"""
Instagram AI-бот на базе OpenAI API (ChatGPT)
------------------------------------
Что делает этот сервер:
1. Принимает вебхуки от Instagram (входящие DM)
2. Передаёт сообщение пользователя в OpenAI API
3. Даёт модели "инструмент" search_google_sheet — чтобы она могла сама
   заглянуть в вашу Google Таблицу за нужной информацией
4. Отправляет ответ обратно пользователю в Instagram Direct

Все настройки (ключи, токены) берутся из переменных окружения — см. .env.example
"""

import os
import json
import requests
from flask import Flask, request, jsonify
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Конфигурация (из переменных окружения — задаются на Render, см. README.md)
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")  # можно поменять на другую модель
IG_ACCESS_TOKEN = os.environ["IG_ACCESS_TOKEN"]          # токен страницы/аккаунта Instagram
IG_VERIFY_TOKEN = os.environ["IG_VERIFY_TOKEN"]          # произвольная строка, которую вы сами придумаете
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]          # ID таблицы из её URL
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]  # содержимое service account JSON целиком

# Здесь пропишите, как именно бот должен себя вести — это "мозг" бота.
SYSTEM_PROMPT = """
Ты — ИИ-ассистент бренда в Instagram Direct.
Отвечай дружелюбно, кратко и по делу, на языке, на котором пишет клиент.
Если для ответа нужна конкретная информация (цены, наличие, условия) —
ОБЯЗАТЕЛЬНО используй инструмент search_google_sheet, а не придумывай данные.
Если информации в таблице нет — честно скажи, что уточнишь у менеджера.
"""

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------------------------------------------------------------------------
# Подключение к Google Sheets
# ---------------------------------------------------------------------------
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(GOOGLE_SHEET_ID).sheet1


def search_google_sheet(query: str) -> str:
    """Ищет строки в таблице, где query встречается в любой из колонок."""
    sheet = get_sheet()
    rows = sheet.get_all_records()  # первая строка таблицы = заголовки колонок
    query_lower = query.lower()

    matches = []
    for row in rows:
        row_text = " ".join(str(v) for v in row.values()).lower()
        if query_lower in row_text:
            matches.append(row)

    if not matches:
        return "Ничего не найдено по этому запросу."

    return json.dumps(matches[:10], ensure_ascii=False)  # ограничим 10 строками


# ---------------------------------------------------------------------------
# Описание инструмента для Claude (tool use)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_google_sheet",
            "description": (
                "Ищет информацию в базе данных компании (Google Таблица): "
                "товары, цены, наличие, условия доставки и т.п. "
                "Используй эту функцию, если для точного ответа клиенту "
                "нужны конкретные данные."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Ключевые слова для поиска, например название товара",
                    }
                },
                "required": ["query"],
            },
        },
    }
]


def ask_chatgpt(user_message: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        tools=TOOLS,
    )

    # Пока модель просит вызвать функцию — выполняем и отдаём результат обратно
    while response.choices[0].finish_reason == "tool_calls":
        assistant_message = response.choices[0].message
        messages.append(assistant_message)

        for tool_call in assistant_message.tool_calls:
            if tool_call.function.name == "search_google_sheet":
                args = json.loads(tool_call.function.arguments)
                result = search_google_sheet(args["query"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=TOOLS,
        )

    return response.choices[0].message.content or "Извините, не получилось сформировать ответ."


# ---------------------------------------------------------------------------
# Отправка ответа обратно в Instagram
# ---------------------------------------------------------------------------
def send_instagram_message(recipient_id: str, text: str):
    url = "https://graph.instagram.com/v21.0/me/messages"
    params = {"access_token": IG_ACCESS_TOKEN}
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    r = requests.post(url, params=params, json=payload)
    if r.status_code != 200:
        print("Ошибка отправки в Instagram:", r.status_code, r.text)


# ---------------------------------------------------------------------------
# Вебхук: подтверждение подписки (GET) и приём сообщений (POST)
# ---------------------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == IG_VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_webhook():
    data = request.get_json()
    print("Входящее событие:", json.dumps(data, ensure_ascii=False))

    try:
        for entry in data.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                sender_id = messaging_event["sender"]["id"]
                message = messaging_event.get("message", {})
                text = message.get("text")

                if not text:
                    continue  # пропускаем стикеры/вложения без текста

                reply = ask_chatgpt(text)
                send_instagram_message(sender_id, reply)
    except Exception as e:
        print("Ошибка обработки вебхука:", e)

    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def health_check():
    return "Бот работает", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
