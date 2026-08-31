#!/usr/bin/env python3
"""Сбор агрегированных показателей рынка труда с HeadHunter API.

Сохраняются только количества, без персональных данных резюме.
Для поиска резюме нужен OAuth-токен пользователя-работодателя и доступ к методу API.
Модуль используется как из командной строки, так и локальной веб-панелью.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_BASE = "https://api.hh.ru"
DEFAULT_DB = "hh_market.sqlite3"
DEFAULT_CONFIG = "searches.json"
ProgressCallback = Callable[[dict[str, Any]], None]
MODEL_VERSION = "proxy-support-v2"
DEFAULT_VACANCIES_PER_RECRUITER = 25
PRIMARY_CAPACITY_SCENARIOS = (20, 25, 40)
MIN_VACANCIES_PER_RECRUITER = 4
MAX_VACANCIES_PER_RECRUITER = 300
CONTACT_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PLACEHOLDER_CONTACTS = {
    "",
    "your-email@example.com",
    "ваша-почта@example.com",
    "email@example.com",
}


@dataclass(frozen=True)
class SearchQuery:
    query_id: str
    label: str
    text: str
    area: str = "1"  # Москва


class HHAPIError(RuntimeError):
    """Ошибка обращения к HeadHunter API с сохранением деталей ответа."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        error_value: str | None = None,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.error_value = error_value
        self.response_text = response_text

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": str(self),
            "status_code": self.status_code,
            "type": self.error_type,
            "value": self.error_value,
            "response_text": self.response_text,
        }


def load_queries(path: Path) -> list[SearchQuery]:
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)

    if not isinstance(raw, dict) or not isinstance(raw.get("queries"), list):
        raise ValueError(f"В {path} ожидается объект с массивом queries")

    result: list[SearchQuery] = []
    for item in raw["queries"]:
        result.append(
            SearchQuery(
                query_id=str(item["id"]),
                label=str(item["label"]),
                text=str(item["text"]),
                area=str(item.get("area", "1")),
            )
        )
    return result


def load_search_force_query(path: Path) -> SearchQuery | None:
    """Загрузить единый запрос вакансий IT-рекрутеров для квазииндекса.

    Этот запрос не относится к отдельной IT-позиции: он измеряется один раз за
    сбор и используется как общий прокси дефицита поисковой мощности рынка.
    """

    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    item = raw.get("search_force") if isinstance(raw, dict) else None
    if not isinstance(item, dict):
        return None
    return SearchQuery(
        query_id=str(item.get("id", "it_recruiting_power")),
        label=str(item.get("label", "IT-рекрутеры")),
        text=str(item["text"]),
        area=str(item.get("area", "1")),
    )


def normalize_recruiter_capacity(value: Any) -> int:
    """Нормализовать сценарную одновременную нагрузку рекрутера."""

    try:
        capacity = int(value)
    except (TypeError, ValueError):
        capacity = DEFAULT_VACANCIES_PER_RECRUITER
    return min(MAX_VACANCIES_PER_RECRUITER, max(MIN_VACANCIES_PER_RECRUITER, capacity))


def load_search_force_capacity(path: Path) -> int:
    """Загрузить сценарную норму одновременной нагрузки рекрутера."""

    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    item = raw.get("search_force") if isinstance(raw, dict) else None
    value = item.get("vacancies_per_recruiter") if isinstance(item, dict) else None
    return normalize_recruiter_capacity(value)


def get_app_contact() -> str:
    return os.getenv("HH_APP_CONTACT", "").strip()


def validate_app_contact(contact: str | None = None) -> str:
    """Проверить контакт, который HH требует в User-Agent."""

    value = (contact if contact is not None else get_app_contact()).strip()
    if value.lower() in PLACEHOLDER_CONTACTS or not CONTACT_RE.fullmatch(value):
        raise HHAPIError(
            "Не настроен корректный HH_APP_CONTACT. Укажите настоящую контактную "
            "почту в настройках панели или в файле .env. Это не логин и не пароль."
        )
    return value


