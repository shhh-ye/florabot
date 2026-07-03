"""
Базовые автотесты: чанкер, история диалога, разрезание длинных сообщений,
вебхуки (секрет, дедупликация повторных доставок, фильтр эха Instagram).

Запуск из корня проекта:  python tests/test_core.py
Сеть и настоящие ключи не нужны — всё внешнее подменяется фейками.
"""

import os
import sys
import tempfile
from pathlib import Path

# --- Фейковое окружение (до импорта модулей бота) --------------------------
os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:fake")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("IG_ACCESS_TOKEN", "fake-ig-token")
os.environ.setdefault("IG_VERIFY_TOKEN", "fake-verify")
os.environ["RAG_INDEX_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="rag-test-"), "index.json"
)
# Пустая папка знаний — чтобы старт сервера не пытался строить индекс через сеть
os.environ["KNOWLEDGE_DIR"] = tempfile.mkdtemp(prefix="knowledge-test-")
os.environ["HISTORY_MAX_MESSAGES"] = "4"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# --- Чанкер -----------------------------------------------------------------
from bot.rag.chunker import chunk_document, split_text

parts = split_text("абзац один\n\nабзац два\n\n" + "х" * 2000, max_chars=800, overlap=150)
assert all(len(p) <= 800 for p in parts), "фрагмент длиннее лимита"
assert any("абзац один" in p for p in parts)

# Кривой конфиг (overlap >= max_chars) не должен зациклить разбиение
parts = split_text("y" * 500, max_chars=100, overlap=100)
assert parts and all(len(p) <= 100 for p in parts)

chunks = chunk_document("текст документа", source="doc.md", category="faq")
assert chunks[0]["metadata"] == {"source": "doc.md", "category": "faq", "chunk": 0}
assert chunks[0]["id"].startswith("doc.md:0:")
print("OK чанкер")

# --- Векторное хранилище (поиск, сохранение на диск, reset) -------------------
# Embeddings подменяем фейком: «векторы» по ключевым словам, без сети
from bot.rag import store as store_module


def fake_embed(texts):
    return [
        [float("роз" in t.lower()), float("доставк" in t.lower()), 0.1]
        for t in texts
    ]


store_module.embed_texts = fake_embed

store_path = os.path.join(tempfile.mkdtemp(prefix="rag-store-test-"), "index.json")
store = store_module.VectorStore(path=store_path)
assert store.count() == 0 and store.query("что угодно") == []

store.upsert(chunk_document("Розы красные, свежие", source="roses.md"))
store.upsert(chunk_document("Доставка по городу за 2 часа", source="delivery.md"))
assert store.count() == 2

results = store.query("сколько идёт доставка?", top_k=1)
assert results[0]["metadata"]["source"] == "delivery.md", "нашёлся не тот фрагмент"

# Повторный upsert того же документа не создаёт дубликатов
store.upsert(chunk_document("Розы красные, свежие", source="roses.md"))
assert store.count() == 2, "повторная индексация задублировала фрагменты"

# Индекс переживает перезапуск (новый экземпляр читает тот же файл)
store2 = store_module.VectorStore(path=store_path)
assert store2.count() == 2
assert store2.query("розы", top_k=1)[0]["metadata"]["source"] == "roses.md"

store2.reset()
assert store2.count() == 0
print("OK векторное хранилище")

# --- История диалога ---------------------------------------------------------
from bot import memory

for i in range(10):
    memory.remember("chat1", f"вопрос {i}", f"ответ {i}")
history = memory.get_history("chat1")
assert len(history) == 4, "история должна обрезаться до HISTORY_MAX_MESSAGES"
assert history[-1] == {"role": "assistant", "content": "ответ 9"}
assert memory.get_history(None) == []
print("OK история диалога")

# --- Разрезание длинных сообщений для Telegram -------------------------------
from bot.channels import split_long_message

