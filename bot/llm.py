"""
Ядро бота: формирование ответа с помощью OpenAI API + RAG.

Как устроен ответ на сообщение клиента:
1. RAG-поиск: находим в векторной базе фрагменты знаний, близкие к вопросу.
2. Собираем системный промпт: правила поведения + найденные фрагменты.
3. Добавляем историю диалога с этим клиентом.
4. Вызываем модель. Если ей нужны точные данные из таблицы — она сама
   вызовет инструмент search_google_sheet (цикл tool calls).
5. Запоминаем пару «вопрос → ответ» в историю.
"""

import json

from openai import OpenAI

from bot import config, memory
from bot.prompts import build_system_prompt
from bot.rag.retriever import retrieve_context
from bot.sheets import search_google_sheet

client = OpenAI(api_key=config.OPENAI_API_KEY)

# Описание инструмента для модели (tool use)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_google_sheet",
            "description": (
                "Точный поиск в таблице компании (Google Таблица): "
                "актуальное наличие товара, цены. Используй, когда клиенту "
                "нужны конкретные текущие данные, которых нет в базе знаний."
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


def generate_reply(user_message: str, chat_id=None) -> str:
    """Формирует ответ бота на сообщение клиента."""
    # 1-2. RAG-поиск и системный промпт с найденными знаниями
    try:
        context = retrieve_context(user_message)
    except Exception as e:
        # Проблема с индексом не должна ронять бота — отвечаем без базы знаний
        print("Ошибка RAG-поиска:", e)
        context = ""

    messages = [{"role": "system", "content": build_system_prompt(context)}]
    # 3. История диалога с этим клиентом
    messages += memory.get_history(chat_id)
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=messages,
        tools=TOOLS,
    )

    # 4. Пока модель просит вызвать инструмент — выполняем и отдаём результат
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
            model=config.OPENAI_MODEL,
            messages=messages,
            tools=TOOLS,
        )

    reply = response.choices[0].message.content or "Извините, не получилось сформировать ответ."

    # 5. Запоминаем диалог
    memory.remember(chat_id, user_message, reply)
    return reply
