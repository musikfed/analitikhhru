#!/usr/bin/env python3
"""Сбор агрегированных чисел с hh.ru через обычный локальный браузер Chrome/Edge.

Пароли программа не принимает и не хранит. Пользователь вручную входит на hh.ru
в отдельном постоянном профиле браузера. Далее Playwright использует сохранённую
сессию и читает только агрегированные количества из поисковой выдачи.

Капча и подтверждение входа не обходятся: при их появлении пользователь должен
пройти проверку вручную в открытом браузере.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import BrowserContext, Page, Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright

from hh_market_monitor import (
    ProgressCallback,
    SearchQuery,
    calculate_search_force_model,
    connect_db,
    export_csv,
    init_db,
    load_queries,
    load_search_force_query,
    load_search_force_capacity,
    recalculate_search_force_history,
    ratio,
    save_search_force_snapshot,
    save_snapshot,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PROFILE_DIR = DATA_DIR / "hh-browser-profile"
ERROR_DIR = DATA_DIR / "browser-errors"
DEFAULT_CONFIG = BASE_DIR / "searches.json"
DEFAULT_DB = DATA_DIR / "hh_market.sqlite3"
DEFAULT_CSV = DATA_DIR / "hh_market.csv"

COUNT_RE = re.compile(
    r"(?P<count>\d[\d\s\u00a0\u202f]*)\s+"
    r"(?P<word>ваканси(?:я|и|й)|резюм(?:е|э))",
    flags=re.IGNORECASE,
)

BLOCK_MARKERS = (
    "подтвердите, что вы не робот",
    "captcha",
    "введите код с картинки",
    "доступ ограничен",
    "проверка безопасности",
    "слишком много запросов",
)

LOGIN_MARKERS = (
    "войти в аккаунт",
    "войдите в аккаунт",
    "вход в личный кабинет",
)


class BrowserCollectionError(RuntimeError):
    """Понятная пользователю ошибка браузерного сбора."""


class SessionRequiredError(BrowserCollectionError):
    """Нужно открыть браузер и войти/подтвердить сессию."""


def profile_exists() -> bool:
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())


def _channel_candidates() -> list[str]:
    requested = os.getenv("HH_BROWSER_CHANNEL", "chrome").strip().lower()
    result: list[str] = []
    for item in (requested, "chrome", "msedge"):
        if item and item not in result:
            result.append(item)
    return result


def _launch_persistent_context(
    playwright: Playwright,
    *,
    headless: bool,
    slow_mo: int = 0,
) -> tuple[BrowserContext, str]:
    """Открыть установленный Chrome, а при отсутствии — Edge."""

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for channel in _channel_candidates():
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel=channel,
                headless=headless,
                slow_mo=slow_mo,
                viewport={"width": 1440, "height": 1000},
                locale="ru-RU",
                args=["--start-maximized"],
            )
            return context, channel
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise BrowserCollectionError(
        "Не удалось открыть Google Chrome или Microsoft Edge. "
        "Проверьте, что один из браузеров установлен. "
        f"Техническая ошибка: {last_error}"
    )


def _first_page(context: BrowserContext) -> Page:
    return context.pages[0] if context.pages else context.new_page()


def open_login_browser() -> None:
    """Открыть отдельный браузер для ручного входа пользователя."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 72)
    print(" ВХОД В HH.RU")
    print(" 1. Войдите в hh.ru в открывшемся браузере.")
    print(" 2. Пройдите код/капчу вручную, если HH попросит.")
    print(" 3. После успешного входа полностью закройте окно браузера.")
    print(" Пароль остаётся только на странице hh.ru и не передаётся программе.")
    print("=" * 72)

    with sync_playwright() as playwright:
        context, channel = _launch_persistent_context(playwright, headless=False)
        print(f"Открыт браузер: {channel}")
        page = _first_page(context)
        page.goto("https://hh.ru/", wait_until="domcontentloaded", timeout=90_000)
        try:
            page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass

        # Ждём, пока пользователь сам закроет браузер. Периодический доступ к
        # списку страниц позволяет корректно завершиться после закрытия окна.
        while True:
            try:
                if not context.pages:
                    break
                time.sleep(1)
            except Exception:  # браузер закрыт
                break
        try:
            context.close()
        except Exception:  # noqa: BLE001
            pass


