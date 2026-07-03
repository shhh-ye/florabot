"""
История диалога: последние N сообщений для каждого клиента.

Хранится в памяти процесса — при перезапуске сервера история обнуляется.
Для MVP этого достаточно; когда появится своя БД (см. план продукта),
историю можно будет переложить туда, поменяв только этот модуль.
"""

import threading
from collections import defaultdict, deque

from bot import config

_lock = threading.Lock()
_histories: dict[str, deque] = defaultdict(
    lambda: deque(maxlen=config.HISTORY_MAX_MESSAGES)
)


def get_history(chat_id) -> list[dict]:
    """Возвращает историю как список сообщений для OpenAI API."""
    if chat_id is None:
        return []
    with _lock:
        return list(_histories[str(chat_id)])


def remember(chat_id, user_message: str, assistant_reply: str):
    """Дописывает пару «клиент → ответ бота» в историю."""
    if chat_id is None:
        return
    with _lock:
        history = _histories[str(chat_id)]
        history.append({"role": "user", "content": user_message})
        history.append({"role": "assistant", "content": assistant_reply})
