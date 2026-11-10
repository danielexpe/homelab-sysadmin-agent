import contextlib
import contextvars
import hashlib
import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

_SECRET_KEY_RE = re.compile(
    r"(^token$|^api_?key$|password|passwd|secret|authorization|private_key|access_token|refresh_token|bot_token)",
    re.IGNORECASE,
)

_SECRET_PATTERNS = [
    (
        re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----", re.DOTALL),
        "[REDACTED]",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"), "[REDACTED]"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|API_KEY|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)\s*=\s*(\S+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r'(?i)("(?:password|passwd|token|secret|api[_-]?key)"\s*:\s*)"[^"]*"'
        ),
        r'\1"[REDACTED]"',
    ),
]

_SENSITIVE_SUFFIXES = (".pem", ".key", ".crt", ".cer", ".p12", ".pfx")
_SENSITIVE_NAMES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "authorized_keys", "known_hosts")
_SENSITIVE_DIRS = ("/root/.ssh/", "/etc/ssl/private/")

_request_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "audit_request_context", default={}
)
_tool_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "audit_tool_context", default={}
)

_logger: "AuditLogger | None" = None


@dataclass
class AuditConfig:
    enabled: bool = True
    directory: str = "/app/logs"
    filename: str = "audit.jsonl"
    retention_days: int = 0
    max_field_chars: int = 20000
    capture_output: bool = True
    capture_denied: bool = False
    redact: bool = True

    @classmethod
    def from_env(cls) -> "AuditConfig":
        return cls(
            enabled=_env_bool("AUDIT_LOG_ENABLED", True),
            directory=os.getenv("AUDIT_LOG_DIR", "/app/logs"),
            filename=os.getenv("AUDIT_LOG_FILE", "audit.jsonl"),
            retention_days=_env_int("AUDIT_LOG_RETENTION_DAYS", 0),
            max_field_chars=_env_int("AUDIT_LOG_MAX_FIELD_CHARS", 20000),
            capture_output=_env_bool("AUDIT_LOG_CAPTURE_OUTPUT", True),
            capture_denied=_env_bool("AUDIT_LOG_CAPTURE_DENIED", False),
            redact=_env_bool("AUDIT_LOG_REDACT", True),
        )


def _warn(message: str) -> None:
    print(f"[audit] {message}", file=sys.stderr)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    _warn(f"valor invalido para {name}: {raw!r}; usando {default}")
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        _warn(f"valor invalido para {name}: {raw!r}; usando {default}")
        return default


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    return f"{ts.strftime('%Y-%m-%dT%H:%M:%S')}.{ts.microsecond // 1000:03d}Z"


def _sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _truncate(value: Any, max_chars: int) -> Any:
    if value is None or not isinstance(value, str):
        return value
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}...[truncated]"


def redact(value: Any) -> Any:
    try:
        return _redact(value)
    except Exception:
        return "[REDACTED]"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern, replacement in _SECRET_PATTERNS:
            result = pattern.sub(replacement, result)
        return result
    return value


def is_sensitive_path(path: Any) -> bool:
    if path is None:
        return False
    cleaned = str(path).strip().strip("'\"").lower()
    if not cleaned:
        return False
    basename = cleaned.rsplit("/", 1)[-1]
    if basename.endswith(_SENSITIVE_SUFFIXES):
        return True
    if basename in _SENSITIVE_NAMES:
        return True
    if basename == ".env" or basename.startswith(".env."):
        return True
    return any(directory in cleaned for directory in _SENSITIVE_DIRS)


def command_has_sensitive_path(command: Any) -> bool:
    if command is None:
        return False
    text = str(command)
    if is_sensitive_path(text):
        return True
    for token in re.split(r"[\s'\"`;|&<>()]+", text):
        if token and is_sensitive_path(token):
            return True
    return False


def sanitize_arguments(tool: str | None, arguments: Any) -> Any:
    parsed = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            parsed = arguments
    if isinstance(parsed, dict) and tool == "write_file" and "content" in parsed:
        content = parsed.get("content") or ""
        sanitized = {key: value for key, value in parsed.items() if key != "content"}
        sanitized["content_size"] = len(str(content))
        sanitized["content_sha256"] = _sha256(str(content))
        return sanitized
    return parsed


def serialize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not tool_calls:
        return []
    result = []
    for tool_call in tool_calls:
        function = getattr(tool_call, "function", None)
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", "{}")
        arguments = sanitize_arguments(name, raw_arguments)
        if isinstance(arguments, str):
            serialized = arguments
        else:
            serialized = json.dumps(arguments, ensure_ascii=False)
        result.append(
            {
                "id": getattr(tool_call, "id", None),
                "name": name,
                "arguments": serialized,
            }
        )
    return result


def usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