def _page_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=15_000)
    except Exception:  # noqa: BLE001
        return ""


def _dismiss_cookie_banners(page: Page) -> None:
    for text in ("Принять", "Согласиться", "Хорошо", "Понятно"):
        try:
            locator = page.get_by_role("button", name=text, exact=True)
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=1_500)
                return
        except Exception:  # noqa: BLE001
            continue


def _detect_block_or_login(page: Page, body_text: str) -> None:
    lower = body_text.lower()
    if any(marker in lower for marker in BLOCK_MARKERS):
        raise SessionRequiredError(
            "HH показал капчу или проверку безопасности. Нажмите «Открыть Chrome для входа», "
            "пройдите проверку вручную, закройте браузер и повторите сбор."
        )
    # На поисковой странице могут быть обычные кнопки «Войти», поэтому считаем
    # сессию обязательной только при явной полноэкранной форме входа.
    title = (page.title() or "").lower()
    url = page.url.lower()
    if "/account/login" in url or any(marker in lower[:2500] for marker in LOGIN_MARKERS):
        raise SessionRequiredError(
            "HH перенаправил на страницу входа. Нажмите «Открыть Chrome для входа», "
            "авторизуйтесь вручную и закройте браузер."
        )
    if "403" in title or "forbidden" in title:
        raise SessionRequiredError(
            "HH вернул страницу 403. Откройте Chrome для входа и подтвердите сессию вручную."
        )


def _normalise_count(raw: str) -> int:
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(raw)
    return int(digits)


def _extract_count(page: Page, kind: str) -> tuple[int, str]:
    """Извлечь главное число выдачи, используя селекторы и безопасный fallback."""

    body_text = _page_text(page)
    _detect_block_or_login(page, body_text)

    candidates: list[tuple[int, int, str]] = []
    serial = 0

    def add_text(text: str, score: int, source: str) -> None:
        nonlocal serial
        if not text:
            return
        for match in COUNT_RE.finditer(text):
            word = match.group("word").lower()
            if kind == "vacancy" and not word.startswith("вакан"):
                continue
            if kind == "resume" and not word.startswith("резюм"):
                continue
            try:
                value = _normalise_count(match.group("count"))
            except ValueError:
                continue
            # Блокируем очевидные локальные счётчики карточек/компаний.
            context_start = max(0, match.start() - 70)
            context_end = min(len(text), match.end() + 70)
            context = " ".join(text[context_start:context_end].split())
            context_lower = context.lower()
            local_score = score
            if "найден" in context_lower or "показать" in context_lower:
                local_score += 30
            if "работа " in context_lower and kind == "vacancy":
                local_score += 15
            if "активн" in context_lower and kind == "vacancy":
                local_score -= 25  # часто это счётчик вакансий отдельной компании
            candidates.append((local_score, -serial, f"{value}|{source}|{context}"))
            serial += 1

    selector_specs = [
        ('[data-qa="vacancies-search-header"]', 170),
        ('[data-qa*="search-header"]', 150),
        ('[data-qa*="resume-search"]', 150),
        ("h1", 140),
        ("h2", 105),
    ]
    if kind == "resume":
        selector_specs = [
            ('button:has-text("Показать")', 210),
            ('[role="button"]:has-text("Показать")', 200),
            *selector_specs,
        ]

    for selector, score in selector_specs:
        try:
            texts = page.locator(selector).all_inner_texts()
        except Exception:  # noqa: BLE001
            continue
        for text in texts[:30]:
            add_text(text, score, selector)

    # Fallback: анализ строк страницы. Важнее строки с «Найдено» и «Показать».
    for line in body_text.splitlines()[:700]:
        compact = " ".join(line.split())
        if not compact or len(compact) > 280:
            continue
        score = 35
        lower = compact.lower()
        if "найден" in lower:
            score = 100
        elif "показать" in lower:
            score = 120
        add_text(compact, score, "body-line")

    if not candidates:
        raise BrowserCollectionError(
            f"Не удалось найти количество {'вакансий' if kind == 'vacancy' else 'резюме'} "
            "на странице HH. Возможно, изменилась разметка сайта."
        )

    candidates.sort(reverse=True)
    _, _, packed = candidates[0]
    value_raw, source, context = packed.split("|", 2)
    return int(value_raw), f"{source}: {context}"


