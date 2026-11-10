import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


class _FakeFilter:
    def __and__(self, other):
        return self

    def __invert__(self):
        return self


def _install_stubs():
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv

    telegram = types.ModuleType("telegram")
    telegram.Update = object
    telegram_request = types.ModuleType("telegram.request")
    telegram_request.HTTPXRequest = object
    telegram.request = telegram_request
    sys.modules["telegram"] = telegram
    sys.modules["telegram.request"] = telegram_request

    telegram_ext = types.ModuleType("telegram.ext")
    telegram_ext.ApplicationBuilder = object
    telegram_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    telegram_ext.MessageHandler = object
    telegram_ext.filters = _FakeFilter()
    telegram.ext = telegram_ext
    sys.modules["telegram.ext"] = telegram_ext

    openai_module = types.ModuleType("openai")

    class FakeOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=MagicMock())
            )

    openai_module.OpenAI = FakeOpenAI
    sys.modules["openai"] = openai_module


_install_stubs()

import audit
import bot
import sysadmin_agent


class FakeStatusMessage:
    def __init__(self, message_id=99):
        self.message_id = message_id
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append(text)


class FakeMessage:
    def __init__(self, text, message_id=10):
        self.text = text
        self.message_id = message_id
        self.replies = []
        self.status = FakeStatusMessage()

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return self.status


class FakeUpdate:
    def __init__(self, text, user_id, message_id=10, update_id=20):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=1)
        self.update_id = update_id
        self.message = FakeMessage(text, message_id)


