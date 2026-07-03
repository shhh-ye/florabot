"""
Векторное хранилище: ChromaDB (локально, без отдельного сервера) +
embeddings через OpenAI API.

ChromaDB сохраняет индекс на диск в папку CHROMA_DIR, поэтому между
перезапусками локально ничего пересчитывать не нужно. На Render диск
эфемерный — индекс автоматически перестраивается при старте, если пуст
(см. app.py / indexer.ensure_index).
"""

import chromadb
from openai import OpenAI

from bot import config

_openai = OpenAI(api_key=config.OPENAI_API_KEY)

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


class VectorStore:
    """Тонкая обёртка над коллекцией ChromaDB."""

    def __init__(self):
        self._client = chromadb.PersistentClient(path=config.CHROMA_DIR)
        self._collection = self._client.get_or_create_collection(
            name=config.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        return self._collection.count()

    def upsert(self, chunks: list[dict]):
        """Добавляет/обновляет фрагменты (см. chunker.chunk_document)."""
        if not chunks:
            return
        texts = [c["text"] for c in chunks]
        self._collection.upsert(
            ids=[c["id"] for c in chunks],
            documents=texts,
            metadatas=[c["metadata"] for c in chunks],
            embeddings=embed_texts(texts),
        )

    def query(self, text: str, top_k: int | None = None) -> list[dict]:
        """Ищет top_k фрагментов, самых похожих на текст запроса."""
        top_k = top_k or config.RAG_TOP_K
        if self.count() == 0:
            return []

        result = self._collection.query(
            query_embeddings=embed_texts([text]),
            n_results=min(top_k, self.count()),
        )

        found = []
        for doc, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            found.append({"text": doc, "metadata": meta, "distance": distance})
        return found

    def reset(self):
        """Полностью удаляет и пересоздаёт коллекцию (для переиндексации с нуля)."""
        self._client.delete_collection(config.CHROMA_COLLECTION)
        self._collection = self._client.get_or_create_collection(
            name=config.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )


# Один общий экземпляр на процесс — создаётся лениво при первом обращении
_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
