# analitikhhru

स्थानीय ब्राउज़र-आधारित HH विश्लेषण डैशबोर्ड, जो उपयोगकर्ता की Chrome/Edge सत्र के माध्यम से hh.ru खोज पृष्ठों से केवल समेकित vacancy/resume गणना एकत्र करता है। OAuth ऐप क्रेडेंशियल आवश्यक नहीं हैं।

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
