"""
Конфигурация бота.

Все настройки берутся из переменных окружения (на Render — раздел Environment,
локально — файл .env). Обязательные переменные без значения уронят запуск
сразу с понятной ошибкой KeyError — так проще заметить, что что-то не задано.
"""

import os

# --- OpenAI ---------------------------------------------------------------
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
# Модель для создания embeddings (векторов) — используется RAG-поиском
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

# --- Telegram ---------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]

# --- Instagram (необязательно) ----------------------------------------------
IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN")
IG_VERIFY_TOKEN = os.environ.get("IG_VERIFY_TOKEN")

# --- Google Sheets (необязательно) -------------------------------------------
# Если заданы — строки таблицы попадут в векторный индекс при индексации,
# а у модели останется инструмент для точного поиска по таблице.
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

# --- RAG ----------------------------------------------------------------------
# Папка с документами базы знаний (FAQ, условия доставки, правила и т.п.)
KNOWLEDGE_DIR = os.environ.get("KNOWLEDGE_DIR", "knowledge")
# Куда ChromaDB сохраняет векторный индекс
CHROMA_DIR = os.environ.get("CHROMA_DIR", ".chroma")
# Имя коллекции в ChromaDB
CHROMA_COLLECTION = os.environ.get("CHROMA_COLLECTION", "florabot_knowledge")
# Сколько самых похожих фрагментов подставлять в контекст модели
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "6"))
# Размер фрагмента (в символах) при разбиении документов
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "800"))
# Перекрытие между соседними фрагментами
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "150"))

# --- История диалога -----------------------------------------------------------
# Сколько последних сообщений (реплик) держать в памяти для каждого клиента
HISTORY_MAX_MESSAGES = int(os.environ.get("HISTORY_MAX_MESSAGES", "10"))