def _save_error_artifacts(page: Page, name: str) -> None:
    ERROR_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_")[:80] or "error"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = ERROR_DIR / f"{stamp}_{safe_name}"
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _navigate_and_count(page: Page, url: str, kind: str, artifact_name: str) -> tuple[int, str]:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        _dismiss_cookie_banners(page)
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(1_400)
        return _extract_count(page, kind)
    except Exception:
        _save_error_artifacts(page, artifact_name)
        raise


def build_vacancy_url(query: SearchQuery, period_days: int | None) -> str:
    params: dict[str, str | int] = {
        "text": query.text,
        "area": query.area,
        "ored_clusters": "true",
        "enable_snippets": "true",
    }
    if period_days is not None:
        params["period"] = period_days
    return f"https://hh.ru/search/vacancy?{urlencode(params)}"


def build_resume_url(query: SearchQuery, period_days: int | None) -> str:
    params: dict[str, str | int] = {
        "text": query.text,
        "area": query.area,
        "logic": "normal",
        "pos": "full_text",
        "exp_period": "all_time",
        "isDefaultArea": "true",
        "job_search_status": "active_search",
    }
    if period_days is not None:
        params["period"] = period_days
    return f"https://hh.ru/search/resume?{urlencode(params)}"


def check_browser_session() -> dict[str, Any]:
    """Проверить, открывается ли HH и читается ли агрегированный счётчик."""

    test_query = SearchQuery("session_test", "DevOps-инженер", "DevOps-инженер", "1")
    result: dict[str, Any] = {
        "ok": False,
        "profile_exists": profile_exists(),
        "profile_dir": str(PROFILE_DIR),
        "checks": [],
    }
    with sync_playwright() as playwright:
        context, channel = _launch_persistent_context(playwright, headless=False)
        result["browser"] = channel
        page = _first_page(context)
        try:
            value, source = _navigate_and_count(
                page,
                build_vacancy_url(test_query, 30),
                "vacancy",
                "diagnostic_vacancy",
            )
            result["checks"].append(
                {"name": "vacancy_page", "ok": True, "details": {"found": value, "source": source}}
            )
        except Exception as exc:  # noqa: BLE001
            result["checks"].append(
                {"name": "vacancy_page", "ok": False, "error": {"message": str(exc)}}
            )

        try:
            value, source = _navigate_and_count(
                page,
                build_resume_url(test_query, 30),
                "resume",
                "diagnostic_resume",
            )
            result["checks"].append(
                {"name": "resume_page", "ok": True, "details": {"found": value, "source": source}}
            )
        except Exception as exc:  # noqa: BLE001
            result["checks"].append(
                {"name": "resume_page", "ok": False, "error": {"message": str(exc)}}
            )
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass

    result["ok"] = any(check.get("ok") for check in result["checks"])
    return result


