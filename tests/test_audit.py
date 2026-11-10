import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import audit


class AuditLoggerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.config = audit.AuditConfig(
            enabled=True,
            directory=self.tempdir.name,
            filename="audit.jsonl",
            retention_days=0,
            max_field_chars=20000,
            capture_output=True,
            capture_denied=False,
            redact=True,
        )
        self.logger = audit.AuditLogger(
            "test-service",
            config=self.config,
            model="deepseek-chat",
            system_prompt="system prompt",
            tools=["execute_ssh"],
        )
        audit.bind_request(chat_id=10, user_id=20, update_id=30, correlation_id="corr-1")
        self.addCleanup(audit.unbind_request)

    @property
    def log_path(self):
        return Path(self.tempdir.name) / "audit.jsonl"

    def events(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def test_jsonl_events_have_common_fields(self):
        self.logger.message_received("hello", 100)
        self.logger.llm_request(1, "deepseek-chat", 2, ["execute_ssh"])
        self.logger.response_sent("done", 101)

        events = self.events()
        self.assertEqual([event["event"] for event in events], [
            "message_received",
            "llm_request",
            "response_sent",
        ])
        for event in events:
            self.assertTrue(event["ts"].endswith("Z"))
            self.assertEqual(event["service"], "test-service")
            self.assertEqual(event["correlation_id"], "corr-1")
            self.assertEqual(event["chat_id"], 10)
            self.assertEqual(event["user_id"], 20)
            self.assertEqual(event["update_id"], 30)

    def test_correlation_and_tool_context(self):
        with audit.tool_context(tool="execute_ssh", tool_call_id="call-1", step=2):
            self.logger.command_result(
                command="df -h",
                target="proxmox",
                resolved_target="192.168.30.250",
                transport="ssh",
                executed_argv=["ssh", "root@192.168.30.250", "df -h"],
                exit_code=0,
                duration_ms=5,
                stdout="ok",
                stderr="",
            )

        event = [item for item in self.events() if item["event"] == "command_result"][0]
        self.assertEqual(event["correlation_id"], "corr-1")
        self.assertEqual(event["tool"], "execute_ssh")
        self.assertEqual(event["tool_call_id"], "call-1")
        self.assertEqual(event["step"], 2)
        self.assertFalse(event["timed_out"])

    def test_redacts_secrets_recursively(self):
        private_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----"
        self.logger.message_received(
            f"token sk-abcdefgh12345678 password=segredo {private_key}",
            1,
        )
        self.logger.tool_call(
            tool="pvesh_query",
            arguments={"password": "segredo", "api_key": "sk-abcdefgh12345678"},
            command="echo DEEPSEEK_API_KEY=sk-abcdefgh12345678",
        )

        raw = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn("sk-abcdefgh12345678", raw)
        self.assertNotIn("segredo", raw)
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", raw)
        self.assertIn("[REDACTED]", raw)

    def test_preserves_usage_token_counts(self):
        self.logger.llm_response(
            1,
            "ok",
            "stop",
            [],
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        )
        event = [item for item in self.events() if item["event"] == "llm_response"][0]
        self.assertEqual(event["usage"]["prompt_tokens"], 10)
        self.assertEqual(event["usage"]["total_tokens"], 12)

    def test_sensitive_file_output_is_redacted(self):
        self.logger.command_result(
            command="cat /root/.ssh/id_ed25519",
            target="proxmox",
            resolved_target="192.168.30.250",
            transport="ssh",
            exit_code=0,
            stdout="PRIVATE KEY CONTENT",
            stderr="",
        )
        event = [item for item in self.events() if item["event"] == "command_result"][0]
        self.assertTrue(event["sensitive_path"])
        self.assertEqual(event["stdout"], "[REDACTED: sensitive file]")

    def test_truncates_long_fields(self):
        self.config.max_field_chars = 10
        self.logger.message_received("x" * 50, 1)
        event = [item for item in self.events() if item["event"] == "message_received"][0]
        self.assertEqual(event["text"], "x" * 10 + "...[truncated]")

    def test_capture_output_disabled(self):
        self.config.capture_output = False
        self.logger.command_result(command="df -h", exit_code=0, stdout="out", stderr="err")
        event = [item for item in self.events() if item["event"] == "command_result"][0]
        self.assertIsNone(event["stdout"])
        self.assertIsNone(event["stderr"])

    def test_disabled_logger_does_not_create_file(self):
        config = audit.AuditConfig(enabled=False, directory=self.tempdir.name)
        logger = audit.AuditLogger("disabled", config=config)
        logger.message_received("hello", 1)
        self.assertFalse(self.log_path.exists())

    def test_denied_text_is_hidden_by_default(self):
        self.logger.access_denied("mensagem sensivel")
        event = [item for item in self.events() if item["event"] == "access_denied"][0]
        self.assertIsNone(event["text"])

        self.config.capture_denied = True
        self.logger.access_denied("mensagem visivel")
        events = [item for item in self.events() if item["event"] == "access_denied"]
        self.assertEqual(events[-1]["text"], "mensagem visivel")

    def test_rotates_file_from_previous_day(self):
        self.logger.message_received("first", 1)
        old_time = time.time() - (2 * 86400)
        os.utime(self.log_path, (old_time, old_time))

        rotated_logger = audit.AuditLogger("test-service", config=self.config)
        rotated_logger.message_received("second", 2)

        rotated = [item for item in Path(self.tempdir.name).iterdir() if item.name != "audit.jsonl"]
        self.assertTrue(rotated)
        events = self.events()
        self.assertEqual(events[-1]["text"], "second")

    def test_retention_removes_old_rotated_files(self):
        old_file = Path(self.tempdir.name) / "audit.jsonl.2020-01-01"
        old_file.write_text("{}\n", encoding="utf-8")
        config = audit.AuditConfig(
            enabled=True,
            directory=self.tempdir.name,
            retention_days=1,
        )
        audit.AuditLogger("retention", config=config)
        self.assertFalse(old_file.exists())

    def test_write_failure_does_not_raise(self):
        blocked = Path(self.tempdir.name) / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        config = audit.AuditConfig(enabled=True, directory=str(blocked))
        logger = audit.AuditLogger("failure", config=config)
        logger.message_received("hello", 1)
        self.assertFalse(blocked.is_dir())

    def test_sanitize_arguments_removes_write_content(self):
        sanitized = audit.sanitize_arguments(
            "write_file",
            {"target": "proxmox", "file_path": "/tmp/a", "content": "secret"},
        )
        self.assertNotIn("content", sanitized)
        self.assertEqual(sanitized["content_size"], 6)
        self.assertEqual(sanitized["content_sha256"], audit._sha256("secret"))

    def test_serialize_tool_calls_sanitizes_write_content(self):
        tool_call = SimpleNamespace(
            id="call-1",
            function=SimpleNamespace(
                name="write_file",
                arguments='{"target":"proxmox","file_path":"/tmp/a","content":"secret"}',
            ),
        )
        serialized = audit.serialize_tool_calls([tool_call])
        self.assertNotIn("secret", serialized[0]["arguments"])
        self.assertIn("content_size", serialized[0]["arguments"])

    def test_concurrent_writes_keep_jsonl_valid(self):
        def worker(index):
            audit.bind_request(
                chat_id=1,
                user_id=2,
                update_id=index,
                correlation_id=f"concurrent-{index}",
            )
            try:
                self.logger.message_received(f"message-{index}", index)
            finally:
                audit.unbind_request()

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(worker, range(20)))

        events = self.events()
        self.assertEqual(len(events), 20)
        self.assertEqual(len({event["correlation_id"] for event in events}), 20)

    def test_sensitive_path_detection(self):
        self.assertTrue(audit.is_sensitive_path("/root/.ssh/id_ed25519"))
        self.assertTrue(audit.is_sensitive_path("/app/.env"))
        self.assertTrue(audit.is_sensitive_path("/etc/ssl/private/server.pem"))
        self.assertTrue(audit.command_has_sensitive_path("cat /app/.env"))
        self.assertFalse(audit.command_has_sensitive_path("df -h"))


class AuditWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.config = audit.AuditConfig(enabled=True, directory=self.tempdir.name)
        audit.setup("wrapper-service", config=self.config)
        self.addCleanup(self._reset)

    def _reset(self):
        audit.unbind_request()
        audit._logger = None

    def test_wrappers_write_events(self):
        audit.bind_request(chat_id=1, user_id=2, update_id=3, correlation_id="wrapper-corr")
        audit.log_message_received("hello", 4)
        audit.log_response_sent("done", 5)
        audit.unbind_request()

        path = Path(self.tempdir.name) / "audit.jsonl"
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events[0]["event"], "session_start")
        self.assertEqual(events[1]["correlation_id"], "wrapper-corr")
        self.assertEqual(events[2]["text"], "done")


if __name__ == "__main__":
    unittest.main()
