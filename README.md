# Instagram AI-бот на Claude — инструкция по запуску

Бот принимает сообщения в Instagram Direct, при необходимости ищет ответ
в вашей Google Таблице и отвечает от лица Claude.

Всё делается без покупки сервера — используем бесплатный Render.com.

---

## Шаг 1. OpenAI API-ключ

1. Зайдите на https://platform.openai.com
2. Settings → API Keys → Create new secret key
3. Скопируйте ключ (начинается с `sk-proj-...` или `sk-...`) — понадобится дальше
4. Важно: на platform.openai.com нужно привязать карту и пополнить
   баланс (Billing) — без этого API не будет работать, даже с
   действующим ключом

---

## Шаг 2. Google Таблица + доступ для бота

1. Создайте Google Таблицу (или используйте существующую) со списком
   товаров/услуг/цен. Первая строка — заголовки колонок (например:
   `Товар | Цена | Наличие | Описание`)
2. Скопируйте **ID таблицы** — это часть ссылки между `/d/` и `/edit`:
   `docs.google.com/spreadsheets/d/ЭТОТ_ID/edit`
3. Зайдите на https://console.cloud.google.com
4. Создайте проект (если ещё нет) → **APIs & Services → Library** →
   найдите **Google Sheets API** → Enable
5. **APIs & Services → Credentials → Create Credentials → Service Account**
   → задайте любое имя → Create and Continue → Done
6. Откройте созданный service account → вкладка **Keys** → **Add Key →
   Create new key → JSON** → скачается файл — это и есть
   `GOOGLE_CREDENTIALS_JSON`
7. Откройте скачанный JSON, найдите поле `client_email` (выглядит как
   `xxx@xxx.iam.gserviceaccount.com`)
8. Откройте вашу Google Таблицу → кнопка **Share** → вставьте этот email
   → дайте доступ **Viewer** (на чтение достаточно)

---

## Шаг 3. Instagram / Meta

Вы это частично уже настроили (business-аккаунт + Facebook-страница +
Allow access to messages). Осталось получить токен и подключить вебхук:

1. Зайдите на https://developers.facebook.com → **My Apps → Create App**
   → тип **Business**
2. В приложении добавьте продукт **Instagram** (Instagram API setup with
   Instagram Login или через Facebook Login, в зависимости от версии
   консоли)
3. Привяжите вашу Facebook-страницу / Instagram-аккаунт в настройках
   продукта
4. Сгенерируйте **Access Token** для вашего Instagram-аккаунта — это и
   есть `IG_ACCESS_TOKEN`
5. Придумайте любую строку для `IG_VERIFY_TOKEN` (например
   `moi_bot_secret_2026`) — она нужна только для подтверждения вебхука

Настройку вебхука сделаем ПОСЛЕ деплоя на Render, в шаге 5 — потому что
для этого нужен готовый URL сервера.

---

## Шаг 4. Деплой на Render.com

1. Зарегистрируйтесь на https://render.com (можно через GitHub)
2. Загрузите папку с этими файлами (`app.py`, `requirements.txt`) к
   себе на GitHub в новый репозиторий
   — если не умеете работать с GitHub, просто создайте новый
   репозиторий на github.com, нажмите **Add file → Upload files** и
   перетащите все файлы из этой папки
3. В Render: **New → Web Service** → подключите этот GitHub-репозиторий
4. Настройки сервиса:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
5. В разделе **Environment** добавьте переменные (см. `.env.example`):
   - `OPENAI_API_KEY`
   - `OPENAI_MODEL` (необязательно, по умолчанию `gpt-4o`)
   - `IG_ACCESS_TOKEN`
   - `IG_VERIFY_TOKEN`
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_CREDENTIALS_JSON` — вставьте содержимое скачанного JSON
     файла целиком, одной строкой
6. Нажмите **Create Web Service** — Render соберёт и запустит сервер.
   После деплоя вы получите публичный URL вида:
   `https://ваш-бот.onrender.com`

---

## Шаг 5. Подключить вебхук в Meta

1. Вернитесь в developers.facebook.com → ваше приложение → раздел
   **Webhooks** (или **Instagram → Configuration**)
2. Укажите:
   - **Callback URL**: `https://ваш-бот.onrender.com/webhook`
   - **Verify Token**: то же значение, что вы задали в `IG_VERIFY_TOKEN`
3. Подпишитесь на поле **messages**
4. Нажмите Verify and Save — если всё настроено верно, подтверждение
   пройдёт автоматически (сервер ответит на проверочный запрос)

---

## Готово

Теперь напишите что-нибудь вашему бизнес-аккаунту в Instagram Direct —
Claude получит сообщение, при необходимости заглянет в Google Таблицу и
ответит.

## Что можно донастроить дальше

- **SYSTEM_PROMPT** в `app.py` — здесь пропишите тон, правила, что бот
  может/не может говорить
- **search_google_sheet** — сейчас это простой поиск по совпадению
  текста; можно усложнить логику под структуру вашей таблицы
- Добавить второй "инструмент" для Google Drive (поиск по документам) —
  скажите, если нужно, помогу дописать
- Render на бесплатном тарифе "засыпает" после 15 минут без запросов —
  первое сообщение после паузы будет обрабатываться на 20–30 секунд
  дольше. Если это критично — можно перейти на платный тариф ($7/мес)

## Если что-то не работает

Самая частая причина — неверно вставленный `GOOGLE_CREDENTIALS_JSON`
(его нужно вставить как есть, без переносов строк, целиком в одну
переменную). Логи ошибок смотрите в Render → ваш сервис → **Logs**.
