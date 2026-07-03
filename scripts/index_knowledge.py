"""
Скрипт индексации базы знаний.

Читает документы из папки knowledge/ (и Google Таблицу, если настроена),
разбивает на фрагменты, создаёт embeddings и сохраняет в ChromaDB.

Запуск:
    python scripts/index_knowledge.py            # добавить/обновить документы
    python scripts/index_knowledge.py --reset    # пересоздать индекс с нуля

--reset нужен, если вы удаляли или переименовывали документы: без него
старые фрагменты останутся в индексе.
"""

import argparse
import sys
from pathlib import Path

# Чтобы скрипт можно было запускать из корня проекта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.rag.indexer import reindex  # noqa: E402
from bot.rag.store import get_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Индексация базы знаний для RAG")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Пересоздать индекс с нуля (стереть старые фрагменты)",
    )
    args = parser.parse_args()

    store = get_store()
    total = reindex(store, reset=args.reset)
    print(f"Готово: проиндексировано {total} фрагментов, всего в базе: {store.count()}")


if __name__ == "__main__":
    main()
