# analitikhhru

사용자의 Chrome/Edge 세션을 통해 hh.ru 검색 페이지에서 집계된 채용공고/이력서 수만 수집하는 로컬 HH 분석 대시보드입니다. OAuth 애플리케이션 자격증명은 필요하지 않습니다.

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
