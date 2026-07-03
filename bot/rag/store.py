"""
Векторное хранилище: обычный Python-словарь в памяти + сохранение в
JSON-файл. Embeddings создаёт OpenAI API.

Почему не ChromaDB: у нас десятки фрагментов, а не десятки тысяч.
Перебрать их все косинусной близостью — доли миллисекунды, зато без
тяжёлых зависимостей (ChromaDB с обвязкой съедала сотни МБ памяти и
роняла бота на бесплатном тарифе Render с его лимитом в 512 МБ).
Качество поиска не меняется: векторы те же самые, от OpenAI.

Индекс сохраняется в файл RAG_INDEX_PATH, поэтому между перезапусками
локально ничего пересчитывать не нужно. На Render диск эфемерный —
индекс автоматически перестраивается при старте, если пуст
(см. app.py / indexer.ensure_index).
"""

import json
import math
import os
import threading

from openai import OpenAI

from bot import config

_openai = OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.OPENAI_TIMEOUT)

# Размер пачки текстов на один запрос к embeddings API
_EMBED_BATCH_SIZE = 100


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Создаёт векторы (embeddings) для списка текстов, батчами."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        batch = texts[start : start + _EMBED_BATCH_SIZE]
        response = _openai.embeddings.create(model=config.EMBEDDING_MODEL, input=batch)
        vectors.extend(item.embedding for item in response.data)
    return vectors


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Косинусное расстояние: 0 — одинаковый смысл, 2 — противоположный."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


class VectorStore:
    """Хранилище фрагментов с их векторами. Потокобезопасное."""

    def __init__(self, path: str | None = None):
        self._path = path or config.RAG_INDEX_PATH
        self._lock = threading.Lock()
        # id фрагмента -> {"text": ..., "metadata": ..., "embedding": [...]}
        self._items: dict[str, dict] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                self._items = json.load(f)
        except Exception as e:
            print("Не удалось прочитать файл индекса, начинаем с пустого:", e)
            self._items = {}

    def _save(self):
        # Пишем во временный файл и атомарно подменяем, чтобы убитый на
        # середине записи процесс не оставил после себя битый индекс
        tmp_path = self._path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._items, f, ensure_ascii=False)
        os.replace(tmp_path, self._path)

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def upsert(self, chunks: list[dict]):
        """Добавляет/обновляет фрагменты (см. chunker.chunk_document)."""
        if not chunks:
            return
        vectors = embed_texts([c["text"] for c in chunks])
        with self._lock:
            for chunk, vector in zip(chunks, vectors):
                self._items[chunk["id"]] = {
                    "text": chunk["text"],
                    "metadata": chunk["metadata"],
                    "embedding": vector,
                }
            self._save()

    def query(self, text: str, top_k: int | None = None) -> list[dict]:
        """Ищет top_k фрагментов, самых похожих на текст запроса."""
        top_k = top_k or config.RAG_TOP_K
        with self._lock:
            items = list(self._items.values())
        if not items:
            return []

        query_vector = embed_texts([text])[0]
        found = [
            {
                "text": item["text"],
                "metadata": item["metadata"],
                "distance": _cosine_distance(query_vector, item["embedding"]),
            }
            for item in items
        ]
        found.sort(key=lambda item: item["distance"])
        return found[:top_k]

    def reset(self):
        """Полностью очищает индекс (для переиндексации с нуля)."""
        with self._lock:
            self._items = {}
            self._save()


# Один общий экземпляр на процесс — создаётся лениво при первом обращении
_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
