#!/usr/bin/env python3
"""Локальная веб-панель HH Market Radar — браузерный режим.

Один процесс обслуживает HTML, JSON API и запуск браузерного сборщика.
Пароли не передаются приложению: пользователь входит в hh.ru вручную в Chrome.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from waitress import serve

from hh_browser_collector import (
    PROFILE_DIR,
    check_browser_session,
    clear_browser_profile,
    collect_all_browser,
    profile_exists,
)
from hh_market_monitor import (
    connect_db,
    export_csv,
    init_db,
    load_queries,
    load_search_force_capacity,
    recalculate_search_force_history,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = BASE_DIR / "searches.json"
DB_PATH = DATA_DIR / "hh_market.sqlite3"
CSV_PATH = DATA_DIR / "hh_market.csv"
ENV_PATH = BASE_DIR / ".env"
ENV_EXAMPLE_PATH = BASE_DIR / ".env.example"


def _ensure_env_file() -> None:
    if ENV_PATH.exists():
        return
    if ENV_EXAMPLE_PATH.exists():
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8-sig"), encoding="utf-8")
    else:
        ENV_PATH.write_text(
            "HH_WEB_HOST=127.0.0.1\n"
            "HH_WEB_PORT=5050\n"
            "HH_OPEN_BROWSER=1\n"
            "HH_BROWSER_CHANNEL=chrome\n",
            encoding="utf-8",
        )


_ensure_env_file()
load_dotenv(ENV_PATH)

app = Flask(__name__)
app.json.ensure_ascii = False

_status_lock = threading.Lock()
_login_lock = threading.Lock()
_login_process: subprocess.Popen[Any] | None = None
_collection_status: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "message": "Готово к сбору через браузер",
    "current": 0,
    "total": 0,
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "errors": [],
    "source": "browser",
}


def _set_status(**values: Any) -> None:
    with _status_lock:
        _collection_status.update(values)


def _get_status() -> dict[str, Any]:
    with _status_lock:
        return dict(_collection_status)


def _login_running() -> bool:
    global _login_process
    with _login_lock:
        if _login_process is None:
            return False
        if _login_process.poll() is None:
            return True
        _login_process = None
        return False


def _db_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        return []
    conn = connect_db(DB_PATH)
    try:
        init_db(conn)
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _dashboard_payload() -> dict[str, Any]:
    queries = load_queries(CONFIG_PATH)
    force_capacity = load_search_force_capacity(CONFIG_PATH)
    if DB_PATH.exists():
        conn = connect_db(DB_PATH)
        try:
            init_db(conn)
            recalculate_search_force_history(conn, force_capacity)
        finally:
            conn.close()
    latest = _db_rows("SELECT MAX(collected_at) AS collected_at FROM market_snapshot")
    latest_at = latest[0]["collected_at"] if latest and latest[0]["collected_at"] else None

    latest_rows: list[dict[str, Any]] = []
    latest_force: dict[str, Any] | None = None
    if latest_at:
        latest_rows = _db_rows(
            """
            SELECT collected_at, query_id, label, query_text, area,
                   vacancy_active_now, vacancy_published_30d,
                   resume_active_now, resume_active_published_30d,
                   competition_stock, competition_flow, resume_error
            FROM market_snapshot
            WHERE collected_at = ?
            ORDER BY label COLLATE NOCASE
            """,
            (latest_at,),
        )
        force_rows = _db_rows(
            """
            SELECT collected_at, query_id, label, query_text, area,
                   recruiter_vacancy_active_now, recruiter_vacancy_published_30d,
                   recruiter_resume_active_now, recruiter_resume_published_30d,
                   recruiter_competition_stock, recruiter_competition_flow,
                   target_vacancy_active_total, target_vacancy_published_30d_total,
                   recruiting_gap_stock, recruiting_gap_flow, model_version,
                   hr_vacancy_density, observed_pressure_percentile,
                   observed_support_score, vacancies_per_recruiter,
                   required_recruiters_estimate, required_recruiters_min,
                   required_recruiters_base, required_recruiters_max,
                   recruiting_pressure, proxy_deficit_index, proxy_support_index,
                   proxy_support_index_low, proxy_support_index_base,
                   proxy_support_index_high, search_resource_deficit_index,
                   search_force_index, force_error
            FROM search_force_snapshot
            WHERE collected_at = ?
            """,
            (latest_at,),
        )
        latest_force = force_rows[0] if force_rows else None

    history = _db_rows(
        """
        SELECT collected_at, query_id, label,
               vacancy_active_now, vacancy_published_30d,
               resume_active_now, resume_active_published_30d,
               competition_stock, competition_flow
        FROM market_snapshot
        ORDER BY collected_at, label COLLATE NOCASE
        """
    )
    search_force_history = _db_rows(
        """
        SELECT collected_at, recruiter_vacancy_active_now,
               recruiter_vacancy_published_30d,
               recruiter_resume_active_now, recruiter_resume_published_30d,
               recruiter_competition_stock, recruiter_competition_flow,
               target_vacancy_active_total, target_vacancy_published_30d_total,
               recruiting_gap_stock, recruiting_gap_flow, model_version,
               hr_vacancy_density, observed_pressure_percentile,
               observed_support_score, vacancies_per_recruiter,
               required_recruiters_estimate, required_recruiters_min,
               required_recruiters_base, required_recruiters_max,
               recruiting_pressure, proxy_deficit_index, proxy_support_index,
               proxy_support_index_low, proxy_support_index_base,
               proxy_support_index_high, search_resource_deficit_index,
               search_force_index
        FROM search_force_snapshot
        ORDER BY collected_at
        """
    )

    def total(field: str) -> int | None:
        values = [row[field] for row in latest_rows if row.get(field) is not None]
        return sum(values) if values else None

    stock_ratios = [
        row["competition_stock"]
        for row in latest_rows
        if row.get("competition_stock") is not None
    ]
    flow_ratios = [
        row["competition_flow"]
        for row in latest_rows
        if row.get("competition_flow") is not None
    ]
    summary = {
        "vacancy_active_now": total("vacancy_active_now"),
        "vacancy_published_30d": total("vacancy_published_30d"),
        "resume_active_now": total("resume_active_now"),
        "resume_active_published_30d": total("resume_active_published_30d"),
        "competition_stock_average": (
            round(sum(stock_ratios) / len(stock_ratios), 2) if stock_ratios else None
        ),
        "competition_flow_average": (
            round(sum(flow_ratios) / len(flow_ratios), 2) if flow_ratios else None
        ),
        "proxy_support_index": latest_force.get("proxy_support_index") if latest_force else None,
        "proxy_deficit_index": latest_force.get("proxy_deficit_index") if latest_force else None,
        "hr_vacancy_density": latest_force.get("hr_vacancy_density") if latest_force else None,
        "observed_pressure_percentile": latest_force.get("observed_pressure_percentile") if latest_force else None,
        "observed_support_score": latest_force.get("observed_support_score") if latest_force else None,
        "search_force_index": latest_force.get("proxy_support_index") if latest_force else None,
        "search_resource_deficit_index": latest_force.get("proxy_deficit_index") if latest_force else None,
        "true_search_force_index": None,
        "positions": len(latest_rows),
    }

    return {
        "latest_at": latest_at,
        "browser_profile_configured": profile_exists(),
        "login_browser_running": _login_running(),
        "profile_dir": str(PROFILE_DIR),
        "summary": summary,
        "latest": latest_rows,
        "history": history,
        "search_force": latest_force,
        "search_force_history": search_force_history,
        "search_force_capacity": force_capacity,
        "queries": [
            {"id": item.query_id, "label": item.label, "text": item.text, "area": item.area}
            for item in queries
        ],
        "status": _get_status(),
    }


def _collection_worker() -> None:
    def progress(event: dict[str, Any]) -> None:
        phase = event.get("phase")
        if phase == "collecting":
            _set_status(
                phase="collecting",
                current=event["current"],
                total=event["total"],
                message=f"Chrome собирает: {event['label']}",
            )
        elif phase == "finished":
            _set_status(
                phase="finished",
                current=event["total"],
                total=event["total"],
                message=(
                    f"Сбор завершён: {event['completed']} шагов обработано, "
                    f"полных ошибок — {event['failed']}"
                ),
                errors=event["errors"],
            )

    try:
        result = collect_all_browser(CONFIG_PATH, DB_PATH, CSV_PATH, progress=progress)
        _set_status(
            running=False,
            finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            last_error=None,
            errors=result["errors"],
        )
    except Exception as exc:  # noqa: BLE001
        _set_status(
            running=False,
            phase="error",
            message="Сбор через браузер не завершён",
            finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            last_error=str(exc),
        )


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/api/dashboard")
def dashboard() -> Any:
    return jsonify(_dashboard_payload())


@app.get("/api/status")
def status() -> Any:
    payload = _get_status()
    payload["login_browser_running"] = _login_running()
    payload["browser_profile_configured"] = profile_exists()
    return jsonify(payload)


@app.post("/api/browser/open-login")
def open_login() -> Any:
    global _login_process
    with _login_lock:
        if _login_process is not None and _login_process.poll() is None:
            return jsonify({"ok": True, "running": True, "message": "Окно Chrome уже открыто"})

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        _login_process = subprocess.Popen(
            [sys.executable, str(BASE_DIR / "hh_browser_collector.py"), "--login"],
            cwd=str(BASE_DIR),
            creationflags=creationflags,
        )
    return jsonify(
        {
            "ok": True,
            "running": True,
            "message": "Chrome открыт. Войдите в HH вручную и затем полностью закройте окно Chrome.",
        }
    )


@app.post("/api/browser/check")
def browser_check() -> Any:
    if _login_running():
        return jsonify({"ok": False, "error": "Сначала завершите вход и закройте окно Chrome."}), 409
    try:
        result = check_browser_session()
        return jsonify(result), 200 if result.get("ok") else 207
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc), "checks": []}), 400


@app.delete("/api/browser/profile")
def delete_browser_profile() -> Any:
    if _login_running() or _get_status().get("running"):
        return jsonify({"error": "Сначала закройте Chrome и дождитесь окончания сбора."}), 409
    try:
        clear_browser_profile()
        return jsonify({"ok": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400


@app.post("/api/search-force/capacity")
def update_search_force_capacity() -> Any:
    payload = request.get_json(silent=True) or {}
    try:
        capacity = int(payload.get("vacancies_per_recruiter"))
    except (TypeError, ValueError):
        return jsonify({"error": "Укажите целое число вакансий на одного рекрутера."}), 400
    allowed = {15, 20, 25, 30, 40, 60, 100, 300}
    if capacity not in allowed:
        return jsonify({"error": "Допустимые сценарии K: 15, 20, 25, 30, 40, 60, 100 или 300."}), 400

    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    force = raw.setdefault("search_force", {})
    force["vacancies_per_recruiter"] = capacity
    CONFIG_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    if DB_PATH.exists():
        conn = connect_db(DB_PATH)
        try:
            init_db(conn)
            recalculate_search_force_history(conn, capacity)
        finally:
            conn.close()
    return jsonify({"ok": True, "vacancies_per_recruiter": capacity})


@app.post("/api/collect")
def collect() -> Any:
    if _login_running():
        return jsonify({"error": "Закройте окно Chrome после входа, затем запустите сбор."}), 409

    with _status_lock:
        if _collection_status["running"]:
            return jsonify(dict(_collection_status)), 409
        _collection_status.update(
            {
                "running": True,
                "phase": "starting",
                "message": "Открываю Chrome и подготавливаю поиск",
                "current": 0,
                "total": len(load_queries(CONFIG_PATH)) + 1,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "finished_at": None,
                "last_error": None,
                "errors": [],
                "source": "browser",
            }
        )

    thread = threading.Thread(target=_collection_worker, name="hh-browser-collector", daemon=True)
    thread.start()
    return jsonify(_get_status()), 202


@app.get("/api/export.csv")
def export_csv_route() -> Any:
    if not DB_PATH.exists():
        return jsonify({"error": "История ещё не создана. Сначала выполните сбор."}), 404

    # Пересобираем CSV при каждом скачивании: старые записи из SQLite сразу
    # получают новые русские заголовки и актуальные формулы STOCK/FLOW.
    conn = connect_db(DB_PATH)
    try:
        init_db(conn)
        recalculate_search_force_history(conn, load_search_force_capacity(CONFIG_PATH))
        count = conn.execute("SELECT COUNT(*) FROM market_snapshot").fetchone()[0]
        if not count:
            return jsonify({"error": "История пуста. Сначала выполните сбор."}), 404
        export_csv(conn, CSV_PATH)
    finally:
        conn.close()

    return send_file(CSV_PATH, as_attachment=True, download_name="hh_market_ru.csv")


@app.get("/api/health")
def health() -> Any:
    return jsonify({"ok": True, "mode": "browser"})


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect_db(DB_PATH)
    init_db(conn)
    recalculate_search_force_history(conn, load_search_force_capacity(CONFIG_PATH))
    conn.close()

    host = os.getenv("HH_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("HH_WEB_PORT", "5050"))
    url = f"http://{host}:{port}"
    open_browser = os.getenv("HH_OPEN_BROWSER", "1").strip().lower() not in {"0", "false", "no"}

    print("=" * 72)
    print(" HH Market Radar 1.9 — proxy-support model")
    print(f" Панель: {url}")
    print(" Пароль HH вводится только в открытом браузере hh.ru")
    print(" Остановить сервер: Ctrl+C")
    print("=" * 72)

    if open_browser:
        threading.Timer(1.1, lambda: webbrowser.open(url)).start()

    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    main()
