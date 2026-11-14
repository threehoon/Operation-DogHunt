"""只读日志文件：按调用编号把日志归成一次次调用，供“调用日志”页列表与详情使用。不修改、不删除任何文件。"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from app.core import applog

MAX_BYTES = 20 * 1024 * 1024
MAX_DAYS = applog.KEEP_DAYS
KINDS = ("chat", "odds_refresh", "task", "api")


def _today() -> date:
    return datetime.now(applog.BEIJING).date()


def _read_file(path: Path) -> list[dict]:
    """超过 MAX_BYTES 时只读最后一段，丢掉被截断的第一行；坏行跳过。"""
    entries = []
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            if size > MAX_BYTES:
                handle.seek(size - MAX_BYTES)
                handle.readline()
            for raw in handle:
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(entry, dict) and isinstance(entry.get("ts"), str):
                    entries.append(entry)
    except OSError:
        return []
    return entries


def load_entries(days: int, root: Path | None = None, today: date | None = None) -> list[dict]:
    root = root or applog.log_dir()
    today = today or _today()
    entries: list[dict] = []
    for offset in range(max(1, min(days, MAX_DAYS))):
        path = root / f"app-{(today - timedelta(days=offset)).isoformat()}.jsonl"
        if path.exists():
            entries.extend(_read_file(path))
    return entries


def _entries_around(trace_id: str, root: Path | None = None, today: date | None = None) -> list[dict]:
    """从今天往前逐天读，找到这次调用以后，再往前一天没有它就停，不用每次读满 MAX_DAYS 天。
    它触发的后台任务总在它之后，已经在更新的文件里读到了；编号不存在时才会读满。"""
    root = root or applog.log_dir()
    today = today or _today()
    entries: list[dict] = []
    found = False
    for offset in range(MAX_DAYS):
        path = root / f"app-{(today - timedelta(days=offset)).isoformat()}.jsonl"
        day = _read_file(path) if path.exists() else []
        here = any(e.get("trace_id") == trace_id for e in day)
        if found and not here:
            break
        found = found or here
        entries.extend(day)
    return entries


def _kind(trace_id: str, path: str | None, entries: list[dict]) -> str:
    if trace_id.startswith("task-"):
        return "task"
    if path and path.startswith("/api/v1/chat"):
        return "chat"
    if path == "/api/v1/odds/refresh":
        return "odds_refresh"
    if path is None:
        # 还没传完的请求没有 http.request 这一条，按已写下的步骤认类型，免得进行中的聊天被“聊天”筛选漏掉。
        names = [e.get("event") or "" for e in entries]
        if any(name.startswith(("chat.", "llm.")) for name in names):
            return "chat"
        if any(name.startswith("odds.") for name in names):
            return "odds_refresh"
    return "api"


def summarize(trace_id: str, entries: list[dict]) -> dict:
    entries = sorted(entries, key=lambda e: e["ts"])
    http = next((e for e in entries if e.get("event") == "http.request"), None)
    start = next((e for e in entries if e.get("event") == "task.start"), None)
    end = next((e for e in entries if e.get("event") == "task.end"), None)
    http_data = (http or {}).get("data") or {}
    path = http_data.get("path")
    kind = _kind(trace_id, path, entries)
    warnings = sum(e.get("level") == "WARNING" and e is not http for e in entries)
    errors = sum(e.get("level") in {"ERROR", "CRITICAL"} and e is not http for e in entries)
    status_code = http_data.get("status")
    if kind == "task":
        running = end is None
        duration = ((end or {}).get("data") or {}).get("ms")
        title = "后台取数" if start else "后台任务"
    else:
        # 流式回答在传完之前还没有 http.request 这一条。
        running = http is None
        duration = http_data.get("ms")
        title = f"{http_data.get('method', '')} {path}".strip() if http else "进行中"
    if duration is None and len(entries) > 1:
        duration = round((datetime.fromisoformat(entries[-1]["ts"]) - datetime.fromisoformat(entries[0]["ts"])).total_seconds() * 1000)
    failed = errors > 0 or (isinstance(status_code, int) and status_code >= 400) or http_data.get("outcome") == "error"
    status = "running" if running and not failed else "error" if failed else "warning" if warnings or http_data.get("outcome") == "disconnected" else "ok"
    problem = next((e for e in entries if e.get("level") in {"ERROR", "CRITICAL", "WARNING"} and e is not http), None) or (http if status in {"error", "warning"} else None)
    return {
        "trace_id": trace_id, "parent": next((e.get("parent") for e in entries if e.get("parent")), None),
        "kind": kind, "title": title, "status": status, "http_status": status_code if isinstance(status_code, int) else None,
        "started_at": entries[0]["ts"], "ended_at": entries[-1]["ts"], "duration_ms": duration,
        "steps": len(entries), "warnings": warnings, "errors": errors,
        "headline": problem.get("msg") if problem else None, "children": [],
    }


def _group(entries: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for entry in entries:
        trace_id = entry.get("trace_id")
        if isinstance(trace_id, str):
            groups.setdefault(trace_id, []).append(entry)
    return groups


def list_traces(days: int = 1, kind: str | None = None, problems_only: bool = False, query: str | None = None,
                cursor: int = 0, limit: int = 50, root: Path | None = None, today: date | None = None) -> dict:
    summaries = {tid: summarize(tid, items) for tid, items in _group(load_entries(days, root, today)).items()}
    for summary in summaries.values():
        parent = summaries.get(summary["parent"] or "")
        if parent is not None:
            parent["children"].append(summary["trace_id"])
    items = list(summaries.values())
    if kind:
        items = [s for s in items if s["kind"] == kind]
    if problems_only:
        items = [s for s in items if s["status"] in {"error", "warning"}]
    if query:
        needle = query.strip().lower()
        items = [s for s in items if needle in s["trace_id"] or needle in s["title"].lower() or needle in (s["headline"] or "").lower()]
    items.sort(key=lambda s: s["started_at"], reverse=True)
    page = items[cursor:cursor + limit]
    return {"items": page, "total": len(items), "next_cursor": cursor + limit if cursor + limit < len(items) else None}


def read_archive(trace_id: str, root: Path | None = None) -> dict | None:
    if not applog.TRACE_ID.fullmatch(trace_id):
        return None
    path = (root or applog.trace_dir()) / f"{trace_id}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def get_trace(trace_id: str, root: Path | None = None, archive_root: Path | None = None, today: date | None = None) -> dict | None:
    """一次调用的全部日志，加上它触发的后台任务概要和聊天存档；编号格式不对或找不到时返回 None。"""
    if not applog.TRACE_ID.fullmatch(trace_id):
        return None
    groups = _group(_entries_around(trace_id, root, today))
    entries = groups.get(trace_id)
    if not entries:
        return None
    children = [summarize(tid, items) for tid, items in groups.items() if any(e.get("parent") == trace_id for e in items)]
    return {
        "summary": summarize(trace_id, entries),
        "entries": sorted(entries, key=lambda e: e["ts"]),
        "children": sorted(children, key=lambda s: s["started_at"]),
        "archive": read_archive(trace_id, archive_root),
    }
