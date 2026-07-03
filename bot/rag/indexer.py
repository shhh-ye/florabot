"""
Индексация базы знаний: собирает документы из двух источников и кладёт
их фрагменты в векторное хранилище.

Источники:
1. Папка knowledge/ — файлы .md и .txt (FAQ, доставка, уход за цветами...).
   Имя файла становится категорией фрагментов.
2. Google Таблица (если настроена) — каждая строка превращается в короткий
   текстовый фрагмент категории "catalog". Это даёт семантический поиск
   по каталогу; для точных актуальных цифр у модели остаётся инструмент
   search_google_sheet.

Запуск вручную: python scripts/index_knowledge.py [--reset]
"""

from pathlib import Path

from bot import config
from bot.rag.chunker import chunk_document
from bot.rag.store import VectorStore, get_store


def load_documents_from_dir(directory: str | None = None) -> list[dict]:
    """Читает все .md/.txt из папки базы знаний."""
    directory = directory or config.KNOWLEDGE_DIR
    docs = []
    base = Path(directory)
    if not base.is_dir():
        return docs

    for path in sorted(base.rglob("*")):
        if path.suffix.lower() not in (".md", ".txt"):
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        docs.append(
            {
                "source": str(path.relative_to(base)),
                "category": path.stem,
                "text": text,
            }
        )
    return docs


def load_documents_from_sheet() -> list[dict]:
    """Превращает строки Google Таблицы в документы (по одному на строку)."""
    if not (config.GOOGLE_SHEET_ID and config.GOOGLE_CREDENTIALS_JSON):
        return []

    # Импорт внутри функции, чтобы бот работал и без настроенной таблицы
    from bot.sheets import get_all_rows

    docs = []
    for i, row in enumerate(get_all_rows()):
        line = "; ".join(f"{key}: {value}" for key, value in row.items() if str(value).strip())
        if not line:
            continue
        docs.append(
            {
                "source": f"google-sheet:row-{i + 2}",  # +2: заголовки + нумерация с 1
                "category": "catalog",
                "text": line,
            }
        )
    return docs


def reindex(store: VectorStore | None = None, reset: bool = False) -> int:
    """
    Индексирует все документы. Возвращает число проиндексированных фрагментов.

    reset=True — сначала стереть старый индекс (нужно, если документы
    удалялись или сильно менялись; иначе старые фрагменты останутся).
    """
    store = store or get_store()
    if reset:
        store.reset()

    documents = load_documents_from_dir()
    try:
        documents += load_documents_from_sheet()
    except Exception as e:
        # Таблица недоступна — индексируем хотя бы файлы, бот не должен падать
        print("Не удалось прочитать Google Таблицу при индексации:", e)

    all_chunks = []
    for doc in documents:
        all_chunks.extend(chunk_document(doc["text"], doc["source"], doc["category"]))

    store.upsert(all_chunks)
    return len(all_chunks)


def ensure_index(store: VectorStore | None = None):
    """
    Строит индекс при старте сервера, если он пуст (например, после деплоя
    на Render, где диск эфемерный). Если индекс уже есть — ничего не делает.
    """
    store = store or get_store()
    if store.count() > 0:
        print(f"RAG-индекс уже построен: {store.count()} фрагментов")
        return
    try:
        total = reindex(store)
        print(f"RAG-индекс построен при старте: {total} фрагментов")
    except Exception as e:
        # Без индекса бот всё равно отвечает (просто без базы знаний)
        print("Не удалось построить RAG-индекс при старте:", e)
