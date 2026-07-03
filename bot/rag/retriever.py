"""
Поиск релевантных знаний перед обращением к модели.

retrieve_context(вопрос) — находит в векторной базе top-k фрагментов,
самых близких по смыслу к сообщению клиента, и форматирует их в текстовый
блок, который подставляется в системный промпт.
"""

from bot import config
from bot.rag.store import get_store


def retrieve_context(query: str, top_k: int | None = None) -> str:
    """Возвращает найденные фрагменты одним текстовым блоком (или пустую строку)."""
    results = get_store().query(query, top_k=top_k or config.RAG_TOP_K)
    if not results:
        return ""

    parts = []
    for i, item in enumerate(results, start=1):
        source = item["metadata"].get("source", "?")
        parts.append(f"[Фрагмент {i} — источник: {source}]\n{item['text']}")
    return "\n\n".join(parts)