assert split_long_message("короткое") == ["короткое"]
long_text = "\n".join("строка " + str(i) for i in range(1000))
parts = split_long_message(long_text)
assert all(len(p) <= 4096 for p in parts)
assert "".join(p + "\n" for p in parts).split() == long_text.split(), "текст потерялся при разрезании"
parts = split_long_message("б" * 9000)  # без переносов — жёсткая нарезка
assert all(len(p) <= 4096 for p in parts) and sum(len(p) for p in parts) == 9000
print("OK разрезание длинных сообщений")

# --- Вебхуки: секрет, дедупликация, эхо Instagram ------------------------------
# Ответ генерируется в фоновом потоке (вебхук отвечает мессенджеру сразу),
# поэтому после запроса даём потоку время отработать.
import time

import app as app_module

replies = []
app_module.generate_reply = lambda text, chat_id=None: (replies.append((chat_id, text)), "ответ")[1]
app_module.send_telegram_message = lambda chat_id, text: None
app_module.send_instagram_message = lambda recipient_id, text: None

client = app_module.app.test_client()


def wait_replies(expected_count, timeout=3.0):
    """Ждёт, пока фоновые потоки не наберут expected_count ответов."""
    deadline = time.time() + timeout
    while time.time() < deadline and len(replies) < expected_count:
        time.sleep(0.02)
    time.sleep(0.1)  # даём шанс появиться ЛИШНЕМУ ответу, если он есть
    return len(replies)


# Неверный секрет → 403, обработки нет
r = client.post("/webhook/telegram", json={"update_id": 1}, headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
assert r.status_code == 403 and not replies

# Нормальное сообщение обрабатывается один раз, повтор доставки — игнорируется
update = {"update_id": 42, "message": {"chat": {"id": 7}, "text": "привет"}}
headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
assert client.post("/webhook/telegram", json=update, headers=headers).status_code == 200
assert client.post("/webhook/telegram", json=update, headers=headers).status_code == 200
assert wait_replies(1) == 1, "повторная доставка не должна дать второй ответ"
assert replies == [("tg:7", "привет")]

# Instagram: эхо собственного сообщения бота пропускается
echo_event = {"entry": [{"messaging": [{
    "sender": {"id": "u1"},
    "message": {"mid": "m-echo", "is_echo": True, "text": "я сам бот"},
}]}]}
assert client.post("/webhook/instagram", json=echo_event).status_code == 200
assert wait_replies(1) == 1, "на эхо отвечать нельзя"

# Instagram: обычное сообщение обрабатывается, повтор по mid — нет
ig_event = {"entry": [{"messaging": [{
    "sender": {"id": "u1"},
    "message": {"mid": "m-1", "text": "есть пионы?"},
}]}]}
assert client.post("/webhook/instagram", json=ig_event).status_code == 200
assert client.post("/webhook/instagram", json=ig_event).status_code == 200
assert wait_replies(2) == 2, "повтор по mid не должен дать второй ответ"
assert replies[1] == ("ig:u1", "есть пионы?")
print("OK вебхуки: секрет, дедупликация, эхо, фоновая обработка")

# --- Запасной ответ при сбое генерации -----------------------------------------
# Если OpenAI упал (нет баланса, сеть), клиент получает вежливое сообщение,
# а не тишину
sent = []


def broken_generate(text, chat_id=None):
    raise RuntimeError("OpenAI недоступен (тестовый сбой)")


app_module.generate_reply = broken_generate
app_module.send_telegram_message = lambda chat_id, text: sent.append((chat_id, text))

update = {"update_id": 99, "message": {"chat": {"id": 8}, "text": "алло"}}
assert client.post("/webhook/telegram", json=update, headers=headers).status_code == 200
deadline = time.time() + 3
while time.time() < deadline and not sent:
    time.sleep(0.02)
assert sent == [(8, app_module.FALLBACK_REPLY)], "клиент не получил запасной ответ"
print("OK запасной ответ при сбое генерации")

print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
