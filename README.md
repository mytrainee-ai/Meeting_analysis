# Meeting Analysis

Веб-сервис анализа записей встреч. Требования к продукту описаны в [PRD](docs/03-prd.md).

## Локальный запуск

Требуется Python 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Проверка сервера: `http://127.0.0.1:8000/health`.

## Структура

- `app/` — серверное приложение.
- `docs/` — описание продукта, техническое исследование и PRD.

Интерфейс и обработка видео будут добавлены на следующих этапах.

