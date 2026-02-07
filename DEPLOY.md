# Развёртывание Discord X Verif Bot

## Что это за проект

- **Discord-бот** (Python): команды `!xlink`, `!xstatus`, `!xunlink`, приём скриншотов для верификации.
- **OAuth-сервер** (FastAPI): страница «Link X Account» и callback для привязки аккаунта X (Twitter).
- **Оба процесса должны работать одновременно**: бот создаёт ссылку на ваш OAuth-сервер; без него `!xlink` не завершит привязку.

Репозиторий подготовлен к запуску **одной командой** (бот + веб-сервер в одном процессе).

---

## Что было исправлено / добавлено

1. **База данных**  
   Инициализация БД (`database.init_db()`) добавлена в `on_ready` бота, чтобы таблицы создавались даже если веб-сервер не запускался первым.

2. **Зависимости**  
   - Файл `requirements.txt` (без пробела в имени).  
   - В `requirements (3).txt` можно не использовать.

3. **Переменная PORT**  
   В `verify_service.py` порт берётся из `PORT` (для Railway), по умолчанию 8000.

4. **Один процесс для хостинга**  
   - `start.py` в фоновом потоке поднимает FastAPI (OAuth), в основном — Discord-бота.  
   - На Railway достаточно одного сервиса и одной команды запуска.

5. **Пример конфига**  
   Файл `.env.example` со списком переменных окружения.

---

## Локальный запуск

1. Установить Python 3.10+.

2. Создать виртуальное окружение и зависимости:
   ```bash
   cd /Users/vvozibic/Downloads/Discord_bot_X
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Скопировать конфиг и заполнить:
   ```bash
   cp .env.example .env
   ```
   В `.env` обязательно:
   - `DISCORD_TOKEN` — токен бота из [Discord Developer Portal](https://discord.com/developers/applications).
   - `X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_REDIRECT_URI` — из [X Developer Portal](https://developer.x.com/).
   - `LINK_SECRET` — произвольная длинная строка (один и тот же для бота и OAuth).

4. Для локального теста OAuth нужен доступ из интернета к `http://localhost:8000`, например:
   ```bash
   ngrok http 8000
   ```
   В X Developer Portal и в `.env` указать `X_REDIRECT_URI=https://ВАШ_НОМЕР.ngrok-free.app/x/callback`.

5. Запуск одного процесса (бот + веб):
   ```bash
   python start.py
   ```
   Либо по отдельности в двух терминалах:
   - `python verify_service.py`
   - `python bot.py`

6. В Discord Developer Portal у бота включить **Message Content Intent** (см. `INTENT_FIX.md`).

---

## Развёртывание на Railway

1. **Репозиторий**  
   Залить проект в GitHub (или подключить существующий репозиторий в Railway).

2. **Новый проект в Railway**  
   [railway.app](https://railway.app) → New Project → Deploy from GitHub → выбрать репозиторий.

3. **Переменные окружения**  
   В настройках сервиса (Variables) добавить все переменные из `.env.example`:

   | Переменная          | Пример / примечание |
   |---------------------|---------------------|
   | `DISCORD_TOKEN`     | Токен бота Discord |
   | `VERIFY_CHANNEL`    | Имя канала или ID (например `verify`) |
   | `X_CLIENT_ID`       | Из X Developer |
   | `X_CLIENT_SECRET`   | Из X Developer |
   | `X_REDIRECT_URI`    | **https://ВАШ-ПРОЕКТ.railway.app/x/callback** |
   | `X_SCOPES`         | `users.read tweet.read` (можно не менять) |
   | `LINK_SECRET`       | Длинная случайная строка (одинаковая у бота и OAuth) |

   В X Developer Portal в настройках приложения в **Callback URL** указать тот же `X_REDIRECT_URI`, что и в Railway.

4. **Команда запуска**  
   Railway по умолчанию может искать `Procfile`. В проекте уже есть:
   ```text
   web: python start.py
   ```
   Если Railway не подхватывает Procfile, в настройках сервиса в **Start Command** указать:
   ```bash
   python start.py
   ```

5. **Порт**  
   Railway сам задаёт `PORT`; `verify_service` и `start.py` его используют. Дополнительно порт настраивать не нужно.

6. **Домен**  
   В Railway выдать сервису публичный домен (например `*.railway.app`). Подставить его в `X_REDIRECT_URI` и в Callback URL в X Developer.

После деплоя в логах должны быть строки вроде:
- `Uvicorn running on http://0.0.0.0:XXXX`
- `Logged in as ... (ID: ...)`
- `Database initialized.`
- `Worker started. Waiting for images...`

---

## Важно

- **Один процесс**: на Railway один сервис = один контейнер. `start.py` запускает и веб, и бота — так и задумано.
- **Файлы и БД**: SQLite (`bot_database.db`) и JSON (`oauth_pending.json`, `x_links.json`) хранятся в файловой системе контейнера. При перезапуске/редиплое данные сохраняются только если у сервиса есть постоянный том (Railway Volumes). Иначе после каждого деплоя БД и ссылки будут пустыми.
- **Тяжёлые зависимости**: `easyocr` тянет за собой PyTorch; сборка на Railway может быть долгой (несколько минут). При нехватке памяти можно перейти на план с большим объёмом RAM.

Если что-то не запускается, проверь логи в Railway и убедись, что в Discord включён Message Content Intent и что `X_REDIRECT_URI` и Callback URL в X совпадают с доменом сервиса.