def _tool_call(name, arguments, call_id="call-1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def _response(message, finish_reason):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class AuditIntegrationTestCase(unittest.TestCase):
    service = "sysadmin-agent"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.config = audit.AuditConfig(enabled=True, directory=self.tempdir.name)
        audit.setup(self.service, config=self.config)
        self.addCleanup(self._reset)

    def _reset(self):
        audit.unbind_request()
        audit._logger = None

    @property
    def log_path(self):
        return Path(self.tempdir.name) / "audit.jsonl"

    def events(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]

    def event_names(self):
        return [event["event"] for event in self.events()]

    def request_events(self):
        return [event for event in self.events() if event["event"] != "session_start"]


class CommandLoggingTests(AuditIntegrationTestCase):
    def setUp(self):
        super().setUp()
        audit.bind_request(chat_id=1, user_id=2, update_id=3, correlation_id="cmd-corr")
        self.addCleanup(audit.unbind_request)

    def test_execute_ssh_logs_command_result(self):
        output = sysadmin_agent.execute_ssh("srv-bot", "printf hello")
        result = json.loads(output)

        self.assertEqual(result["exit_code"], 0)
        event = [item for item in self.events() if item["event"] == "command_result"][-1]
        self.assertEqual(event["correlation_id"], "cmd-corr")
        self.assertEqual(event["command"], "printf hello")
        self.assertEqual(event["executed_argv"], ["sh", "-c", "printf hello"])
        self.assertEqual(event["stdout"], "hello")
        self.assertFalse(event["timed_out"])

    def test_read_file_logs_sensitive_output_as_redacted(self):
        sensitive = Path(self.tempdir.name) / "id_ed25519"
        sensitive.write_text("PRIVATE KEY CONTENT", encoding="utf-8")

        output = sysadmin_agent.read_file("srv-bot", str(sensitive))
        self.assertIn("PRIVATE KEY CONTENT", output)

        event = [item for item in self.events() if item["event"] == "command_result"][-1]
        self.assertTrue(event["sensitive_path"])
        self.assertEqual(event["stdout"], "[REDACTED: sensitive file]")

    def test_write_file_logs_command_result(self):
        target = Path(self.tempdir.name) / "config.txt"
        output = sysadmin_agent.write_file("srv-bot", str(target), "hello")

        self.assertTrue(json.loads(output)["success"])
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")
        event = [item for item in self.events() if item["event"] == "command_result"][-1]
        self.assertEqual(event["tool"], "write_file")
        self.assertEqual(event["exit_code"], 0)

    def test_disk_usage_local_logs_command_result(self):
        bot.execute_disk_usage("local")
        event = [item for item in self.events() if item["event"] == "command_result"][-1]
        self.assertEqual(event["command"], "df -h")
        self.assertEqual(event["transport"], "local")
        self.assertEqual(event["exit_code"], 0)

    def test_pvesh_query_logs_final_command(self):
        fake_result = SimpleNamespace(returncode=0, stdout="{}", stderr="")
        with patch.object(sysadmin_agent.subprocess, "run", return_value=fake_result):
            with audit.tool_context(tool="pvesh_query", tool_call_id="call-2", step=1):
                sysadmin_agent.pvesh_query("get", "/cluster/resources")

        event = [item for item in self.events() if item["event"] == "command_result"][-1]
        self.assertEqual(event["tool"], "pvesh_query")
        self.assertEqual(event["command"], "pvesh get /cluster/resources --output-format json")
        self.assertEqual(event["exit_code"], 0)


class SysadminHandlerLoggingTests(AuditIntegrationTestCase):
    def test_handler_logs_full_flow(self):
        tool_message = SimpleNamespace(
            content="",
            tool_calls=[_tool_call("execute_ssh", {"target": "srv-bot", "command": "printf ok"})],
        )
        final_message = SimpleNamespace(content="Concluido.", tool_calls=None)
        create = MagicMock(
            side_effect=[
                _response(tool_message, "tool_calls"),
                _response(final_message, "stop"),
            ]
        )
        self.addCleanup(setattr, sysadmin_agent, "client", sysadmin_agent.client)
        sysadmin_agent.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        update = FakeUpdate("rode um comando", sysadmin_agent.ALLOWED_USER_ID)
        asyncio.run(sysadmin_agent.handle_message(update, None))

        names = self.event_names()
        for expected in [
            "message_received",
            "llm_request",
            "llm_response",
            "tool_call",
            "command_result",
            "response_sent",
        ]:
            self.assertIn(expected, names)

        events = self.request_events()
        correlation = events[0]["correlation_id"]
        self.assertIsNotNone(correlation)
        self.assertTrue(all(event["correlation_id"] == correlation for event in events))
        command = [item for item in events if item["event"] == "command_result"][0]
        self.assertEqual(command["command"], "printf ok")
        self.assertEqual(update.message.status.edits[-1], "Concluido.")

    def test_unauthorized_user_is_logged_and_ignored(self):
        create = MagicMock()
        self.addCleanup(setattr, sysadmin_agent, "client", sysadmin_agent.client)
        sysadmin_agent.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        update = FakeUpdate("nao autorizado", sysadmin_agent.ALLOWED_USER_ID + 1)
        asyncio.run(sysadmin_agent.handle_message(update, None))

        create.assert_not_called()
        self.assertEqual([event["event"] for event in self.request_events()], ["access_denied"])
        denied = self.request_events()[0]
        self.assertIsNone(denied["text"])


class BotHandlerLoggingTests(AuditIntegrationTestCase):
    service = "homelab-bot"

    def test_handler_logs_tool_and_command(self):
        tool_message = SimpleNamespace(
            content="",
            tool_calls=[_tool_call("check_disk_usage", {"target": "local"})],
        )
        create = MagicMock(return_value=_response(tool_message, "tool_calls"))
        self.addCleanup(setattr, bot, "client", bot.client)
        bot.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        update = FakeUpdate("use o disco", bot.ALLOWED_USER_ID)
        asyncio.run(bot.handle_message(update, None))

        names = self.event_names()
        for expected in [
            "message_received",
            "llm_request",
            "llm_response",
            "tool_call",
            "command_result",
            "response_sent",
        ]:
            self.assertIn(expected, names)

        command = [item for item in self.events() if item["event"] == "command_result"][0]
        self.assertEqual(command["command"], "df -h")
        self.assertEqual(command["target"], "local")

    def test_system_update_logs_each_target(self):
        fake_result = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(bot.subprocess, "run", return_value=fake_result):
            with audit.tool_context(tool="run_system_update", tool_call_id="call-3", step=1):
                result = bot.execute_apt_update("all")

        self.assertEqual(result.count("Sucesso"), 3)
        commands = [
            item for item in self.events() if item["event"] == "command_result"
        ]
        self.assertEqual([item["target"] for item in commands], ["100", "101", "102"])
        self.assertTrue(all(item["tool"] == "run_system_update" for item in commands))
        self.assertTrue(all(item["exit_code"] == 0 for item in commands))


if __name__ == "__main__":
    unittest.main()