def build_headers(token: str | None = None) -> dict[str, str]:
    contact = validate_app_contact()
    # HH обрабатывает HH-User-Agent при наличии обоих заголовков. Значение делаем
    # коротким, ASCII-совместимым и с реальным контактом разработчика.
    app_id = os.getenv("HH_APP_NAME", "analitikhhru").strip() or "analitikhhru"
    user_agent = f"{app_id}/0.3 ({contact})"
    headers = {
        "Accept": "application/json",
        "User-Agent": user_agent,
        "HH-User-Agent": user_agent,
    }
    if token:
        headers["Authorization"] = f"Bearer {token.strip()}"
    return headers


def _response_error(response: requests.Response) -> HHAPIError:
    error_type: str | None = None
    error_value: str | None = None
    description: str | None = None
    response_text = response.text[:2000] if response.text else None

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            first = errors[0]
            error_type = str(first.get("type")) if first.get("type") is not None else None
            error_value = str(first.get("value")) if first.get("value") is not None else None
            description = (
                str(first.get("description")) if first.get("description") is not None else None
            )
        elif payload.get("error"):
            error_type = str(payload.get("error"))
            description = str(payload.get("error_description") or "") or None

    details = ": ".join(part for part in (error_type, error_value, description) if part)
    suffix = f" ({details})" if details else ""

    hints: dict[tuple[str | None, str | None], str] = {
        ("bad_user_agent", "unset"): (
            "HH не увидел заголовок User-Agent. Проверьте HH_APP_CONTACT и обновите проект."
        ),
        ("bad_user_agent", "blacklisted"): (
            "HH отклонил User-Agent. Укажите настоящую контактную почту, а не шаблон example.com."
        ),
        ("oauth", "bad_authorization"): "OAuth-токен неверный или уже отозван.",
        ("oauth", "token_expired"): "OAuth-токен истёк; выполните вход через HH заново.",
        ("oauth", "user_auth_expected"): "Нужен токен пользователя, а не токен приложения.",
        ("api_access_payment", "action_must_be_payed"): (
            "Для этого метода у аккаунта работодателя нет оплаченного доступа."
        ),
    }
    hint = hints.get((error_type, error_value))
    message = f"HH API вернул HTTP {response.status_code}{suffix}."
    if hint:
        message += f" {hint}"
    elif response.status_code == 403:
        message += " Проверьте тип аккаунта, OAuth-токен и права работодателя."
    elif response.status_code == 400:
        message += " Проверьте параметры запроса и настройку User-Agent."

    return HHAPIError(
        message,
        status_code=response.status_code,
        error_type=error_type,
        error_value=error_value,
        response_text=response_text,
    )


def hh_get(
    path: str,
    params: dict[str, Any] | None = None,
    token: str | None = None,
    retries: int = 4,
) -> dict[str, Any]:
    """GET-запрос к HH с повтором только временных сетевых ошибок."""

    url = f"{API_BASE}{path}"
    headers = build_headers(token)
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                params=params or {},
                headers=headers,
                timeout=(12, 45),
            )

            if response.status_code == 429:
                last_error = _response_error(response)
                if attempt + 1 < retries:
                    retry_after = response.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and retry_after.isdigit() else 2**attempt * 5
                    time.sleep(min(60, delay))
                    continue
                raise last_error

            if response.status_code >= 500:
                last_error = _response_error(response)
                if attempt + 1 < retries:
                    time.sleep(min(30, 2**attempt * 2))
                    continue
                raise last_error

            if response.status_code >= 400:
                # 400/401/403 не исправятся повтором того же запроса.
                raise _response_error(response)

            payload = response.json()
            if not isinstance(payload, dict):
                raise HHAPIError("Неожиданный формат ответа HH API")
            return payload

        except HHAPIError:
            raise
        except requests.Timeout as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(15, 2**attempt * 2))
                continue
        except requests.ConnectionError as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(15, 2**attempt * 2))
                continue
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            break

    raise HHAPIError(
        "Не удалось соединиться с api.hh.ru после повторных попыток: "
        f"{last_error}. Проверьте интернет, DNS, VPN/прокси и антивирус."
    )


