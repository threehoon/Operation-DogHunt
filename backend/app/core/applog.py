"""调用日志：终端显示可读文字，另按北京时间每天写一个 JSON 行文件；调用编号经 contextvars 传给本次调用的所有日志。

写入前统一脱敏。异常只有本项目自己的错误类记录原文，其他异常只记类型和出错位置，避免网址或请求头里的密钥进入日志。
"""

import json
import logging
import os
import re
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import monotonic

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "data" / "logs"
DEFAULT_TRACE_DIR = PROJECT_ROOT / "data" / "traces"
KEEP_DAYS = 14
KEEP_TRACES = 200
BEIJING = timezone(timedelta(hours=8))
FILE_PATTERN = re.compile(r"^app-(\d{4}-\d{2}-\d{2})\.jsonl$")
# 用 fullmatch 校验：match 加 $ 会放过结尾带换行的编号。
TRACE_ID = re.compile(r"(task-)?[0-9a-f]{12}")
# 这些错误类的提示是本项目自己写的，不含密钥，可以记录原文；其他异常只记类型和位置。
SAFE_ERRORS = {"ModelError", "DunkError", "ScheduleError", "FetchError", "OddsRefreshError", "ChatPromptError"}

_trace: ContextVar[str | None] = ContextVar("trace_id", default=None)
_parent: ContextVar[str | None] = ContextVar("parent_trace_id", default=None)
_secrets: set[str] = set()
_secrets_lock = threading.Lock()

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[^\s\"',;]+"), r"\1***"),
    (re.compile(r"(?i)((?:x-api-key|authorization|api[_-]?key|access[_-]?token|secret|password)[\"']?\s*[:=]\s*[\"']?)(?!\*\*\*)[^\s\"'&,;}]+"), r"\1***"),
    (re.compile(r"(?i)([?&]key=)[^\s\"'&]+"), r"\1***"),
]


def register_secret(value: str | None) -> None:
    """把实际密钥值登记进来，日志里出现时整段替换；太短的值不登记，避免误伤正常文字。"""
    if value and len(value.strip()) >= 8:
        with _secrets_lock:
            _secrets.add(value.strip())


def redact(text: str) -> str:
    with _secrets_lock:
        known = sorted(_secrets, key=len, reverse=True)
    for value in known:
        text = text.replace(value, "***")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def new_trace_id(task: bool = False) -> str:
    return ("task-" if task else "") + secrets.token_hex(6)


def current_trace() -> str | None:
    return _trace.get()


@contextmanager
def trace(trace_id: str, parent: str | None = None) -> Iterator[str]:
    """在当前上下文设置调用编号；后台线程不会继承 contextvars，要在线程里显式调用。"""
    token, parent_token = _trace.set(trace_id), _parent.set(parent)
    try:
        yield trace_id
    finally:
        _trace.reset(token)
        _parent.reset(parent_token)


def now_text() -> str:
    return datetime.now(BEIJING).isoformat(timespec="milliseconds")


def _where(tb) -> list[str]:
    """只列本项目代码（backend/app 与 scripts）的帧，格式 `相对路径:行号 函数名`，最深的在最后。"""
    frames = []
    while tb is not None:
        code = tb.tb_frame.f_code
        path = Path(code.co_filename)
        for root, prefix in ((APP_ROOT, ""), (PROJECT_ROOT / "scripts", "scripts/")):
            try:
                frames.append(f"{prefix}{path.relative_to(root).as_posix()}:{tb.tb_lineno} {code.co_name}")
                break
            except ValueError:
                continue
        tb = tb.tb_next
    return frames[-6:]


def _type_name(exc: BaseException) -> str:
    cls = type(exc)
    return cls.__qualname__ if cls.__module__ == "builtins" else f"{cls.__module__}.{cls.__qualname__}"


def describe_error(exc: BaseException) -> dict:
    """异常转成可写日志的字典；`from None` 隐藏的原始异常放在 cause 里，同样只记类型和位置。"""
    result: dict = {"type": _type_name(exc), "where": _where(exc.__traceback__)}
    if any(cls.__name__ in SAFE_ERRORS for cls in type(exc).__mro__):
        result["message"] = redact(str(exc))
    inner = exc.__cause__ or exc.__context__
    if inner is not None and inner is not exc:
        result["cause"] = {"type": _type_name(inner), "where": _where(inner.__traceback__)}
    return result


def event(logger: logging.Logger, name: str, msg: str, *, level: int = logging.INFO,
          error: BaseException | None = None, **data) -> None:
    """记一条结构化日志：name 是固定事件名（页面按它显示中文），data 是结构化字段。"""
    logger.log(level, msg, extra={"event": name, "data": data, "err": error})


def run_step(logger: logging.Logger, step: str, fn: Callable[..., str], *args) -> None:
    """后台取数的一步：fn 返回一句结果；含“未更新”算失败，含“跳过”算跳过。任何意外都不向外抛，不影响已保存的盘口。"""
    started = monotonic()
    event(logger, "task.step", f"{step}开始", step=step, status="start")
    try:
        outcome = fn(*args)
    except Exception as exc:  # 可选步骤：任何意外都不能让已保存的盘口更新变成失败。
        event(logger, "task.step", f"{step}未更新：意外错误", level=logging.WARNING, error=exc,
              step=step, status="failed", ms=round((monotonic() - started) * 1000))
        return
    status = "failed" if "未更新" in outcome else "skipped" if "跳过" in outcome else "done"
    event(logger, "task.step", outcome, level=logging.WARNING if status == "failed" else logging.INFO,
          step=step, status=status, ms=round((monotonic() - started) * 1000))


