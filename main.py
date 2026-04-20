import json
import os
import re
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


DB_PATH = "moderation.db"
BANNED_WORDS = [
    "дурак",
    "идиот",
    "тупой",
    "глупый",
    "бездарь",
    "урод",
    "дебил",
    "лох",
    "придурок",
    "кретин",
    "сволочь",
]
RULE_NAMES = ["banned_words", "links", "repetitions", "length"]
VALID_STATUSES = {"approved", "rejected", "manual_review"}

app = FastAPI(title="Text Moderation Service")

# Один connection на приложение + lock для потокобезопасной работы с SQLite.
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
db_lock = threading.Lock()


def init_db() -> None:
    with db_lock:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS moderation_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                approach TEXT NOT NULL,
                status TEXT NOT NULL,
                reasons TEXT NOT NULL,
                processing_time_ms REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rule_settings (
                rule_name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL,
                priority INTEGER NOT NULL
            )
            """
        )
        conn.commit()

    # Значения по умолчанию для OOP-правил.
    defaults = {
        "banned_words": (1, 1),
        "links": (1, 1),
        "repetitions": (1, 2),
        "length": (1, 2),
    }
    with db_lock:
        for rule_name, (enabled, priority) in defaults.items():
            conn.execute(
                """
                INSERT OR IGNORE INTO rule_settings (rule_name, enabled, priority)
                VALUES (?, ?, ?)
                """,
                (rule_name, enabled, priority),
            )
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


# -----------------------------
# Процедурный подход
# -----------------------------
def check_banned_words(text: str) -> Tuple[bool, Optional[str]]:
    lower_text = text.lower()
    for word in BANNED_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lower_text):
            return True, f"Обнаружено запрещенное слово: {word}"
    return False, None


def check_links(text: str) -> Tuple[bool, Optional[str]]:
    lower_text = text.lower()
    if "http://" in lower_text or "https://" in lower_text or "www." in lower_text:
        return True, "Обнаружена ссылка в тексте"
    return False, None


def check_repetitions(text: str) -> Tuple[bool, Optional[str]]:
    if re.search(r"(.)\1{2,}", text):
        return True, "Обнаружены повторения символов (3+ подряд)"
    return False, None


def check_length(text: str) -> Tuple[bool, Optional[str]]:
    length = len(text)
    if length < 10:
        return True, "Текст слишком короткий (меньше 10 символов)"
    if length > 500:
        return True, "Текст слишком длинный (больше 500 символов)"
    return False, None


def moderate_procedural(text: str, enabled_rules: Optional[List[str]] = None) -> Tuple[str, List[str]]:
    enabled_set = set(enabled_rules or RULE_NAMES)
    reasons: List[str] = []

    # Приоритет 1: быстрый ранний выход в rejected.
    for check_fn in (check_banned_words, check_links):
        rule_name = "banned_words" if check_fn == check_banned_words else "links"
        if rule_name not in enabled_set:
            continue
        matched, reason = check_fn(text)
        if matched and reason:
            return "rejected", [reason]

    # Приоритет 2: ручная модерация.
    if "repetitions" in enabled_set:
        matched, reason = check_repetitions(text)
        if matched and reason:
            reasons.append(reason)
    if "length" in enabled_set:
        matched, reason = check_length(text)
        if matched and reason:
            reasons.append(reason)

    if reasons:
        return "manual_review", reasons
    return "approved", ["Нарушений не обнаружено"]


# -----------------------------
# OOP-подход
# -----------------------------
class Rule(ABC):
    def __init__(self, name: str, priority: int) -> None:
        self.name = name
        self.priority = priority

    @abstractmethod
    def check(self, text: str) -> Optional[str]:
        pass

    def get_priority(self) -> int:
        return self.priority


class BannedWordsRule(Rule):
    def __init__(self, priority: int = 1) -> None:
        super().__init__("banned_words", priority)

    def check(self, text: str) -> Optional[str]:
        matched, reason = check_banned_words(text)
        return reason if matched else None


class LinksRule(Rule):
    def __init__(self, priority: int = 1) -> None:
        super().__init__("links", priority)

    def check(self, text: str) -> Optional[str]:
        matched, reason = check_links(text)
        return reason if matched else None


class RepetitionsRule(Rule):
    def __init__(self, priority: int = 2) -> None:
        super().__init__("repetitions", priority)

    def check(self, text: str) -> Optional[str]:
        matched, reason = check_repetitions(text)
        return reason if matched else None


class LengthRule(Rule):
    def __init__(self, priority: int = 2) -> None:
        super().__init__("length", priority)

    def check(self, text: str) -> Optional[str]:
        matched, reason = check_length(text)
        return reason if matched else None


def get_rule_settings_map() -> Dict[str, Dict[str, Any]]:
    with db_lock:
        rows = conn.execute(
            "SELECT rule_name, enabled, priority FROM rule_settings ORDER BY rule_name"
        ).fetchall()
    return {
        row["rule_name"]: {"enabled": bool(row["enabled"]), "priority": row["priority"]}
        for row in rows
    }


class Moderator:
    def __init__(self) -> None:
        self.rules: List[Rule] = []

    def add_rule(self, rule: Rule) -> None:
        self.rules.append(rule)

    def moderate(self, text: str, enabled_rules: Optional[List[str]] = None) -> Tuple[str, List[str]]:
        reasons: List[str] = []
        settings = get_rule_settings_map()
        enabled_input = set(enabled_rules) if enabled_rules else None

        active_rules: List[Rule] = []
        for rule in self.rules:
            rule_cfg = settings.get(rule.name, {"enabled": True, "priority": rule.get_priority()})
            if not rule_cfg["enabled"]:
                continue
            if enabled_input is not None and rule.name not in enabled_input:
                continue
            rule.priority = int(rule_cfg["priority"])
            active_rules.append(rule)

        active_rules.sort(key=lambda r: r.get_priority())

        for rule in active_rules:
            reason = rule.check(text)
            if not reason:
                continue
            if rule.get_priority() == 1:
                # Ранний выход для критичных правил.
                return "rejected", [reason]
            reasons.append(reason)

        if reasons:
            return "manual_review", reasons
        return "approved", ["Нарушений не обнаружено"]


moderator = Moderator()
moderator.add_rule(BannedWordsRule())
moderator.add_rule(LinksRule())
moderator.add_rule(RepetitionsRule())
moderator.add_rule(LengthRule())


# -----------------------------
# API-модели
# -----------------------------
class ModerateRequest(BaseModel):
    text: str = Field(..., min_length=1)
    enabled_rules: Optional[List[str]] = None


class ModerateResponse(BaseModel):
    id: int
    status: str
    reasons: List[str]
    approach: str
    processing_time_ms: float


class RulePriorityRequest(BaseModel):
    priority: int = Field(..., ge=1, le=10)


def save_check(text: str, approach: str, status: str, reasons: List[str], processing_time_ms: float) -> int:
    created_at = datetime.utcnow().isoformat() + "Z"
    with db_lock:
        cur = conn.execute(
            """
            INSERT INTO moderation_checks (text, approach, status, reasons, processing_time_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (text, approach, status, json.dumps(reasons, ensure_ascii=False), processing_time_ms, created_at),
        )
        conn.commit()
        return int(cur.lastrowid)