class AuditLogger:
    def __init__(
        self,
        service: str,
        config: AuditConfig | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        tools: Iterable[str] | None = None,
    ):
        self.service = service
        self.config = config or AuditConfig.from_env()
        self.model = model
        self.system_prompt = system_prompt
        self.tools = list(tools or [])
        self._lock = threading.Lock()
        self._path: Path | None = None
        self._active_date = None
        self._ready = False
        if self.config.enabled:
            with self._lock:
                self._prepare_locked()

    @property
    def path(self) -> Path | None:
        return self._path

    def _prepare_locked(self) -> bool:
        try:
            directory = Path(self.config.directory).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            self._path = directory / self.config.filename
            today = _utc_now().date()
            if self._path.exists() and not self._path.is_file():
                raise OSError(f"{self._path} nao e um arquivo")
            if self._path.exists():
                modified = datetime.fromtimestamp(self._path.stat().st_mtime, timezone.utc).date()
                if modified != today:
                    self._rotate_to(modified)
            self._active_date = today
            self._ready = True
            self._apply_retention_locked()
            return True
        except Exception as exc:
            self._ready = False
            _warn(f"nao foi possivel preparar o log de auditoria: {exc}")
            return False

    def _rotate_to(self, date_value) -> None:
        if self._path is None or not self._path.exists():
            return
        target = self._path.with_name(f"{self._path.name}.{date_value.isoformat()}")
        counter = 1
        while target.exists():
            target = self._path.with_name(
                f"{self._path.name}.{date_value.isoformat()}.{counter}"
            )
            counter += 1
        os.replace(self._path, target)

    def _apply_retention_locked(self) -> None:
        if self.config.retention_days <= 0 or self._path is None:
            return
        cutoff = _utc_now().date() - timedelta(days=self.config.retention_days)
        prefix = f"{self._path.name}."
        for candidate in self._path.parent.glob(f"{self._path.name}.*"):
            suffix = candidate.name[len(prefix) :]
            try:
                file_date = datetime.strptime(suffix[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    candidate.unlink()
                except OSError as exc:
                    _warn(f"nao foi possivel remover {candidate}: {exc}")

    def _rotate_if_needed_locked(self) -> None:
        today = _utc_now().date()
        if self._active_date == today:
            return
        if self._path is not None and self._path.exists() and self._active_date is not None:
            self._rotate_to(self._active_date)
        self._active_date = today
        self._apply_retention_locked()

    def _write_locked(self, payload: dict[str, Any]) -> None:
        if not self._ready and not self._prepare_locked():
            return
        self._rotate_if_needed_locked()
        if self._path is None:
            return
        try:
            line = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), default=str
            )
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
                handle.flush()
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        except Exception as exc:
            _warn(f"falha ao gravar evento de auditoria: {exc}")

    def emit(self, event: str, **fields: Any) -> None:
        if not self.config.enabled:
            return
        request = _request_context.get() or {}
        timestamp = _utc_now()
        payload = {
            "ts": _iso(timestamp),
            "event": event,
            "service": self.service,
            "correlation_id": request.get("correlation_id"),
            "chat_id": request.get("chat_id"),
            "user_id": request.get("user_id"),
            "update_id": request.get("update_id"),
        }
        payload.update(fields)
        if self.config.redact:
            payload = redact(payload)
        with self._lock:
            self._write_locked(payload)

    def session_start(self) -> None:
        self.emit(
            "session_start",
            pid=os.getpid(),
            model=self.model,
            system_prompt=self.system_prompt,
            system_prompt_sha256=_sha256(self.system_prompt),
            tools=self.tools,
        )

    def access_denied(self, text: str | None = None) -> None:
        self.emit(
            "access_denied",
            text=text if self.config.capture_denied else None,
        )

    def message_received(self, text: str, message_id: int | None = None) -> None:
        self.emit(
            "message_received",
            text=_truncate(text, self.config.max_field_chars),
            message_id=message_id,
        )

    def llm_request(
        self,
        step: int,
        model: str,
        message_count: int,
        tool_names: Iterable[str],
    ) -> None:
        self.emit(
            "llm_request",
            step=step,
            model=model,
            message_count=message_count,
            tool_names=list(tool_names),
        )

    def llm_response(
        self,
        step: int,
        content: str | None,
        finish_reason: str | None,
        tool_calls: list[dict[str, Any]] | None,
        usage: dict[str, Any] | None,
        model: str | None = None,
    ) -> None:
        self.emit(
            "llm_response",
            step=step,
            model=model or self.model,
            content=_truncate(content, self.config.max_field_chars),
            finish_reason=finish_reason,
            tool_calls=tool_calls or [],
            usage=usage,
        )

    def tool_call(
        self,
        tool: str,
        tool_call_id: str | None = None,
        step: int | None = None,
        arguments: Any = None,
        target: str | None = None,
        resolved_target: str | None = None,
        command: str | None = None,
        transport: str | None = None,
        sensitive_path: bool | None = None,
    ) -> None:
        if sensitive_path is None:
            sensitive_path = command_has_sensitive_path(command)
        self.emit(
            "tool_call",
            step=step,
            tool_call_id=tool_call_id,
            tool=tool,
            target=target,
            resolved_target=resolved_target,
            command=command,
            transport=transport,
            arguments=sanitize_arguments(tool, arguments),
            sensitive_path=sensitive_path,
        )

    def command_result(
        self,
        tool: str | None = None,
        tool_call_id: str | None = None,
        step: int | None = None,
        target: str | None = None,
        resolved_target: str | None = None,
        transport: str | None = None,
        command: str | None = None,
        executed_argv: list[str] | None = None,
        exit_code: int | None = None,
        timed_out: bool = False,
        duration_ms: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        error: str | None = None,
        sensitive_path: bool | None = None,
    ) -> None:
        context = get_tool_context()
        tool = tool if tool is not None else context.get("tool")
        tool_call_id = (
            tool_call_id if tool_call_id is not None else context.get("tool_call_id")
        )
        step = step if step is not None else context.get("step")
        target = target if target is not None else context.get("target")
        resolved_target = (
            resolved_target
            if resolved_target is not None
            else context.get("resolved_target")
        )
        transport = transport if transport is not None else context.get("transport")
        command = command if command is not None else context.get("command")
        if sensitive_path is None:
            sensitive_path = bool(context.get("sensitive_path")) or command_has_sensitive_path(
                command
            )
        if not self.config.capture_output:
            stdout = None
            stderr = None
        stdout = _truncate(stdout, self.config.max_field_chars)
        stderr = _truncate(stderr, self.config.max_field_chars)
        if sensitive_path and stdout is not None:
            stdout = "[REDACTED: sensitive file]"
        self.emit(
            "command_result",
            step=step,
            tool_call_id=tool_call_id,
            tool=tool,
            target=target,
            resolved_target=resolved_target,
            transport=transport,
            command=command,
            executed_argv=executed_argv,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            error=error,
            sensitive_path=sensitive_path,
        )

    def response_sent(self, text: str, message_id: int | None = None) -> None:
        self.emit(
            "response_sent",
            text=_truncate(text, self.config.max_field_chars),
            message_id=message_id,
        )

    def error(self, stage: str, error: Exception | str, step: int | None = None) -> None:
        if step is None:
            step = get_tool_context().get("step")
        self.emit(
            "error",
            step=step,
            stage=stage,
            error_type=type(error).__name__ if isinstance(error, Exception) else "Error",
            message=str(error),
        )


