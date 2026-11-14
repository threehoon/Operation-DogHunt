"""调用日志离线测试：脱敏、调用编号传递、写文件与清理、聊天存档、查询接口。不调用模型和外部接口。"""

import _isolation  # noqa: F401  先于 create_app，避免写进 data/
import json
import logging
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from app.core import applog
from app.core.config import Settings, get_settings
from app.integrations import dunkapi
from app.integrations.llm import ModelError, TextDelta
from app.main import create_app
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.chat import service as chat_service
from app.services.logs import reader
from app.services.odds import board
from app.services.odds.board import build_board
from test_odds import snapshot

logger = logging.getLogger("app.tests.logs")
REAL_CLIENT = httpx.Client


def settings():
    return Settings(_env_file=None, model_base_url="https://relay.example/v1", model_api_key="test-secret-value",
                    model_name="test-model", model_extra_headers='{"x-relay-auth":"extra-secret-value"}')


class FileLogCase(unittest.TestCase):
    """每个测试写到自己的临时目录，结束后移除文件 handler。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="doghunt-logs-"))
        applog.configure_files(self.root / "logs", self.root / "traces")
        self.addCleanup(applog.shutdown_files)

    def entries(self) -> list[dict]:
        lines = []
        for path in sorted((self.root / "logs").glob("app-*.jsonl")):
            lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return lines

    def raw_text(self) -> str:
        texts = [p.read_text(encoding="utf-8") for p in self.root.rglob("*") if p.is_file()]
        return "\n".join(texts)


class RedactTests(unittest.TestCase):
    def test_patterns_and_registered_values(self):
        applog.register_secret("registered-secret-123")
        cases = {
            "Authorization: Bearer sk-abc.def": "sk-abc.def",
            "headers x-api-key: dk_live_999": "dk_live_999",
            "https://api.example/v4/odds?apiKey=ODDSKEY123&regions=us": "ODDSKEY123",
            "https://x.example/?a=1&key=QUERYKEY77": "QUERYKEY77",
            '{"api_key": "json-secret-1"}': "json-secret-1",
            "plain registered-secret-123 inside": "registered-secret-123",
        }
        for text, secret in cases.items():
            with self.subTest(text=text):
                result = applog.redact(text)
                self.assertNotIn(secret, result)
                self.assertIn("***", result)

    def test_short_values_and_normal_words_are_kept(self):
        applog.register_secret("abc")
        self.assertEqual(applog.redact("abc 盘口 key 是 spreads"), "abc 盘口 key 是 spreads")

    def test_error_message_only_for_project_errors(self):
        try:
            try:
                raise RuntimeError("连接 https://x?apiKey=raw-secret-1 失败")
            except RuntimeError:
                raise ModelError("中转站暂不可用", 502) from None
        except ModelError as exc:
            info = applog.describe_error(exc)
        self.assertEqual(info["message"], "中转站暂不可用")
        self.assertEqual(info["cause"]["type"], "RuntimeError")
        self.assertNotIn("raw-secret-1", json.dumps(info, ensure_ascii=False))
        self.assertEqual(info["where"], [])  # 只列 backend/app 与 scripts 的代码，测试文件不在其中
        try:
            raise RuntimeError("内部细节 raw-secret-2")
        except RuntimeError as exc:
            self.assertNotIn("message", applog.describe_error(exc))


class FileTests(FileLogCase):
    def test_entry_has_trace_and_structured_fields(self):
        with applog.trace("a1b2c3d4e5f6", parent=None):
            applog.event(logger, "ext.call", "调用 Bearer leaked-token-1", provider="X", status=200)
        entry = self.entries()[-1]
        self.assertEqual((entry["trace_id"], entry["event"], entry["data"]["status"]), ("a1b2c3d4e5f6", "ext.call", 200))
        self.assertNotIn("leaked-token-1", self.raw_text())

    def test_plain_logger_calls_are_recorded_too(self):
        logging.getLogger("app.services.odds.board").info("普通日志")
        self.assertEqual(self.entries()[-1]["event"], "log")

    def test_old_files_are_pruned(self):
        folder = self.root / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        for day in ("2026-10-01", "2026-11-02", "2026-11-15"):
            (folder / f"app-{day}.jsonl").write_text("", encoding="utf-8")
        applog.prune_logs(folder, date(2026, 11, 15), keep_days=14)
        self.assertEqual(sorted(p.name for p in folder.glob("*.jsonl")), ["app-2026-11-02.jsonl", "app-2026-11-15.jsonl"])

    def test_configure_twice_writes_once(self):
        applog.configure_files(self.root / "logs", self.root / "traces")
        applog.configure_console()
        applog.configure_console()
        logger.info("只写一次")
        self.assertEqual(sum(e["msg"] == "只写一次" for e in self.entries()), 1)

    def test_archive_keeps_latest(self):
        for index in range(5):
            applog.save_archive(f"{index:012x}", {"n": index}, keep=3)
        self.assertEqual(len(list((self.root / "traces").glob("*.json"))), 3)
        applog.save_archive("../escape", {"n": 1})
        self.assertFalse((self.root / "escape.json").exists())


class StepTests(FileLogCase):
    def test_run_step_classifies_and_records_unexpected_errors(self):
        def broken():
            raise KeyError("raw-secret-3")

        with applog.trace("task-0123456789ab"):
            applog.run_step(logger, "赛程", lambda: "赛程已保存 3 场")
            applog.run_step(logger, "伤病", lambda: "伤病 10 分钟前已更新，本次跳过")
            applog.run_step(logger, "效率", lambda: "球队效率未更新：HTTP 429")
            applog.run_step(logger, "整季", broken)
        done = [e["data"]["status"] for e in self.entries() if e["event"] == "task.step" and e["data"]["status"] != "start"]
        self.assertEqual(done, ["done", "skipped", "failed", "failed"])
        crash = self.entries()[-1]
        self.assertEqual(crash["error"]["type"], "KeyError")
        self.assertTrue(crash["error"]["where"][0].startswith("core/applog.py:"))
        self.assertNotIn("raw-secret-3", self.raw_text())

    def test_background_thread_gets_own_id_and_parent(self):
        def step(*_):
            applog.event(logger, "task.step", "子步骤")

        with patch.object(board.schedule, "log_refresh", side_effect=step), patch.object(board.injuries, "log_refresh"), \
             patch.object(board.team_stats, "log_refresh"), patch.object(board.season, "log_refresh"):
            with applog.trace("abcdefabcdef"):
                board.start_extras(["e"]).join(5)
        rows = [e for e in self.entries() if e["event"] in {"task.start", "task.step", "task.end"}]
        self.assertEqual([e["event"] for e in rows], ["task.start", "task.step", "task.end"])
        self.assertTrue(all(e["trace_id"].startswith("task-") and e["parent"] == "abcdefabcdef" for e in rows))

    def test_dunkapi_failure_logs_type_without_key(self):
        def handler(_):
            raise httpx.ConnectTimeout("timed out with x-api-key dk-secret-9")

        ledger = self.root / "ledger.json"
        with patch("app.integrations.dunkapi.httpx.Client",
                   side_effect=lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw)):
            with self.assertRaises(dunkapi.DunkError):
                dunkapi.call("get_injuries", {}, "dk-secret-9", ledger)
        call = [e for e in self.entries() if e["event"] == "ext.call"][-1]
        self.assertEqual((call["data"]["provider"], call["data"]["status"], call["error"]["type"]), ("DunkAPI", "error", "httpx.ConnectTimeout"))
        self.assertNotIn("dk-secret-9", self.raw_text())


class RouteTests(FileLogCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app()
        self.app.dependency_overrides[get_settings] = settings
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        loaders = {"read_board": build_board(snapshot()), "load_history": [], "load_schedule": None,
                   "load_injuries": None, "load_season": None, "load_team_stats": None}
        for name, value in loaders.items():
            patcher = patch(f"app.services.chat.service.{name}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_trace_header_and_request_entry(self):
        response = await self.client.get("/api/v1/health")
        trace_id = response.headers["x-trace-id"]
        entry = [e for e in self.entries() if e["event"] == "http.request"][-1]
        self.assertEqual((entry["trace_id"], entry["data"]["path"], entry["data"]["status"]), (trace_id, "/api/v1/health", 200))
        await self.client.get("/api/v1/logs/traces")
        self.assertFalse(any((e.get("data") or {}).get("path", "").startswith("/api/v1/logs") for e in self.entries()))

    async def test_chat_stream_records_stages_and_archive(self):
        async def provider(*_):
            yield TextDelta("分析")
            yield ChatResponse(reply="分析", model="test-model")

        with patch("app.services.chat.service.stream_model", side_effect=provider):
            response = await self.client.post("/api/v1/chat/stream", json={"messages": [{"role": "user", "content": "凯尔特人怎么看"}]})
        trace_id = response.headers["x-trace-id"]
        events = [e["event"] for e in self.entries() if e["trace_id"] == trace_id]
        self.assertEqual(events, ["chat.context", "chat.end", "http.request"])
        archive = json.loads((self.root / "traces" / f"{trace_id}.json").read_text(encoding="utf-8"))
        self.assertEqual((archive["outcome"], archive["reply"], archive["reply_complete"]), ("done", "分析", True))
        self.assertEqual([card["away"] for card in archive["analysis_cards"].values()], ["Boston Celtics"])
        context = next(e for e in self.entries() if e["trace_id"] == trace_id and e["event"] == "chat.context")
        self.assertEqual(context["data"]["analysis"]["cards"], 1)
        self.assertIn("analysis_ms", context["data"]["analysis"])
        self.assertIn("<saved_odds_snapshot>", archive["instructions"])
        self.assertEqual(archive["request"]["messages"][0]["content"], "凯尔特人怎么看")
        self.assertNotIn("test-secret-value", self.raw_text())
        self.assertNotIn("extra-secret-value", self.raw_text())

        detail = (await self.client.get(f"/api/v1/logs/traces/{trace_id}")).json()
        self.assertEqual((detail["summary"]["kind"], detail["summary"]["status"]), ("chat", "ok"))
        self.assertEqual(detail["archive"]["outcome"], "done")

    async def test_chat_disconnect_is_recorded(self):
        async def provider(*_):
            yield TextDelta("半截")
            yield TextDelta("没传完")

        request = ChatRequest(messages=[{"role": "user", "content": "测试"}])
        with patch("app.services.chat.service.stream_model", side_effect=provider), applog.trace("0000aaaa1111"):
            stream = await chat_service.prepare_stream(request, settings())
            first = await anext(stream)
            if first.startswith("event: analysis"):  # 测试快照有比赛时先发分析卡
                await anext(stream)
            await stream.aclose()  # 网页关闭时 Starlette 会这样关掉生成器
        end = [e for e in self.entries() if e["event"] == "chat.end"][-1]
        self.assertEqual((end["level"], end["data"]["outcome"]), ("WARNING", "disconnected"))
        archive = json.loads((self.root / "traces" / "0000aaaa1111.json").read_text(encoding="utf-8"))
        self.assertEqual((archive["reply"], archive["reply_complete"]), ("半截", False))
        self.assertFalse(chat_service.busy())

    async def test_rejected_chat_is_listed_as_error(self):
        response = await self.client.post("/api/v1/chat/stream", json={
            "messages": [{"role": "user", "content": "测试"}], "odds_selection": {"event_id": "missing", "bookmaker_key": "a"}})
        self.assertEqual(response.status_code, 409)
        listing = (await self.client.get("/api/v1/logs/traces", params={"problems_only": "true", "kind": "chat"})).json()
        self.assertEqual(listing["items"][0]["trace_id"], response.headers["x-trace-id"])
        self.assertEqual((listing["items"][0]["status"], listing["items"][0]["http_status"]), ("error", 409))

    async def test_model_error_in_stream_marks_trace_failed(self):
        async def provider(*_):
            yield TextDelta("半截")
            raise ModelError("模型回答失败或被截断", 502)

        with patch("app.services.chat.service.stream_model", side_effect=provider):
            response = await self.client.post("/api/v1/chat/stream", json={"messages": [{"role": "user", "content": "测试"}]})
        summary = (await self.client.get(f"/api/v1/logs/traces/{response.headers['x-trace-id']}")).json()["summary"]
        self.assertEqual((summary["status"], summary["http_status"]), ("error", 200))
        self.assertIn("模型回答失败", summary["headline"])

    async def test_only_loopback_can_read(self):
        remote = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=("10.0.0.5", 50000)), base_url="http://test")
        self.addAsyncCleanup(remote.aclose)
        self.assertEqual((await remote.get("/api/v1/logs/traces")).status_code, 403)
        self.assertEqual((await self.client.get("/api/v1/logs/traces/not-an-id")).status_code, 404)


class ReaderTests(unittest.TestCase):
    def write(self, folder: Path, day: str, rows: list[dict]) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"app-{day}.jsonl").open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.write("不是 JSON 的坏行\n")

    def test_grouping_children_filters_and_paging(self):
        folder = Path(tempfile.mkdtemp()) / "logs"
        http = lambda tid, ts, path, status: {"ts": ts, "level": "INFO" if status < 400 else "WARNING", "trace_id": tid, "event": "http.request",
                                               "msg": "", "data": {"method": "POST", "path": path, "status": status, "ms": 5, "outcome": "done"}}
        self.write(folder, "2026-11-15", [
            http("aaaaaaaaaaaa", "2026-11-15T10:00:00.000+08:00", "/api/v1/odds/refresh", 200),
            {"ts": "2026-11-15T10:00:01.000+08:00", "level": "INFO", "trace_id": "task-bbbbbbbbbbbb", "parent": "aaaaaaaaaaaa", "event": "task.start", "msg": ""},
            {"ts": "2026-11-15T10:00:02.000+08:00", "level": "WARNING", "trace_id": "task-bbbbbbbbbbbb", "parent": "aaaaaaaaaaaa", "event": "task.step", "msg": "伤病未更新"},
            {"ts": "2026-11-15T10:00:03.000+08:00", "level": "INFO", "trace_id": "task-bbbbbbbbbbbb", "parent": "aaaaaaaaaaaa", "event": "task.end", "msg": "", "data": {"ms": 2000}},
            http("cccccccccccc", "2026-11-15T11:00:00.000+08:00", "/api/v1/health", 200),
        ])
        self.write(folder, "2026-11-14", [http("dddddddddddd", "2026-11-14T09:00:00.000+08:00", "/api/v1/chat/stream", 409)])
        today = date(2026, 11, 15)
        listing = reader.list_traces(days=1, root=folder, today=today)
        self.assertEqual([s["trace_id"] for s in listing["items"]], ["cccccccccccc", "task-bbbbbbbbbbbb", "aaaaaaaaaaaa"])
        refresh = listing["items"][2]
        self.assertEqual((refresh["kind"], refresh["children"]), ("odds_refresh", ["task-bbbbbbbbbbbb"]))
        task = listing["items"][1]
        self.assertEqual((task["kind"], task["status"], task["duration_ms"], task["headline"]), ("task", "warning", 2000, "伤病未更新"))
        two_days = reader.list_traces(days=2, problems_only=True, root=folder, today=today)
        self.assertEqual([s["trace_id"] for s in two_days["items"]], ["task-bbbbbbbbbbbb", "dddddddddddd"])
        paged = reader.list_traces(days=2, limit=2, root=folder, today=today)
        self.assertEqual((len(paged["items"]), paged["next_cursor"], paged["total"]), (2, 2, 4))
        detail = reader.get_trace("aaaaaaaaaaaa", root=folder, archive_root=folder, today=today)
        self.assertEqual([c["trace_id"] for c in detail["children"]], ["task-bbbbbbbbbbbb"])
        self.assertIsNone(reader.get_trace("../../etc", root=folder, today=today))
        # 结尾带换行的编号以前能通过 match 加 $ 的校验
        self.assertIsNone(reader.get_trace("aaaaaaaaaaaa\n", root=folder, today=today))
        self.assertIsNone(reader.read_archive("aaaaaaaaaaaa\n", root=folder))

    def test_running_chat_is_listed_as_chat(self):
        # 流式回答传完之前还没有 http.request 这一条
        folder = Path(tempfile.mkdtemp()) / "logs"
        self.write(folder, "2026-11-15", [
            {"ts": "2026-11-15T10:00:00.000+08:00", "level": "INFO", "trace_id": "eeeeeeeeeeee", "event": "chat.context", "msg": ""},
            {"ts": "2026-11-15T10:00:01.000+08:00", "level": "INFO", "trace_id": "eeeeeeeeeeee", "event": "llm.start", "msg": ""},
        ])
        listing = reader.list_traces(days=1, kind="chat", root=folder, today=date(2026, 11, 15))
        self.assertEqual([(s["trace_id"], s["status"]) for s in listing["items"]], [("eeeeeeeeeeee", "running")])

    def test_detail_stops_reading_older_days_once_found(self):
        folder = Path(tempfile.mkdtemp()) / "logs"
        row = lambda tid, ts: {"ts": ts, "level": "INFO", "trace_id": tid, "event": "chat.context", "msg": ""}
        # 调用跨过午夜：前一天开始，当天结束；再往前两天的文件与它无关
        self.write(folder, "2026-11-15", [row("ffffffffffff", "2026-11-15T00:00:01.000+08:00")])
        self.write(folder, "2026-11-14", [row("ffffffffffff", "2026-11-14T23:59:59.000+08:00")])
        self.write(folder, "2026-11-13", [row("111111111111", "2026-11-13T10:00:00.000+08:00")])
        self.write(folder, "2026-11-12", [row("222222222222", "2026-11-12T10:00:00.000+08:00")])
        read = []
        original = reader._read_file
        with patch.object(reader, "_read_file", side_effect=lambda path: read.append(path.name) or original(path)):
            detail = reader.get_trace("ffffffffffff", root=folder, archive_root=folder, today=date(2026, 11, 15))
        self.assertEqual(detail["summary"]["steps"], 2)
        self.assertEqual(read, ["app-2026-11-15.jsonl", "app-2026-11-14.jsonl", "app-2026-11-13.jsonl"])


if __name__ == "__main__":
    unittest.main()
