"""
Тест ключевой гарантии проекта: цены и остатки НЕ попадают в RAG-фрагменты
каталога — актуальные цифры бот берёт только живым инструментом.

Запуск из корня проекта:  python tests/test_volatile.py
Сеть и настоящие ключи не нужны — чтение таблицы подменяется фейком.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:fake")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")
os.environ["RAG_INDEX_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="rag-test-"), "index.json"
)
os.environ["GOOGLE_SHEET_ID"] = "fake-id"
os.environ["GOOGLE_CREDENTIALS_JSON"] = "{}"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Подменяем чтение таблицы до использования indexer
import bot.sheets as sheets

sheets.get_all_rows = lambda: [
    {"Товар": "Букет «Нежность»", "Описание": "розовые пионы, романтичный",
     "Повод": "свидание", "Цена": 3500, "Наличие": 4, "Остаток, шт": 4},
    {"Товар": "Букет «Осень»", "Описание": "", "Повод": "",
     "Цена": 2000, "Наличие": 1},  # заполнены только название и волатильные
    {"Товар": "", "Цена": 999, "Наличие": 0},  # нечего индексировать
]

from bot.rag.indexer import _is_volatile_column, load_documents_from_sheet

assert _is_volatile_column("Цена")
assert _is_volatile_column("ЦЕНА, руб")
assert _is_volatile_column("Остаток, шт")
assert _is_volatile_column("stock_qty")
assert not _is_volatile_column("Описание")
assert not _is_volatile_column("Повод")
print("OK определение волатильных колонок")

docs = load_documents_from_sheet()
assert len(docs) == 2, f"ожидали 2 документа, получили {len(docs)}"

d = docs[0]["text"]
assert "Нежность" in d and "пионы" in d and "свидание" in d
assert "3500" not in d and "Цена" not in d and "Наличие" not in d and "Остаток" not in d
assert "search_google_sheet" in d  # указание проверять живьём
assert docs[0]["category"] == "catalog"
print("OK: цена/наличие отфильтрованы, описание и пометка на месте")

d2 = docs[1]["text"]
assert "Осень" in d2 and "2000" not in d2
print("OK: строка только с названием индексируется без цифр")

print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