def row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "text": row["text"],
        "approach": row["approach"],
        "status": row["status"],
        "reasons": json.loads(row["reasons"]),
        "processing_time_ms": row["processing_time_ms"],
        "created_at": row["created_at"],
    }


@app.post("/moderate", response_model=ModerateResponse)
def moderate_text(
    payload: ModerateRequest,
    approach: str = Query("procedural", pattern="^(procedural|oop)$"),
) -> ModerateResponse:
    start = time.perf_counter()
    enabled_rules = payload.enabled_rules
    if enabled_rules:
        bad = [r for r in enabled_rules if r not in RULE_NAMES]
        if bad:
            raise HTTPException(status_code=400, detail=f"Неизвестные правила: {bad}")

    if approach == "procedural":
        status, reasons = moderate_procedural(payload.text, enabled_rules)
    else:
        status, reasons = moderator.moderate(payload.text, enabled_rules)

    processing_time_ms = round((time.perf_counter() - start) * 1000, 3)
    check_id = save_check(payload.text, approach, status, reasons, processing_time_ms)
    return ModerateResponse(
        id=check_id,
        status=status,
        reasons=reasons,
        approach=approach,
        processing_time_ms=processing_time_ms,
    )


@app.get("/moderation/{check_id}")
def get_moderation(check_id: int) -> Dict[str, Any]:
    with db_lock:
        row = conn.execute("SELECT * FROM moderation_checks WHERE id = ?", (check_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Проверка не найдена")
    return row_to_record(row)


@app.get("/moderation/history")
def get_history(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
) -> Dict[str, Any]:
    offset = (page - 1) * limit
    with db_lock:
        total = conn.execute("SELECT COUNT(*) AS c FROM moderation_checks").fetchone()["c"]
        rows = conn.execute(
            """
            SELECT * FROM moderation_checks
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "items": [row_to_record(r) for r in rows],
    }


@app.get("/admin/checks")
def get_admin_checks(
    status: Optional[str] = Query(None, pattern="^(approved|rejected|manual_review)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
) -> Dict[str, Any]:
    offset = (page - 1) * limit
    params: Tuple[Any, ...]

    if status:
        total_query = "SELECT COUNT(*) AS c FROM moderation_checks WHERE status = ?"
        items_query = """
            SELECT * FROM moderation_checks
            WHERE status = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        params = (status, limit, offset)
        with db_lock:
            total = conn.execute(total_query, (status,)).fetchone()["c"]
            rows = conn.execute(items_query, params).fetchall()
    else:
        with db_lock:
            total = conn.execute("SELECT COUNT(*) AS c FROM moderation_checks").fetchone()["c"]
            rows = conn.execute(
                """
                SELECT * FROM moderation_checks
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

    return {
        "page": page,
        "limit": limit,
        "total": total,
        "items": [row_to_record(r) for r in rows],
    }


@app.get("/stats")
def get_stats() -> Dict[str, Any]:
    with db_lock:
        rows = conn.execute("SELECT status, reasons, processing_time_ms FROM moderation_checks").fetchall()

    total = len(rows)
    approved = 0
    rejected = 0
    manual = 0
    all_reasons: List[str] = []
    total_time = 0.0

    for row in rows:
        status = row["status"]
        if status == "approved":
            approved += 1
        elif status == "rejected":
            rejected += 1
        elif status == "manual_review":
            manual += 1
        all_reasons.extend(json.loads(row["reasons"]))
        total_time += float(row["processing_time_ms"])

    avg_time = round(total_time / total, 3) if total else 0.0
    top_reasons = Counter(all_reasons).most_common(5)

    return {
        "total_checks": total,
        "approved_count": approved,
        "rejected_count": rejected,
        "manual_count": manual,
        "average_time_ms": avg_time,
        "top_reasons": [{"reason": reason, "count": count} for reason, count in top_reasons],
    }


# -----------------------------
# Админ-эндпоинты
# -----------------------------
@app.post("/admin/update/{check_id}")
def admin_update_status(
    check_id: int,
    status: str = Form(...),
) -> Dict[str, Any]:
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Некорректный статус")

    with db_lock:
        cur = conn.execute(
            "UPDATE moderation_checks SET status = ? WHERE id = ?",
            (status, check_id),
        )
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Проверка не найдена")

    return {"ok": True, "id": check_id, "new_status": status}


@app.get("/admin/rules")
def admin_rules() -> Dict[str, Any]:
    return {"rules": get_rule_settings_map()}


@app.post("/admin/rules/toggle/{rule_name}")
def admin_toggle_rule(rule_name: str) -> Dict[str, Any]:
    if rule_name not in RULE_NAMES:
        raise HTTPException(status_code=404, detail="Правило не найдено")

    with db_lock:
        row = conn.execute(
            "SELECT enabled FROM rule_settings WHERE rule_name = ?", (rule_name,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Правило не найдено")
        new_value = 0 if row["enabled"] else 1
        conn.execute(
            "UPDATE rule_settings SET enabled = ? WHERE rule_name = ?",
            (new_value, rule_name),
        )
        conn.commit()

    return {"rule_name": rule_name, "enabled": bool(new_value)}


@app.post("/admin/rules/priority/{rule_name}")
def admin_change_priority(rule_name: str, payload: RulePriorityRequest) -> Dict[str, Any]:
    if rule_name not in RULE_NAMES:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    with db_lock:
        cur = conn.execute(
            "UPDATE rule_settings SET priority = ? WHERE rule_name = ?",
            (payload.priority, rule_name),
        )
        conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    return {"rule_name": rule_name, "priority": payload.priority}


def _render_html(initial_tab: str = "moderation") -> str:
    # Один UI с вкладками "Модерация" и "Админ-панель".
    return """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Модерация контента</title>
  <style>
    :root {
      --bg1: #0f172a;
      --bg2: #1e293b;
      --glass: rgba(255, 255, 255, 0.14);
      --line: rgba(255, 255, 255, 0.25);
      --text: #e2e8f0;
      --muted: #cbd5e1;
      --accent: #60a5fa;
      --ok: #22c55e;
      --bad: #ef4444;
      --warn: #f59e0b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, Arial, sans-serif;
      color: var(--text);
      background: radial-gradient(circle at 10% 20%, var(--bg2), var(--bg1));
      min-height: 100vh;
      padding: 20px;
    }
    .shell { max-width: 1100px; margin: 0 auto; }
    .card {
      background: var(--glass);
      border: 1px solid var(--line);
      border-radius: 18px;
      backdrop-filter: blur(14px);
      -webkit-backdrop-filter: blur(14px);
      padding: 18px;
      margin-bottom: 16px;
    }
    h1 { margin: 0 0 14px; font-size: 28px; }
    .tabs { display: flex; gap: 8px; margin-bottom: 12px; }
    .tab-btn {
      border: 1px solid var(--line); background: transparent; color: var(--text);
      padding: 10px 14px; border-radius: 10px; cursor: pointer;
    }
    .tab-btn.active { background: rgba(96, 165, 250, 0.25); border-color: var(--accent); }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .full { grid-column: 1 / -1; }
    textarea, select, input[type="number"] {
      width: 100%; background: rgba(15, 23, 42, 0.6); color: var(--text);
      border: 1px solid var(--line); border-radius: 10px; padding: 10px;
    }
    button.action {
      background: rgba(96, 165, 250, 0.25);
      color: var(--text);
      border: 1px solid var(--accent);
      border-radius: 10px;
      padding: 9px 12px;
      cursor: pointer;
    }
    button.small { padding: 6px 10px; font-size: 12px; }
    .line { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .rule-row, .status-row {
      display: flex; justify-content: space-between; gap: 12px; align-items: center;
      border-bottom: 1px dashed var(--line); padding: 8px 0;
    }
    .table-wrap { overflow: auto; }
    table { width: 100%; border-collapse: collapse; min-width: 840px; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid var(--line); font-size: 13px; }
    .badge { padding: 4px 8px; border-radius: 999px; font-size: 12px; }
    .approved { background: rgba(34, 197, 94, 0.2); color: #bbf7d0; }
    .rejected { background: rgba(239, 68, 68, 0.2); color: #fecaca; }
    .manual_review { background: rgba(245, 158, 11, 0.2); color: #fde68a; }
    .muted { color: var(--muted); font-size: 13px; }
    .hidden { display: none; }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
    .pager { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
  </style>
</head>
<body>
  <div class="shell">
    <div class="card">
      <h1>Модерация контента</h1>
      <div class="tabs">
        <button id="tabModeration" class="tab-btn active">Модерация</button>
        <button id="tabAdmin" class="tab-btn">Админ-панель</button>
      </div>
    </div>

    <section id="viewModeration">
      <div class="card grid">
        <div class="full">
          <label>Текст для проверки</label>
          <textarea id="textInput" rows="5" placeholder="Введите текст..."></textarea>
        </div>
        <div>
          <label>Подход модерации</label>
          <select id="approachSelect">
            <option value="procedural">procedural</option>
            <option value="oop">oop</option>
          </select>
        </div>
        <div>
          <label>Правила (чекбоксы)</label>
          <div class="line">
            <label><input type="checkbox" class="rule-cb" value="banned_words" checked /> banned_words</label>
            <label><input type="checkbox" class="rule-cb" value="links" checked /> links</label>
            <label><input type="checkbox" class="rule-cb" value="repetitions" checked /> repetitions</label>
            <label><input type="checkbox" class="rule-cb" value="length" checked /> length</label>
          </div>
        </div>
        <div class="full">
          <button class="action" id="moderateBtn">Проверить</button>
          <p class="muted" id="moderationResult">Результат появится здесь.</p>
        </div>
      </div>

      <div class="card">
        <div class="line" style="justify-content:space-between;">
          <h3>История проверок</h3>
          <button class="action small" id="refreshHistoryBtn">Обновить</button>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>ID</th><th>Статус</th><th>Подход</th><th>Причины</th><th>Время (мс)</th><th>Дата</th></tr>
            </thead>
            <tbody id="historyBody"></tbody>
          </table>
        </div>
      </div>
    </section>

    <section id="viewAdmin" class="hidden">
      <div class="card grid">
        <div>
          <label>Фильтр по статусу</label>
          <select id="adminStatusFilter">
            <option value="">Все</option>
            <option value="approved">approved</option>
            <option value="rejected">rejected</option>
            <option value="manual_review">manual_review</option>
          </select>
        </div>
        <div style="display:flex;align-items:end;">
          <button class="action" id="refreshAdminBtn">Обновить админ-таблицу</button>
        </div>
        <div>
          <label>Записей на страницу</label>
          <select id="adminLimit">
            <option value="20" selected>20</option>
            <option value="50">50</option>
            <option value="100">100</option>
          </select>
        </div>
      </div>

      <div class="card">
        <h3>Панель управления правилами (OOP)</h3>
        <div id="rulesBox" class="muted">Загрузка правил...</div>
      </div>

      <div class="card">
        <h3>Все проверки</h3>
        <div class="pager">
          <button class="action small" id="adminPrevBtn">Назад</button>
          <button class="action small" id="adminNextBtn">Вперед</button>
          <span id="adminPagerInfo" class="muted">Страница 1</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>ID</th><th>Статус</th><th>Текст</th><th>Причины</th><th>Действия</th></tr>
            </thead>
            <tbody id="adminBody"></tbody>
          </table>
        </div>
      </div>
    </section>
  </div>

  <script>
    const tabModeration = document.getElementById("tabModeration");
    const tabAdmin = document.getElementById("tabAdmin");
    const viewModeration = document.getElementById("viewModeration");
    const viewAdmin = document.getElementById("viewAdmin");
    const resultEl = document.getElementById("moderationResult");
    const historyBody = document.getElementById("historyBody");
    const adminBody = document.getElementById("adminBody");
    const rulesBox = document.getElementById("rulesBox");
    const adminPagerInfo = document.getElementById("adminPagerInfo");
    const initialTab = "__INITIAL_TAB__";
    let adminPage = 1;
    let adminTotal = 0;

    function setTab(admin) {
      viewAdmin.classList.toggle("hidden", !admin);
      viewModeration.classList.toggle("hidden", admin);
      tabAdmin.classList.toggle("active", admin);
      tabModeration.classList.toggle("active", !admin);
    }

    tabModeration.addEventListener("click", () => setTab(false));
    tabAdmin.addEventListener("click", () => { setTab(true); loadAdminData(); });

    function statusBadge(status) {
      return `<span class="badge ${status}">${status}</span>`;
    }

    async function moderate() {
      const text = document.getElementById("textInput").value.trim();
      const approach = document.getElementById("approachSelect").value;
      const enabled_rules = [...document.querySelectorAll(".rule-cb:checked")].map(cb => cb.value);

      if (!text) {
        resultEl.textContent = "Введите текст перед проверкой.";
        return;
      }

      const res = await fetch(`/moderate?approach=${approach}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, enabled_rules })
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        resultEl.textContent = `Ошибка: ${data.detail || res.status}`;
        return;
      }

      const data = await res.json();
      resultEl.innerHTML = `ID: ${data.id} | Статус: ${statusBadge(data.status)} | Причины: ${data.reasons.join("; ")} | Время: ${data.processing_time_ms} мс`;
      loadHistory();
    }

    async function loadHistory() {
      const res = await fetch("/moderation/history?page=1&limit=20");
      const data = await res.json();
      historyBody.innerHTML = (data.items || []).map(item => `
        <tr>
          <td>${item.id}</td>
          <td>${statusBadge(item.status)}</td>
          <td>${item.approach}</td>
          <td>${(item.reasons || []).join("; ")}</td>
          <td>${item.processing_time_ms}</td>
          <td>${item.created_at}</td>
        </tr>
      `).join("");
    }

    async function updateStatus(id, status) {
      const fd = new FormData();
      fd.append("status", status);
      const res = await fetch(`/admin/update/${id}`, { method: "POST", body: fd });
      if (!res.ok) {
        alert("Не удалось обновить статус");
      }
      await loadAdminChecks();
      await loadHistory();
    }

    async function loadAdminChecks() {
      const filter = document.getElementById("adminStatusFilter").value;
      const limit = Number(document.getElementById("adminLimit").value || 20);
      const q = new URLSearchParams({ page: String(adminPage), limit: String(limit) });
      if (filter) q.set("status", filter);
      const res = await fetch(`/admin/checks?${q.toString()}`);
      const data = await res.json();
      const items = data.items || [];
      adminTotal = Number(data.total || 0);
      const totalPages = Math.max(1, Math.ceil(adminTotal / limit));
      adminPagerInfo.textContent = `Страница ${adminPage} из ${totalPages} | Всего: ${adminTotal}`;

      adminBody.innerHTML = items.map(item => `
        <tr>
          <td>${item.id}</td>
          <td>${statusBadge(item.status)}</td>
          <td>${item.text.slice(0, 80)}</td>
          <td>${(item.reasons || []).join("; ")}</td>
          <td>
            <div class="line">
              <button class="action small" onclick="updateStatus(${item.id}, 'approved')">Одобрить</button>
              <button class="action small" onclick="updateStatus(${item.id}, 'rejected')">Отклонить</button>
              <button class="action small" onclick="updateStatus(${item.id}, 'manual_review')">На ручную</button>
            </div>
          </td>
        </tr>
      `).join("");
    }

    async function toggleRule(ruleName) {
      await fetch(`/admin/rules/toggle/${ruleName}`, { method: "POST" });
      await loadRules();
    }

    async function setPriority(ruleName, inputId) {
      const priority = Number(document.getElementById(inputId).value);
      await fetch(`/admin/rules/priority/${ruleName}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ priority })
      });
      await loadRules();
    }

    async function loadRules() {
      const res = await fetch("/admin/rules");
      const data = await res.json();
      const rules = data.rules || {};
      const names = Object.keys(rules).sort();
      rulesBox.innerHTML = names.map((name, idx) => `
        <div class="rule-row">
          <div>
            <b>${name}</b><br />
            <span class="muted">enabled: ${rules[name].enabled}, priority: ${rules[name].priority}</span>
          </div>
          <div class="line">
            <button class="action small" onclick="toggleRule('${name}')">${rules[name].enabled ? "Выключить" : "Включить"}</button>
            <input id="prio_${idx}" type="range" min="1" max="10" value="${rules[name].priority}" />
            <span class="muted" id="prio_lbl_${idx}">${rules[name].priority}</span>
            <button class="action small" onclick="setPriority('${name}', 'prio_${idx}')">Сохранить</button>
          </div>
        </div>
      `).join("");
      names.forEach((_, idx) => {
        const slider = document.getElementById(`prio_${idx}`);
        const label = document.getElementById(`prio_lbl_${idx}`);
        if (slider && label) {
          slider.addEventListener("input", () => { label.textContent = slider.value; });
        }
      });
    }

    async function loadAdminData() {
      await Promise.all([loadAdminChecks(), loadRules()]);
    }

    document.getElementById("moderateBtn").addEventListener("click", moderate);
    document.getElementById("refreshHistoryBtn").addEventListener("click", loadHistory);
    document.getElementById("refreshAdminBtn").addEventListener("click", loadAdminData);
    document.getElementById("adminStatusFilter").addEventListener("change", () => {
      adminPage = 1;
      loadAdminChecks();
    });
    document.getElementById("adminLimit").addEventListener("change", () => {
      adminPage = 1;
      loadAdminChecks();
    });
    document.getElementById("adminPrevBtn").addEventListener("click", async () => {
      if (adminPage <= 1) return;
      adminPage -= 1;
      await loadAdminChecks();
    });
    document.getElementById("adminNextBtn").addEventListener("click", async () => {
      const limit = Number(document.getElementById("adminLimit").value || 20);
      const totalPages = Math.max(1, Math.ceil(adminTotal / limit));
      if (adminPage >= totalPages) return;
      adminPage += 1;
      await loadAdminChecks();
    });

    loadHistory();
    if (initialTab === "admin") {
      setTab(true);
      loadAdminData();
    }
  </script>
</body>
</html>
""".replace("__INITIAL_TAB__", initial_tab)


@app.get("/", response_class=HTMLResponse)
def moderation_page() -> str:
    return _render_html(initial_tab="moderation")


@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> str:
    return _render_html(initial_tab="admin")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