def check_api_connection(token: str | None = None) -> dict[str, Any]:
    """Диагностика публичного API, токена и доступа к поиску резюме."""

    checks: list[dict[str, Any]] = []

    def run_check(name: str, callback: Callable[[], dict[str, Any]]) -> None:
        try:
            payload = callback()
            checks.append({"name": name, "ok": True, "details": payload})
        except HHAPIError as exc:
            checks.append({"name": name, "ok": False, "error": exc.as_dict()})
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": name, "ok": False, "error": {"message": str(exc)}})

    run_check(
        "public_vacancies",
        lambda: {
            "found": hh_get(
                "/vacancies",
                params={"text": "DevOps", "area": "1", "per_page": 1, "page": 0},
            ).get("found")
        },
    )

    if token:
        run_check(
            "oauth_me",
            lambda: _me_summary(hh_get("/me", token=token)),
        )
        run_check(
            "resume_search",
            lambda: {
                "found": hh_get(
                    "/resumes",
                    params={"text": "DevOps", "area": "1", "per_page": 1, "page": 0},
                    token=token,
                ).get("found")
            },
        )
    else:
        checks.append(
            {
                "name": "oauth_me",
                "ok": False,
                "skipped": True,
                "error": {"message": "HH_ACCESS_TOKEN не задан"},
            }
        )
        checks.append(
            {
                "name": "resume_search",
                "ok": False,
                "skipped": True,
                "error": {"message": "Без OAuth-токена поиск резюме не выполняется"},
            }
        )

    return {
        "ok": all(item.get("ok") or item.get("skipped") for item in checks),
        "contact": get_app_contact(),
        "token_configured": bool(token),
        "checks": checks,
    }


def _me_summary(payload: dict[str, Any]) -> dict[str, Any]:
    employer = payload.get("employer")
    return {
        "id": payload.get("id"),
        "first_name": payload.get("first_name"),
        "last_name": payload.get("last_name"),
        "email": payload.get("email"),
        "is_employer": isinstance(employer, dict),
        "employer": {
            "id": employer.get("id"),
            "name": employer.get("name"),
        }
        if isinstance(employer, dict)
        else None,
    }