def collect_all_browser(
    config_path: Path,
    db_path: Path,
    csv_path: Path,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Собрать все позиции через браузер и сохранить агрегированные числа."""

    queries = load_queries(config_path)
    force_query = load_search_force_query(config_path)
    force_capacity = load_search_force_capacity(config_path)
    collected_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    init_db(conn)
    recalculate_search_force_history(conn, force_capacity)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    failed = 0
    force_failed = 0
    force_row: dict[str, Any] | None = None
    total = len(queries) + (1 if force_query else 0)

    with sync_playwright() as playwright:
        context, channel = _launch_persistent_context(playwright, headless=False)
        page = _first_page(context)
        try:
            for index, query in enumerate(queries, start=1):
                if progress:
                    progress(
                        {
                            "phase": "collecting",
                            "current": index,
                            "total": total,
                            "query_id": query.query_id,
                            "label": query.label,
                            "source": "browser",
                        }
                    )

                vacancy_active: int | None = None
                vacancy_30d: int | None = None
                resume_active: int | None = None
                resume_30d: int | None = None
                row_errors: list[str] = []

                try:
                    vacancy_active, _ = _navigate_and_count(
                        page,
                        build_vacancy_url(query, None),
                        "vacancy",
                        f"{query.query_id}_vacancy_active",
                    )
                except Exception as exc:  # noqa: BLE001
                    row_errors.append(f"Вакансии сейчас: {exc}")

                try:
                    vacancy_30d, _ = _navigate_and_count(
                        page,
                        build_vacancy_url(query, 30),
                        "vacancy",
                        f"{query.query_id}_vacancy_30d",
                    )
                except Exception as exc:  # noqa: BLE001
                    row_errors.append(f"Вакансии за 30 дней: {exc}")

                try:
                    resume_active, _ = _navigate_and_count(
                        page,
                        build_resume_url(query, None),
                        "resume",
                        f"{query.query_id}_resume_active",
                    )
                except Exception as exc:  # noqa: BLE001
                    row_errors.append(f"Резюме сейчас: {exc}")

                try:
                    resume_30d, _ = _navigate_and_count(
                        page,
                        build_resume_url(query, 30),
                        "resume",
                        f"{query.query_id}_resume_30d",
                    )
                except Exception as exc:  # noqa: BLE001
                    row_errors.append(f"Резюме за 30 дней: {exc}")

                row = {
                    "query_id": query.query_id,
                    "label": query.label,
                    "query_text": query.text,
                    "area": query.area,
                    "vacancy_active_now": vacancy_active,
                    "vacancy_published_30d": vacancy_30d,
                    "resume_active_now": resume_active,
                    "resume_active_published_30d": resume_30d,
                    "competition_stock": ratio(resume_active, vacancy_active),
                    "competition_flow": ratio(resume_30d, vacancy_30d),
                    "resume_error": "; ".join(row_errors) or None,
                }
                save_snapshot(conn, collected_at, row)
                rows.append(row)

                if vacancy_active is None and vacancy_30d is None and resume_active is None and resume_30d is None:
                    failed += 1
                    errors.append(
                        {
                            "query_id": query.query_id,
                            "label": query.label,
                            "error": "; ".join(row_errors) or "Данные не получены",
                        }
                    )
                elif row_errors:
                    errors.append(
                        {
                            "query_id": query.query_id,
                            "label": query.label,
                            "error": "; ".join(row_errors),
                        }
                    )

            if force_query is not None:
                if progress:
                    progress(
                        {
                            "phase": "collecting",
                            "current": len(queries) + 1,
                            "total": total,
                            "query_id": force_query.query_id,
                            "label": "Proxy-индекс поддержки поиска",
                            "source": "browser",
                        }
                    )

                recruiter_active: int | None = None
                recruiter_30d: int | None = None
                recruiter_resume_active: int | None = None
                recruiter_resume_30d: int | None = None
                force_errors: list[str] = []
                try:
                    recruiter_active, _ = _navigate_and_count(
                        page,
                        build_vacancy_url(force_query, None),
                        "vacancy",
                        "search_force_recruiters_active",
                    )
                except Exception as exc:  # noqa: BLE001
                    force_errors.append(f"IT-рекрутеры сейчас: {exc}")
                try:
                    recruiter_30d, _ = _navigate_and_count(
                        page,
                        build_vacancy_url(force_query, 30),
                        "vacancy",
                        "search_force_recruiters_30d",
                    )
                except Exception as exc:  # noqa: BLE001
                    force_errors.append(f"IT-рекрутеры за 30 дней: {exc}")

                try:
                    recruiter_resume_active, _ = _navigate_and_count(
                        page,
                        build_resume_url(force_query, None),
                        "resume",
                        "search_force_recruiter_resumes_active",
                    )
                except Exception as exc:  # noqa: BLE001
                    force_errors.append(f"Резюме IT-рекрутеров сейчас: {exc}")
                try:
                    recruiter_resume_30d, _ = _navigate_and_count(
                        page,
                        build_resume_url(force_query, 30),
                        "resume",
                        "search_force_recruiter_resumes_30d",
                    )
                except Exception as exc:  # noqa: BLE001
                    force_errors.append(f"Резюме IT-рекрутеров за 30 дней: {exc}")

                active_values = [
                    item["vacancy_active_now"] for item in rows
                    if item.get("vacancy_active_now") is not None
                ]
                flow_values = [
                    item["vacancy_published_30d"] for item in rows
                    if item.get("vacancy_published_30d") is not None
                ]
                target_active = sum(active_values) if active_values else None
                target_30d = sum(flow_values) if flow_values else None
                force_model = calculate_search_force_model(
                    recruiter_active, target_active, force_capacity
                )
                force_row = {
                    "query_id": force_query.query_id,
                    "label": force_query.label,
                    "query_text": force_query.text,
                    "area": force_query.area,
                    "recruiter_vacancy_active_now": recruiter_active,
                    "recruiter_vacancy_published_30d": recruiter_30d,
                    "recruiter_resume_active_now": recruiter_resume_active,
                    "recruiter_resume_published_30d": recruiter_resume_30d,
                    "recruiter_competition_stock": ratio(
                        recruiter_resume_active, recruiter_active
                    ),
                    "recruiter_competition_flow": ratio(
                        recruiter_resume_30d, recruiter_30d
                    ),
                    "target_vacancy_active_total": target_active,
                    "target_vacancy_published_30d_total": target_30d,
                    "recruiting_gap_stock": ratio(recruiter_active, target_active),
                    "recruiting_gap_flow": ratio(recruiter_30d, target_30d),
                    **force_model,
                    "force_error": "; ".join(force_errors) or None,
                }
                save_search_force_snapshot(conn, collected_at, force_row)
                recalculate_search_force_history(conn, force_capacity)
                if (
                    recruiter_active is None
                    and recruiter_30d is None
                    and recruiter_resume_active is None
                    and recruiter_resume_30d is None
                ):
                    force_failed = 1
                if force_errors:
                    errors.append(
                        {
                            "query_id": force_query.query_id,
                            "label": "Proxy-индекс поддержки поиска",
                            "error": "; ".join(force_errors),
                        }
                    )

            export_csv(conn, csv_path)
        finally:
            conn.close()
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass

    result = {
        "collected_at": collected_at,
        "total": total,
        "completed": len(rows) - failed + (1 if force_query and not force_failed else 0),
        "failed": failed + force_failed,
        "errors": errors,
        "rows": rows,
        "search_force": force_row,
        "source": "browser",
        "browser": channel,
    }
    if progress:
        progress({"phase": "finished", **result})
    return result


def clear_browser_profile() -> None:
    import shutil

    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description="HH browser collector")
    parser.add_argument("--login", action="store_true", help="Открыть Chrome для ручного входа")
    parser.add_argument("--check", action="store_true", help="Проверить браузерную сессию")
    parser.add_argument("--collect", action="store_true", help="Собрать данные через браузер")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()

    if args.login:
        open_login_browser()
        return 0
    if args.check:
        print(json.dumps(check_browser_session(), ensure_ascii=False, indent=2))
        return 0
    if args.collect:
        print(
            json.dumps(
                collect_all_browser(args.config, args.db, args.csv),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
