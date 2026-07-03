"""
Работа с Google Таблицей.

Таблица используется в двух местах:
1. При индексации — строки попадают в векторную базу (см. rag/indexer.py).
2. Как инструмент модели search_google_sheet — для точного поиска актуальных
   данных (наличие, цены) в момент ответа.
"""

import json

import gspread
from google.oauth2.service_account import Credentials

from bot import config


def _get_sheet():
    creds_dict = json.loads(config.GOOGLE_CREDENTIALS_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    # Запрос к Google без таймаута может висеть вечно и держать поток
    gc.set_timeout(config.HTTP_TIMEOUT)
    return gc.open_by_key(config.GOOGLE_SHEET_ID).sheet1


def get_all_rows() -> list[dict]:
    """Все строки таблицы (первая строка = заголовки колонок)."""
    return _get_sheet().get_all_records()


def search_google_sheet(query: str) -> str:
    """Ищет строки, где query встречается в любой из колонок."""
    if not (config.GOOGLE_SHEET_ID and config.GOOGLE_CREDENTIALS_JSON):
        return "Google Таблица не настроена."

    query_lower = query.lower()
    matches = []
    for row in get_all_rows():
        row_text = " ".join(str(v) for v in row.values()).lower()
        if query_lower in row_text:
            matches.append(row)

    if not matches:
        return "Ничего не найдено по этому запросу."

    return json.dumps(matches[:10], ensure_ascii=False)  # ограничим 10 строками
