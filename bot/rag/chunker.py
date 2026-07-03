"""
Разбиение документов на небольшие фрагменты (chunks).

Зачем: embedding-поиск работает лучше по коротким связным кускам текста,
чем по целым документам. Разбиваем по абзацам и склеиваем их в фрагменты
примерно по CHUNK_SIZE символов с небольшим перекрытием, чтобы мысль,
разрезанная на границе, попала в оба соседних фрагмента.
"""

import hashlib
import re

from bot import config


def _split_paragraphs(text: str) -> list[str]:
    """Делит текст на абзацы (по пустым строкам), выкидывая пустые."""
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def split_text(
    text: str,
    max_chars: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """Разбивает текст на фрагменты не длиннее max_chars символов."""
    max_chars = max_chars or config.CHUNK_SIZE
    overlap = overlap if overlap is not None else config.CHUNK_OVERLAP

    chunks: list[str] = []
    current = ""

    for paragraph in _split_paragraphs(text):
        # Абзац длиннее лимита — режем его "в лоб" по символам с перекрытием
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            # max(1, ...) — защита от вечного цикла, если overlap >= max_chars
            step = max(1, max_chars - overlap)
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + max_chars])
                start += step
            continue

        # Абзац помещается — пробуем приклеить к текущему фрагменту
        candidate = (current + "\n\n" + paragraph) if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return chunks


def chunk_document(text: str, source: str, category: str = "general") -> list[dict]:
    """
    Превращает документ в список фрагментов для индексации.

    Каждый фрагмент — dict с полями:
      id       — стабильный идентификатор (hash от источника и текста),
                 чтобы повторная индексация обновляла, а не дублировала
      text     — сам текст фрагмента
      metadata — источник и категория (пригодятся для фильтрации и отладки)
    """
    chunks = []
    for i, chunk_text in enumerate(split_text(text)):
        digest = hashlib.sha256(f"{source}:{i}:{chunk_text}".encode()).hexdigest()[:16]
        chunks.append(
            {
                "id": f"{source}:{i}:{digest}",
                "text": chunk_text,
                "metadata": {"source": source, "category": category, "chunk": i},
            }
        )
    return chunks