def get_found(
    endpoint: str,
    query: SearchQuery,
    period_days: int | None,
    token: str | None = None,
    active_resume_only: bool = False,
) -> int:
    params: dict[str, Any] = {
        "text": query.text,
        "area": query.area,
        "per_page": 1,
        "page": 0,
    }
    if period_days is not None:
        params["period"] = period_days
    if endpoint == "/resumes" and active_resume_only:
        params["job_search_status"] = "active_search"

    payload = hh_get(endpoint, params=params, token=token)
    found = payload.get("found")
    if not isinstance(found, int):
        raise HHAPIError(f"В ответе {endpoint} отсутствует целочисленное поле found")
    return found


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_snapshot (
            collected_at TEXT NOT NULL,
            query_id TEXT NOT NULL,
            label TEXT NOT NULL,
            query_text TEXT NOT NULL,
            area TEXT NOT NULL,
            vacancy_active_now INTEGER,
            vacancy_published_30d INTEGER,
            resume_active_now INTEGER,
            resume_active_published_30d INTEGER,
            competition_stock REAL,
            competition_flow REAL,
            resume_error TEXT,
            PRIMARY KEY (collected_at, query_id, area)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_force_snapshot (
            collected_at TEXT NOT NULL,
            query_id TEXT NOT NULL,
            label TEXT NOT NULL,
            query_text TEXT NOT NULL,
            area TEXT NOT NULL,
            recruiter_vacancy_active_now INTEGER,
            recruiter_vacancy_published_30d INTEGER,
            recruiter_resume_active_now INTEGER,
            recruiter_resume_published_30d INTEGER,
            recruiter_competition_stock REAL,
            recruiter_competition_flow REAL,
            target_vacancy_active_total INTEGER,
            target_vacancy_published_30d_total INTEGER,
            recruiting_gap_stock REAL,
            recruiting_gap_flow REAL,
            model_version TEXT,
            hr_vacancy_density REAL,
            observed_pressure_percentile REAL,
            observed_support_score REAL,
            vacancies_per_recruiter INTEGER,
            required_recruiters_estimate REAL,
            required_recruiters_min REAL,
            required_recruiters_base REAL,
            required_recruiters_max REAL,
            recruiting_pressure REAL,
            proxy_deficit_index REAL,
            proxy_support_index REAL,
            proxy_support_index_low REAL,
            proxy_support_index_base REAL,
            proxy_support_index_high REAL,
            search_resource_deficit_index REAL,
            search_force_index REAL,
            force_error TEXT,
            PRIMARY KEY (collected_at, query_id, area)
        )
        """
    )

    # Миграция существующей локальной базы: HR-резюме и HR-конкуренция
    # появились в версии 1.7.0. Старые строки сохраняются, новые поля будут NULL.
    force_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(search_force_snapshot)")
    }
    force_migrations = {
        "recruiter_resume_active_now": "INTEGER",
        "recruiter_resume_published_30d": "INTEGER",
        "recruiter_competition_stock": "REAL",
        "recruiter_competition_flow": "REAL",
        "model_version": "TEXT",
        "hr_vacancy_density": "REAL",
        "observed_pressure_percentile": "REAL",
        "observed_support_score": "REAL",
        "vacancies_per_recruiter": "INTEGER",
        "required_recruiters_estimate": "REAL",
        "required_recruiters_min": "REAL",
        "required_recruiters_base": "REAL",
        "required_recruiters_max": "REAL",
        "recruiting_pressure": "REAL",
        "proxy_deficit_index": "REAL",
        "proxy_support_index": "REAL",
        "proxy_support_index_low": "REAL",
        "proxy_support_index_base": "REAL",
        "proxy_support_index_high": "REAL",
        "search_resource_deficit_index": "REAL",
    }
    for column, declaration in force_migrations.items():
        if column not in force_columns:
            conn.execute(
                f"ALTER TABLE search_force_snapshot ADD COLUMN {column} {declaration}"
            )

    conn.commit()


def ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _proxy_support_for_capacity(
    recruiter_vacancies: int,
    target_vacancies: int,
    capacity: int,
) -> tuple[float, float, float]:
    """Вернуть required, pressure и proxy support score для одного сценария K."""

    required = target_vacancies / capacity
    pressure = recruiter_vacancies / required
    support = 100.0 / (1.0 + pressure)
    return required, pressure, support


def calculate_search_force_model(
    recruiter_vacancies: int | None,
    target_vacancies: int | None,
    vacancies_per_recruiter: int = DEFAULT_VACANCIES_PER_RECRUITER,
) -> dict[str, float | int | str | None]:
    """Рассчитать наблюдаемые и сценарные метрики recruiting-support.

    Открытые вакансии рекрутеров — это posted demand на recruiting-ресурс,
    а не число уже работающих рекрутеров. Поэтому здесь рассчитывается не
    фактическая мощность поиска, а proxy-модель давления незакрытой потребности.

    Наблюдаемая метрика:
        HR density = 1000 * HR_open / IT_open

    Сценарная proxy-модель:
        required(K) = IT_open / K
        pressure(K) = HR_open / required(K)
        support(K) = 100 / (1 + pressure(K))
        deficit(K) = 100 - support(K)

    Истинный capacity-index требует W — числа реально работающих рекрутеров,
    которого открытые данные HH не дают.
    """

    capacity = normalize_recruiter_capacity(vacancies_per_recruiter)
    empty: dict[str, float | int | str | None] = {
        "model_version": MODEL_VERSION,
        "hr_vacancy_density": None,
        "observed_pressure_percentile": None,
        "observed_support_score": None,
        "vacancies_per_recruiter": capacity,
        "required_recruiters_estimate": None,
        "required_recruiters_min": None,
        "required_recruiters_base": None,
        "required_recruiters_max": None,
        "recruiting_pressure": None,
        "proxy_deficit_index": None,
        "proxy_support_index": None,
        "proxy_support_index_low": None,
        "proxy_support_index_base": None,
        "proxy_support_index_high": None,
        # Legacy aliases retained for old databases, CSV consumers and UI clients.
        "search_resource_deficit_index": None,
        "search_force_index": None,
    }
    if (
        recruiter_vacancies is None
        or target_vacancies is None
        or recruiter_vacancies < 0
        or target_vacancies <= 0
    ):
        return empty

    required, pressure, support = _proxy_support_for_capacity(
        recruiter_vacancies, target_vacancies, capacity
    )
    scenario_values: dict[int, tuple[float, float, float]] = {
        scenario: _proxy_support_for_capacity(
            recruiter_vacancies, target_vacancies, scenario
        )
        for scenario in PRIMARY_CAPACITY_SCENARIOS
    }
    required_20 = scenario_values[20][0]
    required_25 = scenario_values[25][0]
    required_40 = scenario_values[40][0]
    support_values = [scenario_values[item][2] for item in PRIMARY_CAPACITY_SCENARIOS]
    support_low = min(support_values)
    support_high = max(support_values)
    support_base = scenario_values[25][2]
    deficit = 100.0 - support

    return {
        "model_version": MODEL_VERSION,
        "hr_vacancy_density": round(1000.0 * recruiter_vacancies / target_vacancies, 4),
        "observed_pressure_percentile": None,
        "observed_support_score": None,
        "vacancies_per_recruiter": capacity,
        "required_recruiters_estimate": round(required, 2),
        # Range is ordered from minimum to maximum required headcount.
        "required_recruiters_min": round(required_40, 2),
        "required_recruiters_base": round(required_25, 2),
        "required_recruiters_max": round(required_20, 2),
        "recruiting_pressure": round(pressure, 4),
        "proxy_deficit_index": round(deficit, 2),
        "proxy_support_index": round(support, 2),
        "proxy_support_index_low": round(support_low, 2),
        "proxy_support_index_base": round(support_base, 2),
        "proxy_support_index_high": round(support_high, 2),
        # Legacy aliases: same values, but UI must label them as proxy/model.
        "search_resource_deficit_index": round(deficit, 2),
        "search_force_index": round(support, 2),
    }


def calculate_search_force_index(
    recruiter_vacancies: int | None,
    target_vacancies: int | None,
    vacancies_per_recruiter: int = DEFAULT_VACANCIES_PER_RECRUITER,
) -> float | None:
    return calculate_search_force_model(
        recruiter_vacancies, target_vacancies, vacancies_per_recruiter
    )["proxy_support_index"]  # type: ignore[return-value]


def recalculate_search_force_history(
    conn: sqlite3.Connection,
    vacancies_per_recruiter: int = DEFAULT_VACANCIES_PER_RECRUITER,
) -> None:
    """Пересчитать производные метрики, не изменяя сырые исторические counts."""

    capacity = normalize_recruiter_capacity(vacancies_per_recruiter)
    rows = conn.execute(
        """
        SELECT collected_at, query_id, area, recruiter_vacancy_active_now,
               target_vacancy_active_total
        FROM search_force_snapshot
        """
    ).fetchall()
    for row in rows:
        model = calculate_search_force_model(
            row["recruiter_vacancy_active_now"],
            row["target_vacancy_active_total"],
            capacity,
        )
        conn.execute(
            """
            UPDATE search_force_snapshot
            SET model_version = ?, hr_vacancy_density = ?,
                observed_pressure_percentile = NULL, observed_support_score = NULL,
                vacancies_per_recruiter = ?, required_recruiters_estimate = ?,
                required_recruiters_min = ?, required_recruiters_base = ?,
                required_recruiters_max = ?, recruiting_pressure = ?,
                proxy_deficit_index = ?, proxy_support_index = ?,
                proxy_support_index_low = ?, proxy_support_index_base = ?,
                proxy_support_index_high = ?,
                search_resource_deficit_index = ?, search_force_index = ?
            WHERE collected_at = ? AND query_id = ? AND area = ?
            """,
            (
                model["model_version"], model["hr_vacancy_density"],
                model["vacancies_per_recruiter"],
                model["required_recruiters_estimate"],
                model["required_recruiters_min"], model["required_recruiters_base"],
                model["required_recruiters_max"], model["recruiting_pressure"],
                model["proxy_deficit_index"], model["proxy_support_index"],
                model["proxy_support_index_low"], model["proxy_support_index_base"],
                model["proxy_support_index_high"],
                model["search_resource_deficit_index"], model["search_force_index"],
                row["collected_at"], row["query_id"], row["area"],
            ),
        )

    # Исторически нормированный наблюдаемый score. Он использует только HR density
    # и не зависит от сценария K. Для устойчивости нужен минимум из пяти замеров
    # в скользящем окне 365 дней; при меньшей истории значение остаётся NULL.
    density_rows = conn.execute(
        """
        SELECT collected_at, query_id, area, hr_vacancy_density
        FROM search_force_snapshot
        WHERE hr_vacancy_density IS NOT NULL
        ORDER BY collected_at
        """
    ).fetchall()
    parsed: list[tuple[sqlite3.Row, datetime]] = []
    for item in density_rows:
        try:
            parsed.append((item, datetime.fromisoformat(item["collected_at"])))
        except (TypeError, ValueError):
            continue
    for item, moment in parsed:
        window_start = moment - timedelta(days=365)
        values = [
            float(other["hr_vacancy_density"])
            for other, other_moment in parsed
            if other["query_id"] == item["query_id"]
            and other["area"] == item["area"]
            and window_start <= other_moment <= moment
        ]
        if len(values) < 5:
            continue
        current = float(item["hr_vacancy_density"])
        less = sum(value < current for value in values)
        equal = sum(value == current for value in values)
        percentile = 100.0 * (less + 0.5 * equal) / len(values)
        support_score = 100.0 - percentile
        conn.execute(
            """
            UPDATE search_force_snapshot
            SET observed_pressure_percentile = ?, observed_support_score = ?
            WHERE collected_at = ? AND query_id = ? AND area = ?
            """,
            (
                round(percentile, 2), round(support_score, 2),
                item["collected_at"], item["query_id"], item["area"],
            ),
        )
    conn.commit()


def collect_one(query: SearchQuery, token: str | None) -> dict[str, Any]:
    vacancy_active = get_found("/vacancies", query, period_days=None)
    vacancy_30d = get_found("/vacancies", query, period_days=30)

    resume_active: int | None = None
    resume_30d: int | None = None
    resume_error: str | None = None
    if token:
        try:
            resume_active = get_found(
                "/resumes", query, period_days=None, token=token, active_resume_only=True
            )
            resume_30d = get_found(
                "/resumes", query, period_days=30, token=token, active_resume_only=True
            )
        except HHAPIError as exc:
            resume_error = str(exc)
    else:
        resume_error = "HH_ACCESS_TOKEN не задан; сбор резюме пропущен"

    return {
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
        "resume_error": resume_error,
    }


def save_snapshot(conn: sqlite3.Connection, collected_at: str, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO market_snapshot (
            collected_at, query_id, label, query_text, area,
            vacancy_active_now, vacancy_published_30d,
            resume_active_now, resume_active_published_30d,
            competition_stock, competition_flow, resume_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collected_at,
            row["query_id"],
            row["label"],
            row["query_text"],
            row["area"],
            row["vacancy_active_now"],
            row["vacancy_published_30d"],
            row["resume_active_now"],
            row["resume_active_published_30d"],
            row["competition_stock"],
            row["competition_flow"],
            row["resume_error"],
        ),
    )
    conn.commit()


def save_search_force_snapshot(
    conn: sqlite3.Connection,
    collected_at: str,
    row: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO search_force_snapshot (
            collected_at, query_id, label, query_text, area,
            recruiter_vacancy_active_now, recruiter_vacancy_published_30d,
            recruiter_resume_active_now, recruiter_resume_published_30d,
            recruiter_competition_stock, recruiter_competition_flow,
            target_vacancy_active_total, target_vacancy_published_30d_total,
            recruiting_gap_stock, recruiting_gap_flow, model_version,
            hr_vacancy_density, observed_pressure_percentile, observed_support_score,
            vacancies_per_recruiter,
            required_recruiters_estimate, required_recruiters_min,
            required_recruiters_base, required_recruiters_max,
            recruiting_pressure, proxy_deficit_index, proxy_support_index,
            proxy_support_index_low, proxy_support_index_base,
            proxy_support_index_high, search_resource_deficit_index,
            search_force_index, force_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            collected_at, row["query_id"], row["label"], row["query_text"], row["area"],
            row["recruiter_vacancy_active_now"], row["recruiter_vacancy_published_30d"],
            row.get("recruiter_resume_active_now"), row.get("recruiter_resume_published_30d"),
            row.get("recruiter_competition_stock"), row.get("recruiter_competition_flow"),
            row["target_vacancy_active_total"], row["target_vacancy_published_30d_total"],
            row["recruiting_gap_stock"], row["recruiting_gap_flow"],
            row.get("model_version"), row.get("hr_vacancy_density"),
            row.get("observed_pressure_percentile"), row.get("observed_support_score"),
            row.get("vacancies_per_recruiter"), row.get("required_recruiters_estimate"),
            row.get("required_recruiters_min"), row.get("required_recruiters_base"),
            row.get("required_recruiters_max"), row.get("recruiting_pressure"),
            row.get("proxy_deficit_index"), row.get("proxy_support_index"),
            row.get("proxy_support_index_low"), row.get("proxy_support_index_base"),
            row.get("proxy_support_index_high"),
            row.get("search_resource_deficit_index"), row.get("search_force_index"),
            row.get("force_error"),
        ),
    )
    conn.commit()


def _csv_ratio(value: float | None) -> str:
    """Форматировать коэффициент для русской версии Excel."""

    if value is None:
        return ""
    return f"{value:.4f}".replace(".", ",")


def export_csv(conn: sqlite3.Connection, output: Path) -> None:
    """Выгрузить сырьё и производные proxy-метрики в русскоязычный CSV."""

    output.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        """
        SELECT m.collected_at, m.query_id, m.label, m.query_text, m.area,
               m.vacancy_active_now, m.vacancy_published_30d,
               m.resume_active_now, m.resume_active_published_30d,
               m.competition_stock, m.competition_flow, m.resume_error,
               f.model_version, f.hr_vacancy_density,
               f.observed_pressure_percentile, f.observed_support_score,
               f.recruiter_vacancy_active_now, f.recruiter_vacancy_published_30d,
               f.recruiter_resume_active_now, f.recruiter_resume_published_30d,
               f.recruiter_competition_stock, f.recruiter_competition_flow,
               f.recruiting_gap_stock, f.recruiting_gap_flow,
               f.vacancies_per_recruiter, f.required_recruiters_estimate,
               f.required_recruiters_min, f.required_recruiters_base,
               f.required_recruiters_max, f.recruiting_pressure,
               f.proxy_deficit_index, f.proxy_support_index,
               f.proxy_support_index_low, f.proxy_support_index_base,
               f.proxy_support_index_high
        FROM market_snapshot AS m
        LEFT JOIN search_force_snapshot AS f ON f.collected_at = m.collected_at
        ORDER BY m.collected_at, m.query_id
        """
    ).fetchall()

    headers = [
        "Дата и время замера", "Код запроса", "Позиция", "Поисковая фраза",
        "Регион HH", "Активные вакансии", "Новые вакансии за 30 дней",
        "Активные резюме", "Новые активные резюме за 30 дней",
        "STOCK: активные резюме / активные вакансии",
        "FLOW: новые резюме / новые вакансии за 30 дней",
        "Версия модели recruiting-support",
        "Плотность HR-вакансий на 1000 IT-вакансий",
        "Наблюдаемый percentile HR-напряжения за 365 дней",
        "Наблюдаемый индекс поддержки (100 − percentile)",
        "Proxy-индекс поддержки поиска (выбранный K, 0–100)",
        "Proxy-индекс дефицита поддержки (выбранный K, 0–100)",
        "Proxy-индекс поддержки: нижняя граница K=20..40",
        "Proxy-индекс поддержки: базовый K=25",
        "Proxy-индекс поддержки: верхняя граница K=20..40",
        "Сценарная норма K: одновременно открытых вакансий на рекрутера",
        "Оценка требуемых рекрутеров при выбранном K",
        "Минимальная оценка рекрутеров (K=40)",
        "Базовая оценка рекрутеров (K=25)",
        "Максимальная оценка рекрутеров (K=20)",
        "Давление posted HR demand / модельная потребность",
        "Активные вакансии IT-рекрутеров",
        "Новые вакансии IT-рекрутеров за 30 дней",
        "Активные резюме IT-рекрутеров",
        "Новые активные резюме IT-рекрутеров за 30 дней",
        "HR STOCK: активные резюме / активные вакансии",
        "HR FLOW: новые резюме / новые вакансии за 30 дней",
        "Доля HR-спроса STOCK: вакансии рекрутеров / выбранные IT-вакансии",
        "Доля HR-спроса FLOW: новые вакансии рекрутеров / новые IT-вакансии",
        "Предупреждения сбора",
    ]

    with output.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(headers)
        for row in rows:
            writer.writerow([
                row["collected_at"], row["query_id"], row["label"], row["query_text"],
                row["area"], row["vacancy_active_now"], row["vacancy_published_30d"],
                row["resume_active_now"], row["resume_active_published_30d"],
                _csv_ratio(row["competition_stock"]), _csv_ratio(row["competition_flow"]),
                row["model_version"] or "", _csv_ratio(row["hr_vacancy_density"]),
                _csv_ratio(row["observed_pressure_percentile"]),
                _csv_ratio(row["observed_support_score"]),
                _csv_ratio(row["proxy_support_index"]),
                _csv_ratio(row["proxy_deficit_index"]),
                _csv_ratio(row["proxy_support_index_low"]),
                _csv_ratio(row["proxy_support_index_base"]),
                _csv_ratio(row["proxy_support_index_high"]),
                row["vacancies_per_recruiter"],
                _csv_ratio(row["required_recruiters_estimate"]),
                _csv_ratio(row["required_recruiters_min"]),
                _csv_ratio(row["required_recruiters_base"]),
                _csv_ratio(row["required_recruiters_max"]),
                _csv_ratio(row["recruiting_pressure"]),
                row["recruiter_vacancy_active_now"],
                row["recruiter_vacancy_published_30d"],
                row["recruiter_resume_active_now"],
                row["recruiter_resume_published_30d"],
                _csv_ratio(row["recruiter_competition_stock"]),
                _csv_ratio(row["recruiter_competition_flow"]),
                _csv_ratio(row["recruiting_gap_stock"]),
                _csv_ratio(row["recruiting_gap_flow"]),
                row["resume_error"] or "",
            ])


def collect_all(
    config_path: Path,
    db_path: Path,
    csv_path: Path,
    *,
    token: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Собрать все запросы и вернуть сводку выполнения."""

    # Предварительная проверка предотвращает шесть одинаковых ошибок при неверном
    # User-Agent или недоступности api.hh.ru.
    validate_app_contact()
    hh_get("/areas/1", retries=3)
    queries = load_queries(config_path)
    token = token if token is not None else os.getenv("HH_ACCESS_TOKEN")
    token = token.strip() if token else None
    collected_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    conn = connect_db(db_path)
    init_db(conn)

    failed = 0
    errors: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    total = len(queries)

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
                    }
                )
            try:
                row = collect_one(query, token)
                save_snapshot(conn, collected_at, row)
                rows.append(row)
            except HHAPIError as exc:
                failed += 1
                errors.append(
                    {
                        "query_id": query.query_id,
                        "label": query.label,
                        "error": str(exc),
                        "details": exc.as_dict(),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                errors.append({"query_id": query.query_id, "label": query.label, "error": str(exc)})

        export_csv(conn, csv_path)
    finally:
        conn.close()

    result = {
        "collected_at": collected_at,
        "total": total,
        "completed": len(rows),
        "failed": failed,
        "errors": errors,
        "rows": rows,
    }
    if progress:
        progress({"phase": "finished", **result})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="HH market analytics collector")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="JSON-файл с поисковыми запросами")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite-файл")
    parser.add_argument("--csv", default="hh_market.csv", help="CSV-выгрузка")
    parser.add_argument("--diagnose", action="store_true", help="Проверить API и OAuth без сбора")
    args = parser.parse_args()

    if args.diagnose:
        print(
            json.dumps(
                check_api_connection(os.getenv("HH_ACCESS_TOKEN") or None),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    result = collect_all(Path(args.config), Path(args.db), Path(args.csv))
    for row in result["rows"]:
        print(json.dumps({"collected_at": result["collected_at"], **row}, ensure_ascii=False))
    for error in result["errors"]:
        print(f"ERROR {error['query_id']}: {error['error']}", file=sys.stderr)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
