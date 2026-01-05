from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class FailureRecord:
    task_id: str
    model: str
    test_id: str
    passed: bool
    expected: str
    actual: str
    error: Optional[str]
    timestamp: str


@dataclass
class RunMeta:
    run_id: str
    task_id: str
    start_time: str
    end_time: Optional[str]
    machine_info: Optional[str]
    harbor_version: Optional[str]


@dataclass
class StageResultRecord:
    run_id: str
    stage_name: str
    status: str
    exit_code: int
    runtime_ms: int
    logs_path: str
    error_signature_id: Optional[str]


@dataclass
class FailureSignatureRecord:
    signature_id: str
    stage_name: str
    category: str
    message_hash: str
    key_features: Dict[str, Any]
    example_log_snippet: str


class FailureLogger:
    def __init__(self, context_dir: str = "./.context") -> None:
        self.context_dir = Path(context_dir)
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.context_dir / "runs.jsonl"
        self.db_path = self.context_dir / "runs.sqlite"
        self._ensure_db()

    def _ensure_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                create table if not exists runs (
                    id integer primary key autoincrement,
                    task_id text not null,
                    model text not null,
                    test_id text not null,
                    passed integer not null,
                    expected text,
                    actual text,
                    error text,
                    timestamp text not null
                )
                """
            )
            conn.execute(
                """
                create table if not exists run_meta (
                    run_id text primary key,
                    task_id text not null,
                    start_time text not null,
                    end_time text,
                    machine_info text,
                    harbor_version text
                )
                """
            )
            conn.execute(
                """
                create table if not exists stage_results (
                    id integer primary key autoincrement,
                    run_id text not null,
                    stage_name text not null,
                    status text not null,
                    exit_code integer not null,
                    runtime_ms integer not null,
                    logs_path text,
                    error_signature_id text
                )
                """
            )
            conn.execute(
                """
                create table if not exists failure_signatures (
                    signature_id text primary key,
                    stage_name text not null,
                    category text not null,
                    message_hash text not null,
                    key_features text,
                    example_log_snippet text,
                    first_seen text not null,
                    last_seen text not null,
                    count integer not null
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def log_failure_records(self, records: List[FailureRecord]) -> None:
        if not records:
            return
        with self.log_path.open("a", encoding="utf-8") as f:
            for rec in records:
                obj: Dict[str, Any] = {
                    "task_id": rec.task_id,
                    "model": rec.model,
                    "test_id": rec.test_id,
                    "passed": rec.passed,
                    "expected": rec.expected,
                    "actual": rec.actual,
                    "error": rec.error,
                    "timestamp": rec.timestamp,
                }
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany(
                """
                insert into runs (task_id, model, test_id, passed, expected, actual, error, timestamp)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        rec.task_id,
                        rec.model,
                        rec.test_id,
                        1 if rec.passed else 0,
                        rec.expected,
                        rec.actual,
                        rec.error,
                        rec.timestamp,
                    )
                    for rec in records
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def get_or_create_signature(
        self,
        stage_name: str,
        category: str,
        message: str,
        key_features: Optional[Dict[str, Any]],
        timestamp: str,
        example_snippet: Optional[str] = None,
    ) -> str:
        stage = stage_name or ""
        cat = category or ""
        msg = (message or "").strip()
        msg_hash = sha256(msg.encode("utf-8")).hexdigest()
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                """
                select signature_id, count
                from failure_signatures
                where stage_name = ? and category = ? and message_hash = ?
                """,
                (stage, cat, msg_hash),
            )
            row = cur.fetchone()
            if row:
                signature_id, count_val = row
                new_count = int(count_val) + 1
                conn.execute(
                    """
                    update failure_signatures
                    set last_seen = ?, count = ?
                    where signature_id = ?
                    """,
                    (timestamp, new_count, signature_id),
                )
                conn.commit()
                return str(signature_id)
            signature_id = str(uuid.uuid4())
            features_json = json.dumps(key_features or {}, ensure_ascii=False)
            snippet = example_snippet if example_snippet is not None else msg[:200]
            conn.execute(
                """
                insert into failure_signatures (
                    signature_id,
                    stage_name,
                    category,
                    message_hash,
                    key_features,
                    example_log_snippet,
                    first_seen,
                    last_seen,
                    count
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signature_id,
                    stage,
                    cat,
                    msg_hash,
                    features_json,
                    snippet,
                    timestamp,
                    timestamp,
                    1,
                ),
            )
            conn.commit()
            return signature_id
        finally:
            conn.close()

    def create_run(
        self,
        task_id: str,
        start_time: str,
        end_time: Optional[str] = None,
        machine_info: Optional[str] = None,
        harbor_version: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> str:
        run_id_value = run_id or str(uuid.uuid4())
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                insert or replace into run_meta (run_id, task_id, start_time, end_time, machine_info, harbor_version)
                values (?, ?, ?, ?, ?, ?)
                """,
                (run_id_value, task_id, start_time, end_time, machine_info, harbor_version),
            )
            conn.commit()
        finally:
            conn.close()
        return run_id_value

    def update_run_end_time(self, run_id: str, end_time: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                update run_meta
                set end_time = ?
                where run_id = ?
                """,
                (end_time, run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def log_stage_results(self, results: List[StageResultRecord]) -> None:
        if not results:
            return
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executemany(
                """
                insert into stage_results (run_id, stage_name, status, exit_code, runtime_ms, logs_path, error_signature_id)
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        rec.run_id,
                        rec.stage_name,
                        rec.status,
                        rec.exit_code,
                        rec.runtime_ms,
                        rec.logs_path,
                        rec.error_signature_id,
                    )
                    for rec in results
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def top_failing_tasks(self, limit: int = 10) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                """
                select task_id,
                       model,
                       count(*) as total,
                       sum(case when passed = 0 then 1 else 0 end) as failures
                from runs
                group by task_id, model
                having total > 0
                order by failures desc, total desc
                limit ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        out: List[Dict[str, Any]] = []
        for task_id, model, total, failures in rows:
            out.append(
                {
                    "task_id": task_id,
                    "model": model,
                    "total": int(total),
                    "failures": int(failures),
                }
            )
        return out

    def generate_html_report(self, output_path: Optional[str] = None) -> str:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                """
                select model,
                       count(*) as total,
                       sum(case when passed = 1 then 1 else 0 end) as passed
                from runs
                group by model
                """
            )
            model_rows = cur.fetchall()
            cur = conn.execute(
                """
                select task_id,
                       model,
                       test_id,
                       min(passed) as min_passed,
                       max(passed) as max_passed,
                       count(*) as total
                from runs
                group by task_id, model, test_id
                having min_passed = 0 and max_passed = 1
                """
            )
            flake_rows = cur.fetchall()
            cur = conn.execute(
                """
                select stage_name,
                       count(*) as total,
                       sum(case when status = 'pass' then 1 else 0 end) as passes
                from stage_results
                group by stage_name
                """
            )
            stage_rows = cur.fetchall()
            cur = conn.execute(
                """
                select category,
                       sum(count) as total
                from failure_signatures
                group by category
                order by total desc
                """
            )
            category_rows = cur.fetchall()
            cur = conn.execute(
                """
                select signature_id,
                       stage_name,
                       category,
                       message_hash,
                       example_log_snippet,
                       first_seen,
                       last_seen,
                       count
                from failure_signatures
                order by count desc, last_seen desc
                limit 20
                """
            )
            signature_rows = cur.fetchall()
        finally:
            conn.close()

        html_parts: List[str] = []
        html_parts.append("<!DOCTYPE html>")
        html_parts.append("<html lang='en'>")
        html_parts.append("<head>")
        html_parts.append("<meta charset='utf-8'>")
        html_parts.append("<title>HarborForge Report</title>")
        html_parts.append(
            "<style>"
            "body{font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:2rem;background:#0b0c10;color:#f5f7ff;}"
            "h1,h2,h3{color:#e5f1ff;}"
            "section{margin-bottom:2rem;padding:1.25rem;border-radius:0.75rem;background:rgba(15,23,42,0.9);border:1px solid rgba(148,163,184,0.4);}"
            "table{border-collapse:collapse;width:100%;margin-top:0.75rem;font-size:0.9rem;}"
            "th,td{border-bottom:1px solid rgba(148,163,184,0.3);padding:0.4rem 0.6rem;text-align:left;}"
            "th{background:rgba(15,23,42,0.95);color:#e5f1ff;font-weight:600;}"
            "tr:nth-child(even){background:rgba(15,23,42,0.75);}"
            ".metric{font-size:1.1rem;margin-right:1.5rem;display:inline-block;}"
            ".metric strong{font-size:1.3rem;color:#4ade80;margin-right:0.25rem;}"
            ".chip{display:inline-block;padding:0.15rem 0.5rem;border-radius:999px;background:rgba(31,41,55,0.9);font-size:0.75rem;margin-right:0.25rem;}"
            ".pill-pass{background:rgba(22,163,74,0.15);border:1px solid rgba(52,211,153,0.7);}"
            ".pill-fail{background:rgba(220,38,38,0.18);border:1px solid rgba(248,113,113,0.8);}"
            ".pill-flake{background:rgba(234,179,8,0.16);border:1px solid rgba(250,204,21,0.85);}"
            "</style>"
        )
        html_parts.append("</head>")
        html_parts.append("<body>")
        html_parts.append("<h1>HarborForge Analytics Report</h1>")

        total_model_runs = sum(int(row["total"]) for row in model_rows) if model_rows else 0
        total_flaky = len(flake_rows)
        total_signatures = sum(int(row["total"]) for row in category_rows) if category_rows else 0

        html_parts.append("<section>")
        html_parts.append("<h2>Overview</h2>")
        html_parts.append("<div class='metric'><strong>")
        html_parts.append(str(total_model_runs))
        html_parts.append("</strong>model test runs</div>")
        html_parts.append("<div class='metric'><strong>")
        html_parts.append(str(total_signatures))
        html_parts.append("</strong>failure signature hits</div>")
        html_parts.append("<div class='metric'><strong>")
        html_parts.append(str(total_flaky))
        html_parts.append("</strong>flaky test triplets</div>")
        html_parts.append("</section>")

        html_parts.append("<section>")
        html_parts.append("<h2>Model pass rates</h2>")
        if not model_rows:
            html_parts.append("<p>No model runs recorded.</p>")
        else:
            html_parts.append("<table>")
            html_parts.append("<tr><th>Model</th><th>Pass rate</th><th>Passed</th><th>Total</th></tr>")
            for row in model_rows:
                model = escape(str(row["model"]))
                total = int(row["total"])
                passed = int(row["passed"] or 0)
                rate = (passed / total) if total > 0 else 0.0
                html_parts.append(
                    "<tr>"
                    f"<td>{model}</td>"
                    f"<td>{rate:.1%}</td>"
                    f"<td>{passed}</td>"
                    f"<td>{total}</td>"
                    "</tr>"
                )
            html_parts.append("</table>")
        html_parts.append("</section>")

        html_parts.append("<section>")
        html_parts.append("<h2>Stage outcomes</h2>")
        if not stage_rows:
            html_parts.append("<p>No validation stages recorded.</p>")
        else:
            html_parts.append("<table>")
            html_parts.append("<tr><th>Stage</th><th>Pass rate</th><th>Passed</th><th>Total</th></tr>")
            for row in stage_rows:
                stage_name = escape(str(row["stage_name"]))
                total = int(row["total"])
                passes = int(row["passes"] or 0)
                rate = (passes / total) if total > 0 else 0.0
                html_parts.append(
                    "<tr>"
                    f"<td>{stage_name}</td>"
                    f"<td>{rate:.1%}</td>"
                    f"<td>{passes}</td>"
                    f"<td>{total}</td>"
                    "</tr>"
                )
            html_parts.append("</table>")
        html_parts.append("</section>")

        html_parts.append("<section>")
        html_parts.append("<h2>Failure categories</h2>")
        if not category_rows:
            html_parts.append("<p>No failure signatures recorded.</p>")
        else:
            html_parts.append("<table>")
            html_parts.append("<tr><th>Category</th><th>Hits</th></tr>")
            for row in category_rows:
                category = escape(str(row["category"]))
                total = int(row["total"])
                html_parts.append(
                    "<tr>"
                    f"<td>{category}</td>"
                    f"<td>{total}</td>"
                    "</tr>"
                )
            html_parts.append("</table>")
        html_parts.append("</section>")

        html_parts.append("<section>")
        html_parts.append("<h2>Top failure signatures</h2>")
        if not signature_rows:
            html_parts.append("<p>No failure signatures recorded.</p>")
        else:
            html_parts.append("<table>")
            html_parts.append("<tr><th>Signature</th><th>Stage</th><th>Category</th><th>Hits</th><th>Example</th></tr>")
            for row in signature_rows:
                sig_id = escape(str(row["signature_id"]))
                stage_name = escape(str(row["stage_name"]))
                category = escape(str(row["category"]))
                count_val = int(row["count"])
                snippet = escape(str(row["example_log_snippet"] or ""))[:240]
                html_parts.append(
                    "<tr>"
                    f"<td><code>{sig_id}</code></td>"
                    f"<td>{stage_name}</td>"
                    f"<td>{category}</td>"
                    f"<td>{count_val}</td>"
                    f"<td>{snippet}</td>"
                    "</tr>"
                )
            html_parts.append("</table>")
        html_parts.append("</section>")

        html_parts.append("<section>")
        html_parts.append("<h2>Flake statistics</h2>")
        if not flake_rows:
            html_parts.append("<p>No flaky triplets detected across task, model, and test_id.</p>")
        else:
            html_parts.append("<p>Flaky triplets have at least one pass and one fail for the same task_id, model, and test_id.</p>")
            html_parts.append("<table>")
            html_parts.append("<tr><th>Task</th><th>Model</th><th>Test</th><th>Total runs</th></tr>")
            for row in flake_rows[:50]:
                task_id = escape(str(row["task_id"]))
                model = escape(str(row["model"]))
                test_id = escape(str(row["test_id"]))
                total = int(row["total"])
                html_parts.append(
                    "<tr>"
                    f"<td>{task_id}</td>"
                    f"<td>{model}</td>"
                    f"<td>{test_id}</td>"
                    f"<td>{total}</td>"
                    "</tr>"
                )
            html_parts.append("</table>")
        html_parts.append("</section>")

        html_parts.append("</body></html>")

        html_content = "".join(html_parts)
        if output_path:
            out_path = Path(output_path)
        else:
            out_path = self.context_dir / "reports" / "harborforge_report.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_content, encoding="utf-8")
        return str(out_path)
