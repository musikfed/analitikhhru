# analitikhhru

Локальная браузерная панель аналитики HH, которая собирает только агрегированные количества вакансий и резюме со страниц поиска hh.ru через пользовательскую сессию Chrome/Edge. OAuth-приложение и токены API не требуются.

## Architecture / Архитектура

```text
Browser -> Flask local web app -> Playwright/Chrome profile -> hh.ru search pages -> aggregate metrics -> SQLite/CSV -> dashboard
```

## Project structure / Структура проекта

- Flask serves the local dashboard and API
- browser profile stores the user's HH session locally
- Playwright reads search result pages
- `data/` stores SQLite/CSV results and diagnostics
- command scripts handle setup, login, collection and diagnostics

## Contact / Контакты

- Email: feudor.lab@yandex.ru
- Telegram: @Feudorlab1
