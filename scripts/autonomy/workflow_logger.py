#!/usr/bin/env python3
"""WorkflowLogger - エージェント実行ログの共通記録基盤.

目的:
- 実行ごとに `_logs/autonomy/{agent}/{YYYYMMDD}/{run_id}.jsonl` を生成
- JSONL 1行1イベントで追跡可能性(run_id/trace_id/span/event_seq)を担保
- フェーズ/ツール/検証/主張(CLAIM)を分離して記録
- 長い本文は artifact 化し、イベントには参照情報のみ残す
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = "1.0"
WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

_REDACTED = "***REDACTED***"
_SECRET_KEY_PATTERN = re.compile(
    r"(password|passwd|passphrase|token|api[_-]?key|secret|authorization|cookie|credential|bearer)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*"),
]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: Optional[datetime] = None) -> str:
    return (ts or _now_utc()).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._")
    return normalized or "unknown"


def _preview(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    if is_dataclass(value):
        return asdict(value)
    return str(value)


def _json_dumps(value: Any, *, indent: Optional[int] = None) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default, indent=indent)


def _redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def redact_data(value: Any, *, key_hint: str = "") -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key)
            if _SECRET_KEY_PATTERN.search(key):
                out[key] = _REDACTED
            else:
                out[key] = redact_data(raw_val, key_hint=key)
        return out
    if isinstance(value, (list, tuple, set)):
        return [redact_data(v, key_hint=key_hint) for v in value]
    if isinstance(value, str):
        if _SECRET_KEY_PATTERN.search(key_hint):
            return _REDACTED
        return _redact_text(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return value


class _TeeStream:
    """stdout/stderr を透過しつつ1行単位でイベント化する。"""

    def __init__(self, logger: "WorkflowLogger", stream_name: str, original_stream: Any):
        self._logger = logger
        self._stream_name = stream_name
        self._original_stream = original_stream
        self._buffer = ""

    def write(self, data: Any) -> int:
        text = "" if data is None else str(data)
        try:
            written = self._original_stream.write(text)
        except UnicodeEncodeError:
            encoding = getattr(self._original_stream, "encoding", None) or "utf-8"
            safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            written = self._original_stream.write(safe_text)
        self._original_stream.flush()
        self._buffer += text

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line:
                self._logger._capture_stream_line(self._stream_name, line)

        if isinstance(written, int):
            return written
        return len(text)

    def flush(self) -> None:
        self._original_stream.flush()

    def close_pending(self) -> None:
        if self._buffer:
            line = self._buffer.rstrip("\r")
            if line:
                self._logger._capture_stream_line(self._stream_name, line)
            self._buffer = ""

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original_stream, name)


class PhaseScope:
    """WorkflowLogger.phase() が返すコンテキスト。"""

    def __init__(self, logger: "WorkflowLogger", phase_name: str, span_id: str, parent_span_id: Optional[str]):
        self._logger = logger
        self._phase_name = phase_name
        self._span_id = span_id
        self._parent_span_id = parent_span_id
        self._started_at: Optional[datetime] = None
        self._inputs: dict[str, Any] = {}
        self._outputs: dict[str, Any] = {}
        self._metrics: dict[str, Any] = {}
        self._errors: list[dict[str, str]] = []

    def __enter__(self) -> "PhaseScope":
        self._started_at = _now_utc()
        self._logger._emit(
            "PHASE_START",
            payload={"phase_name": self._phase_name},
            span_id=self._span_id,
            parent_span_id=self._parent_span_id,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        ended_at = _now_utc()
        started_at = self._started_at or ended_at
        duration_ms = int((ended_at - started_at).total_seconds() * 1000)
        status = "error" if (exc or self._errors) else "ok"
        if exc:
            self._errors.append({"type": type(exc).__name__, "message": str(exc)})

        phase_record = {
            "phase_name": self._phase_name,
            "status": status,
            "duration_ms": duration_ms,
            "inputs": self._inputs,
            "outputs": self._outputs,
            "metrics": self._metrics,
            "errors": self._errors,
        }
        self._logger._phase_records.append(phase_record)

        self._logger._emit(
            "PHASE_END",
            payload=phase_record,
            span_id=self._span_id,
            parent_span_id=self._parent_span_id,
        )
        return False

    def set_input(self, key: str, value: Any) -> None:
        self._inputs[str(key)] = value

    def set_output(self, key: str, value: Any) -> None:
        self._outputs[str(key)] = value

    def add_metric(self, key: str, value: Any) -> None:
        self._metrics[str(key)] = value

    def add_error(self, message: str, *, error_type: str = "Error") -> None:
        self._errors.append({"type": error_type, "message": message})


class WorkflowLogger:
    """エージェント実行ログをJSONLとして記録する。"""

    def __init__(
        self,
        *,
        agent: str,
        workflow: str = "",
        workspace_root: Optional[Path] = None,
        capture_streams: Optional[bool] = None,
        max_stream_events: int = 4000,
        max_inline_chars: int = 2000,
        max_inline_json_chars: int = 8000,
    ):
        self.workspace_root = (workspace_root or WORKSPACE_ROOT).resolve()
        self.agent = _safe_name(agent)
        self.workflow = workflow or agent
        self.workflow_slug = _safe_name(self.workflow)
        self.trace_id = f"trace_{uuid.uuid4().hex}"
        self.started_at = _now_utc()

        self._lock = threading.RLock()
        self._event_seq = 0
        self._span_seq = 0
        self._phase_records: list[dict[str, Any]] = []
        self._verification_runs: list[dict[str, Any]] = []
        self._claims: list[dict[str, Any]] = []
        self._tool_calls: dict[str, dict[str, Any]] = {}

        self._run_inputs: dict[str, Any] = {}
        self._run_outputs: dict[str, Any] = {}
        self._run_metrics: dict[str, Any] = {}
        self._claimed_success: Optional[bool] = None
        self._claim_evidence_refs: list[str] = []
        self._closed = False
        self._summary_cache: Optional[dict[str, Any]] = None

        self.max_stream_events = max_stream_events
        self.max_inline_chars = max_inline_chars
        self.max_inline_json_chars = max_inline_json_chars
        self._stream_event_count = 0
        self._stream_drop_notified = False

        date_str = self.started_at.strftime("%Y%m%d")
        stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
        self.run_id = f"{self.agent}_{self.workflow_slug}_{stamp}_{uuid.uuid4().hex[:8]}"

        self.log_root = self.workspace_root / "_logs" / "autonomy"
        self.agent_log_root = self.log_root / self.agent
        self.run_log_dir = self.agent_log_root / date_str
        self.run_log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_log_dir / f"{self.run_id}.jsonl"
        self.summary_path = self.run_log_dir / f"{self.run_id}_summary.json"
        self.artifacts_dir = self.run_log_dir / "artifacts" / self.run_id
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._fp = self.log_path.open("a", encoding="utf-8")

        env_capture = os.getenv("WORKFLOW_LOG_CAPTURE_STREAMS", "1").strip().lower()
        self.capture_streams = (
            capture_streams
            if capture_streams is not None
            else env_capture not in {"0", "false", "off", "no"}
        )
        self._stdout_original = None
        self._stderr_original = None
        self._stdout_tee: Optional[_TeeStream] = None
        self._stderr_tee: Optional[_TeeStream] = None

        self._emit(
            "TASK_RECEIVED",
            payload={
                "goal": os.getenv("AGENT_GOAL", ""),
                "acceptance_criteria": [c for c in os.getenv("AGENT_ACCEPTANCE", "").split("||") if c],
            },
        )
        self._emit(
            "RUN_START",
            payload={
                "pid": os.getpid(),
                "cwd": str(Path.cwd()),
                "argv": list(sys.argv[1:]),
                "schema_version": SCHEMA_VERSION,
            },
        )
        if self.capture_streams:
            self._start_stream_capture()

    def __enter__(self) -> "WorkflowLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc:
            self.add_phase_direct(
                phase_name="_ERROR",
                status="error",
                errors=[{"type": type(exc).__name__, "message": str(exc)}],
            )
        self.finalize()
        return False

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace_root))
        except Exception:
            return str(path.resolve())

    def _next_span_id(self) -> str:
        self._span_seq += 1
        return f"span_{self._span_seq:05d}"

    def _artifact_ref(self, artifact_id: str, path: Path, sha256: str, size_bytes: int, preview_text: str) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "path": self._relative_path(path),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "preview": preview_text,
        }

    def _write_artifact(self, name: str, content: Any, *, suffix: str = "", emit_event: bool = True) -> dict[str, Any]:
        safe_name = _safe_name(name)
        artifact_id = f"artifact_{self._event_seq + 1:05d}_{safe_name}_{uuid.uuid4().hex[:6]}"

        if isinstance(content, bytes):
            body = content
            preview_text = f"<bytes:{len(body)}>"
            if not suffix:
                suffix = ".bin"
        elif isinstance(content, str):
            redacted = _redact_text(content)
            body = redacted.encode("utf-8")
            preview_text = _preview(redacted)
            if not suffix:
                suffix = ".txt"
        else:
            normalized = redact_data(content)
            serialized = _json_dumps(normalized)
            body = serialized.encode("utf-8")
            preview_text = _preview(serialized)
            if not suffix:
                suffix = ".json"

        artifact_path = self.artifacts_dir / f"{artifact_id}{suffix}"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(body)

        ref = self._artifact_ref(
            artifact_id=artifact_id,
            path=artifact_path,
            sha256=_sha256_bytes(body),
            size_bytes=len(body),
            preview_text=preview_text,
        )
        if emit_event:
            self._emit(
                "ARTIFACT_WRITTEN",
                payload={
                    "artifact": ref,
                    "kind": safe_name,
                },
            )
        return ref

    def _prepare_value(self, key: str, value: Any) -> Any:
        normalized = redact_data(value, key_hint=key)
        if isinstance(normalized, str):
            if len(normalized) > self.max_inline_chars:
                return self._write_artifact(key, normalized, suffix=".txt")
            return normalized

        if isinstance(normalized, (dict, list)):
            encoded = _json_dumps(normalized)
            if len(encoded) > self.max_inline_json_chars:
                return self._write_artifact(key, normalized, suffix=".json")
            return normalized

        if isinstance(value, bytes):
            if len(value) > self.max_inline_chars:
                return self._write_artifact(key, value, suffix=".bin")
            return f"<bytes:{len(value)}>"

        return normalized

    def _emit(
        self,
        event_type: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._event_seq += 1
            prepared_payload = {}
            for key, value in (payload or {}).items():
                prepared_payload[key] = self._prepare_value(key, value)

            event = {
                "schema_version": SCHEMA_VERSION,
                "ts": _iso(),
                "event_seq": self._event_seq,
                "event_type": event_type,
                "run_id": self.run_id,
                "trace_id": self.trace_id,
                "span_id": span_id or "",
                "parent_span_id": parent_span_id or "",
                "agent": self.agent,
                "workflow": self.workflow,
                "payload": prepared_payload,
            }
            self._fp.write(_json_dumps(event) + "\n")
            self._fp.flush()

    def _start_stream_capture(self) -> None:
        if self._stdout_original is not None:
            return
        self._stdout_original = sys.stdout
        self._stderr_original = sys.stderr
        self._stdout_tee = _TeeStream(self, "stdout", self._stdout_original)
        self._stderr_tee = _TeeStream(self, "stderr", self._stderr_original)
        sys.stdout = self._stdout_tee
        sys.stderr = self._stderr_tee
        self._emit("STREAM_CAPTURE_STARTED", payload={"max_stream_events": self.max_stream_events})

    def _stop_stream_capture(self) -> None:
        if self._stdout_original is None:
            return
        if self._stdout_tee is not None:
            self._stdout_tee.close_pending()
        if self._stderr_tee is not None:
            self._stderr_tee.close_pending()
        sys.stdout = self._stdout_original
        sys.stderr = self._stderr_original
        self._stdout_original = None
        self._stderr_original = None
        self._stdout_tee = None
        self._stderr_tee = None
        self._emit("STREAM_CAPTURE_STOPPED", payload={"captured_stream_events": self._stream_event_count})

    def _capture_stream_line(self, stream_name: str, text: str) -> None:
        self._stream_event_count += 1
        if self._stream_event_count > self.max_stream_events:
            if not self._stream_drop_notified:
                self._stream_drop_notified = True
                self._emit(
                    "STREAM_OUTPUT_DROPPED",
                    payload={
                        "stream": stream_name,
                        "reason": "stream_event_limit_reached",
                        "limit": self.max_stream_events,
                    },
                )
            return
        self._emit(
            "STREAM_OUTPUT",
            payload={"stream": stream_name, "text": text},
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def phase(self, phase_name: str, *, parent_span_id: Optional[str] = None) -> PhaseScope:
        return PhaseScope(
            logger=self,
            phase_name=phase_name,
            span_id=self._next_span_id(),
            parent_span_id=parent_span_id,
        )

    def add_phase_direct(
        self,
        *,
        phase_name: str,
        status: str = "ok",
        inputs: Optional[dict[str, Any]] = None,
        outputs: Optional[dict[str, Any]] = None,
        metrics: Optional[dict[str, Any]] = None,
        errors: Optional[list[dict[str, Any]]] = None,
        duration_ms: int = 0,
    ) -> None:
        phase_record = {
            "phase_name": phase_name,
            "status": status,
            "duration_ms": int(duration_ms),
            "inputs": inputs or {},
            "outputs": outputs or {},
            "metrics": metrics or {},
            "errors": errors or [],
        }
        self._phase_records.append(phase_record)
        self._emit("PHASE_DIRECT", payload=phase_record, span_id=self._next_span_id())

    def set_input(self, key: str, value: Any) -> None:
        self._run_inputs[str(key)] = value
        self._emit("RUN_INPUT_SET", payload={"key": key, "value": value})

    def set_output(self, key: str, value: Any) -> None:
        self._run_outputs[str(key)] = value
        self._emit("RUN_OUTPUT_SET", payload={"key": key, "value": value})

    def add_metric(self, key: str, value: Any) -> None:
        self._run_metrics[str(key)] = value
        self._emit("RUN_METRIC_SET", payload={"key": key, "value": value})

    def write_artifact(self, artifact_name: str, content: Any, *, suffix: str = "") -> dict[str, Any]:
        return self._write_artifact(artifact_name, content, suffix=suffix, emit_event=True)

    def log_tool_call(self, tool_name: str, *, args: Any = None, call_id: str = "") -> str:
        cid = call_id or f"call_{len(self._tool_calls) + 1:04d}_{uuid.uuid4().hex[:6]}"
        self._tool_calls[cid] = {"tool_name": tool_name, "started_at": _iso()}
        self._emit(
            "TOOL_CALL",
            payload={"tool_name": tool_name, "call_id": cid, "args": args or {}},
            span_id=self._next_span_id(),
        )
        return cid

    def log_tool_result(
        self,
        *,
        call_id: str,
        status: str,
        result: Any = None,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "call_id": call_id,
            "status": status,
            "duration_ms": duration_ms,
            "result": result,
        }
        if error:
            payload["error"] = error
        self._emit("TOOL_RESULT", payload=payload, span_id=self._next_span_id())

    def record_verification(
        self,
        *,
        checks: list[dict[str, Any]],
        passed: Optional[bool] = None,
        evidence: Any = None,
    ) -> str:
        verification_id = f"verification_{len(self._verification_runs) + 1:03d}"
        if passed is None:
            passed = all(
                bool(
                    check.get("pass")
                    if "pass" in check
                    else str(check.get("status", "")).upper() in {"PASS", "OK", "SUCCESS"}
                )
                for check in checks
            )
        payload: dict[str, Any] = {
            "verification_id": verification_id,
            "pass": bool(passed),
            "checks": checks,
        }
        if evidence is not None:
            payload["evidence"] = evidence
        self._verification_runs.append({"verification_id": verification_id, "pass": bool(passed)})
        self._emit("VERIFICATION_RUN", payload=payload, span_id=self._next_span_id())
        return verification_id

    def claim(self, message: str, *, evidence_refs: list[str], claimed_success: bool = True) -> None:
        if not evidence_refs:
            raise ValueError("CLAIM requires non-empty evidence_refs")
        claim_payload = {
            "message": message,
            "claimed_success": bool(claimed_success),
            "evidence_refs": list(evidence_refs),
        }
        self._claims.append(claim_payload)
        self._claimed_success = bool(claimed_success)
        self._claim_evidence_refs = list(dict.fromkeys(self._claim_evidence_refs + list(evidence_refs)))
        self._emit("CLAIM", payload=claim_payload, span_id=self._next_span_id())

    def set_claimed_success(self, value: bool, *, evidence_refs: list[str]) -> None:
        if value and not evidence_refs:
            raise ValueError("claimed_success=True requires evidence_refs")
        self._claimed_success = bool(value)
        self._claim_evidence_refs = list(dict.fromkeys(evidence_refs))

    def _build_summary(self) -> dict[str, Any]:
        completed_at = _now_utc()
        total_duration_ms = int((completed_at - self.started_at).total_seconds() * 1000)

        passed_phases = sum(1 for p in self._phase_records if p.get("status") in {"ok", "success", "passed"})
        failed_phases = sum(1 for p in self._phase_records if p.get("status") not in {"ok", "success", "passed"})

        if self._claimed_success is None:
            claimed_success = failed_phases == 0
        else:
            claimed_success = bool(self._claimed_success)

        evidence_refs = list(dict.fromkeys(self._claim_evidence_refs))
        if claimed_success and not evidence_refs:
            # 検証成功IDを自動接続（なければclaimed_successを落とす）
            evidence_refs = [v["verification_id"] for v in self._verification_runs if v.get("pass")]
            if not evidence_refs:
                claimed_success = False

        verified_success = bool(self._verification_runs) and all(v.get("pass") for v in self._verification_runs)
        final_status = "success" if failed_phases == 0 else "error"

        summary = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "agent": self.agent,
            "workflow": self.workflow,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(completed_at),
            "total_duration_ms": total_duration_ms,
            "log_path": self._relative_path(self.log_path),
            "summary_path": self._relative_path(self.summary_path),
            "artifacts_dir": self._relative_path(self.artifacts_dir),
            "total_events": self._event_seq,
            "total_phases": len(self._phase_records),
            "passed_phases": passed_phases,
            "failed_phases": failed_phases,
            "final_status": final_status,
            "claimed_success": claimed_success,
            "verified_success": verified_success,
            "evidence_refs": evidence_refs,
            "verification": {
                "count": len(self._verification_runs),
                "passed": sum(1 for v in self._verification_runs if v.get("pass")),
                "failed": sum(1 for v in self._verification_runs if not v.get("pass")),
            },
            "inputs": self._run_inputs,
            "outputs": self._run_outputs,
            "metrics": self._run_metrics,
            "stream_events": min(self._stream_event_count, self.max_stream_events),
        }
        return summary

    def finalize(self) -> dict[str, Any]:
        if self._closed:
            return self._summary_cache or {}

        self._stop_stream_capture()
        summary = self._build_summary()

        # CLAIMイベントが無い場合はsummary準拠で自動追加
        if not self._claims:
            self._emit(
                "CLAIM",
                payload={
                    "message": "run_summary_auto_claim",
                    "claimed_success": summary["claimed_success"],
                    "evidence_refs": summary["evidence_refs"],
                },
                span_id=self._next_span_id(),
            )

        self._emit("RUN_SUMMARY", payload=summary, span_id=self._next_span_id())
        summary["total_events"] = self._event_seq

        self.summary_path.write_text(_json_dumps(summary, indent=2), encoding="utf-8")
        latest_payload = {
            "schema_version": SCHEMA_VERSION,
            "agent": self.agent,
            "run_id": self.run_id,
            "log_path": self._relative_path(self.log_path),
            "summary_path": self._relative_path(self.summary_path),
            "completed_at": summary["completed_at"],
            "claimed_success": summary["claimed_success"],
            "verified_success": summary["verified_success"],
            "final_status": summary["final_status"],
        }
        latest_path = self.agent_log_root / "latest.json"
        latest_path.write_text(_json_dumps(latest_payload, indent=2), encoding="utf-8")

        self._fp.flush()
        self._fp.close()
        self._closed = True
        self._summary_cache = summary
        return summary

    close = finalize