def setup(
    service: str,
    model: str | None = None,
    system_prompt: str | None = None,
    tools: Iterable[str] | None = None,
    config: AuditConfig | None = None,
) -> AuditLogger:
    global _logger
    _logger = AuditLogger(
        service,
        config=config,
        model=model,
        system_prompt=system_prompt,
        tools=tools,
    )
    _logger.session_start()
    return _logger


def get_logger() -> AuditLogger | None:
    return _logger


def bind_request(
    update: Any = None,
    correlation_id: str | None = None,
    chat_id: int | None = None,
    user_id: int | None = None,
    update_id: int | None = None,
) -> str:
    if update is not None:
        effective_chat = getattr(update, "effective_chat", None)
        effective_user = getattr(update, "effective_user", None)
        if chat_id is None:
            chat_id = getattr(effective_chat, "id", None)
        if user_id is None:
            user_id = getattr(effective_user, "id", None)
        if update_id is None:
            update_id = getattr(update, "update_id", None)
    correlation_id = correlation_id or str(uuid.uuid4())
    _request_context.set(
        {
            "correlation_id": correlation_id,
            "chat_id": chat_id,
            "user_id": user_id,
            "update_id": update_id,
        }
    )
    return correlation_id


def unbind_request() -> None:
    _request_context.set({})


def get_correlation_id() -> str | None:
    return (_request_context.get() or {}).get("correlation_id")


@contextlib.contextmanager
def tool_context(**fields: Any):
    token = _tool_context.set(dict(fields))
    try:
        yield
    finally:
        _tool_context.reset(token)


def get_tool_context() -> dict[str, Any]:
    return dict(_tool_context.get() or {})


def _call(method: str, *args: Any, **kwargs: Any) -> Any:
    logger = _logger
    if logger is None:
        return None
    return getattr(logger, method)(*args, **kwargs)


def log_access_denied(text: str | None = None) -> None:
    _call("access_denied", text)


def log_message_received(text: str, message_id: int | None = None) -> None:
    _call("message_received", text, message_id)


def log_llm_request(
    step: int, model: str, message_count: int, tool_names: Iterable[str]
) -> None:
    _call("llm_request", step, model, message_count, tool_names)


def log_llm_response(
    step: int,
    content: str | None,
    finish_reason: str | None,
    tool_calls: list[dict[str, Any]] | None,
    usage: dict[str, Any] | None,
    model: str | None = None,
) -> None:
    _call("llm_response", step, content, finish_reason, tool_calls, usage, model)


def log_tool_call(*args: Any, **kwargs: Any) -> None:
    _call("tool_call", *args, **kwargs)


def log_command_result(*args: Any, **kwargs: Any) -> None:
    _call("command_result", *args, **kwargs)


def log_response_sent(text: str, message_id: int | None = None) -> None:
    _call("response_sent", text, message_id)


def log_error(stage: str, error: Exception | str, step: int | None = None) -> None:
    _call("error", stage, error, step)
