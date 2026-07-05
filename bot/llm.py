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
import time

from openai import OpenAI

from bot import config, memory
from bot.prompts import build_system_prompt
from bot.rag.retriever import retrieve_context
from bot.sheets import search_google_sheet

# timeout ограничивает ОДНУ попытку запроса, а max_retries — сколько раз SDK
# повторит её сам при ошибках/лимитах (с растущей паузой, которая в timeout
# не входит). Без ограничения ретраев один вызов может растянуться на минуты.
client = OpenAI(
    api_key=config.OPENAI_API_KEY,
    timeout=config.OPENAI_TIMEOUT,
    max_retries=config.OPENAI_MAX_RETRIES,
)


def _log(message: str):
    """Лог с немедленным сбросом буфера — иначе строки видны только при
    смерти процесса, и по логам Render невозможно понять, где завис бот."""
    print(message, flush=True)

# Максимум циклов «модель просит инструмент → выполняем» на одно сообщение.
# Страховка: без лимита модель теоретически может дёргать инструмент
# бесконечно, сжигая токены и время ответа.
MAX_TOOL_ROUNDS = 5

# Описание инструмента для модели (tool use)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_google_sheet",
            "description": (
                "ЕДИНСТВЕННЫЙ источник актуальных цен, наличия и остатков "
                "(живое чтение Google Таблицы в момент запроса). "
                "Вызывай ВСЕГДА, когда собираешься назвать клиенту цену, "
                "наличие или остаток товара — в базе знаний этих данных нет."
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
    # Пошаговые логи с таймингами: если бот молчит, по последней строке
    # в логах видно, на каком именно шаге он завис.
    t0 = time.monotonic()
    _log(f"[{chat_id}] начинаю ответ")

    # 1-2. RAG-поиск и системный промпт с найденными знаниями
    try:
        context = retrieve_context(user_message)
        _log(f"[{chat_id}] RAG-поиск готов за {time.monotonic() - t0:.1f}с")
    except Exception as e:
        # Проблема с индексом не должна ронять бота — отвечаем без базы знаний
        _log(f"[{chat_id}] ошибка RAG-поиска (отвечаю без базы знаний): {e}")
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
    _log(f"[{chat_id}] модель ответила за {time.monotonic() - t0:.1f}с "
         f"(finish_reason={response.choices[0].finish_reason})")

    # 4. Пока модель просит вызвать инструмент — выполняем и отдаём результат
    rounds = 0
    while response.choices[0].finish_reason == "tool_calls" and rounds < MAX_TOOL_ROUNDS:
        assistant_message = response.choices[0].message
        messages.append(assistant_message)

        for tool_call in assistant_message.tool_calls:
            if tool_call.function.name == "search_google_sheet":
                args = json.loads(tool_call.function.arguments)
                _log(f"[{chat_id}] модель запросила таблицу: {args['query']!r}")
                result = search_google_sheet(args["query"])
                _log(f"[{chat_id}] таблица ответила за {time.monotonic() - t0:.1f}с")
            else:
                # На каждый tool_call обязан быть ответ, иначе API вернёт ошибку
                result = "Неизвестный инструмент."
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                }
            )

        rounds += 1
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            tools=TOOLS,
            # Лимит исчерпан — требуем финальный текстовый ответ без инструментов
            tool_choice="none" if rounds == MAX_TOOL_ROUNDS else "auto",
        )

    reply = response.choices[0].message.content or "Извините, не получилось сформировать ответ."
    _log(f"[{chat_id}] ответ готов за {time.monotonic() - t0:.1f}с")

    # 5. Запоминаем диалог
    memory.remember(chat_id, user_message, reply)
    return reply
