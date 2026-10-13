# Architecture / Архитектура

```text
Browser -> Flask local web app -> Playwright/Chrome profile -> hh.ru search pages -> aggregate metrics -> SQLite/CSV -> dashboard
```

- Flask serves the local dashboard and API
- browser profile stores the user's HH session locally
- Playwright reads search result pages
- `data/` stores SQLite/CSV results and diagnostics
- command scripts handle setup, login, collection and diagnostics