class _ContextFilter(logging.Filter):
    """在发日志的线程里读调用编号；handler 同步执行，所以这里拿到的就是本次调用的编号。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace.get()
        record.parent = _parent.get()
        return True


def _short_name(name: str) -> str:
    return name[4:] if name.startswith("app.") else name


def to_entry(record: logging.LogRecord) -> dict:
    entry = {
        "ts": datetime.fromtimestamp(record.created, BEIJING).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "trace_id": getattr(record, "trace_id", None),
        "parent": getattr(record, "parent", None),
        "logger": _short_name(record.name),
        "event": getattr(record, "event", None) or "log",
        "msg": record.getMessage(),
    }
    data = getattr(record, "data", None)
    if data:
        entry["data"] = data
    error = getattr(record, "err", None)
    if error is None and record.exc_info and record.exc_info[1] is not None:
        error = record.exc_info[1]
    if error is not None:
        entry["error"] = describe_error(error)
    return entry


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(json.dumps(to_entry(record), ensure_ascii=False, default=str))


class ConsoleFormatter(logging.Formatter):
    """保留原来 uvicorn 风格的一行文字，前面加调用编号，出错时补上类型和位置。"""

    def format(self, record: logging.LogRecord) -> str:
        trace_id = getattr(record, "trace_id", None)
        text = f"{record.levelname}:{' ' * max(1, 9 - len(record.levelname))}{_short_name(record.name)}"
        text += f" [{trace_id}] " if trace_id else " "
        text += record.getMessage()
        error = getattr(record, "err", None) or (record.exc_info[1] if record.exc_info else None)
        if error is not None:
            info = describe_error(error)
            text += f"（{info['type']}，{' → '.join(info['where']) or '位置未知'}）"
        return redact(text)


class DailyJsonHandler(logging.Handler):
    """按北京时间日期写 app-YYYY-MM-DD.jsonl，跨日时换文件并删除超过保留天数的旧文件。"""

    def __init__(self, folder: Path, keep_days: int = KEEP_DAYS):
        super().__init__()
        self.folder = folder
        self.keep_days = keep_days
        self._day: date | None = None
        self._stream = None
        self.setFormatter(JsonFormatter())

    def _open(self, day: date) -> None:
        if self._stream is not None:
            self._stream.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        self._stream = (self.folder / f"app-{day.isoformat()}.jsonl").open("a", encoding="utf-8")
        self._day = day
        prune_logs(self.folder, day, self.keep_days)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            day = datetime.fromtimestamp(record.created, BEIJING).date()
            with self.lock:
                if day != self._day or self._stream is None:
                    self._open(day)
                self._stream.write(line + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


def prune_logs(folder: Path, today: date, keep_days: int = KEEP_DAYS) -> None:
    oldest = today - timedelta(days=keep_days - 1)
    for path in folder.glob("app-*.jsonl"):
        match = FILE_PATTERN.match(path.name)
        if match and date.fromisoformat(match.group(1)) < oldest:
            path.unlink(missing_ok=True)


@dataclass
class _State:
    log_dir: Path | None = None
    trace_dir: Path | None = None
    handler: DailyJsonHandler | None = None


_state = _State()


def configure_console() -> None:
    """给 app.* 配终端输出；uvicorn 只给自己的日志器配了输出。--reload 重复导入时不重复添加。"""
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if any(getattr(h, "_doghunt_console", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler._doghunt_console = True
    handler.addFilter(_ContextFilter())
    handler.setFormatter(ConsoleFormatter())
    logger.addHandler(handler)


def configure_files(log_dir: Path | None = None, trace_dir: Path | None = None) -> None:
    """开始写日志文件与聊天存档。只在后端真正启动时调用（lifespan），离线测试不会写进 data/。"""
    log_dir = log_dir or Path(os.environ.get("DOGHUNT_LOG_DIR") or DEFAULT_LOG_DIR)
    trace_dir = trace_dir or Path(os.environ.get("DOGHUNT_TRACE_DIR") or DEFAULT_TRACE_DIR)
    if _state.handler is not None and _state.log_dir == log_dir:
        _state.trace_dir = trace_dir
        return
    shutdown_files()
    handler = DailyJsonHandler(log_dir)
    handler.addFilter(_ContextFilter())
    logging.getLogger("app").addHandler(handler)
    _state.log_dir, _state.trace_dir, _state.handler = log_dir, trace_dir, handler


def shutdown_files() -> None:
    if _state.handler is not None:
        logging.getLogger("app").removeHandler(_state.handler)
        _state.handler.close()
    _state.handler = None
    _state.log_dir = None
    _state.trace_dir = None


def log_dir() -> Path:
    """查询接口读取的目录；未开始写文件时（如离线测试）读默认目录。"""
    return _state.log_dir or Path(os.environ.get("DOGHUNT_LOG_DIR") or DEFAULT_LOG_DIR)


def trace_dir() -> Path:
    return _state.trace_dir or Path(os.environ.get("DOGHUNT_TRACE_DIR") or DEFAULT_TRACE_DIR)


def save_archive(trace_id: str | None, record: dict, keep: int = KEEP_TRACES) -> None:
    """保存一次聊天的完整输入与回答；没有开始写文件时不存。超过 keep 个时删掉最旧的。失败只记日志。"""
    folder = _state.trace_dir
    if folder is None or trace_id is None or not TRACE_ID.fullmatch(trace_id):
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        text = redact(json.dumps({"trace_id": trace_id, **record}, ensure_ascii=False, indent=2, default=str))
        (folder / f"{trace_id}.json").write_text(text, encoding="utf-8")
        files = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)
    except OSError as exc:
        event(logging.getLogger("app.core.applog"), "archive.error", "聊天存档保存失败", level=logging.WARNING, error=exc)
