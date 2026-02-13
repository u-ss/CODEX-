from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .io import append_jsonl, now_iso, read_json, write_json
from .models import RunError, RunState


@dataclass
class StateManager:
    state_path: Path
    log_path: Path

    def load_or_create(self, project_slug: str, run_id: str) -> RunState:
        if self.state_path.is_file():
            payload = read_json(self.state_path)
            state = RunState.model_validate(payload)
        else:
            state = RunState(project_slug=project_slug, run_id=run_id)
            self.save(state)
        return state

    def save(self, state: RunState) -> None:
        state.updated_at = now_iso()
        write_json(self.state_path, state.model_dump(mode="json"))

    def log_event(self, event: str, **fields: object) -> None:
        payload = {"at": now_iso(), "event": event, **fields}
        append_jsonl(self.log_path, payload)

    def set_step_running(self, state: RunState, step_id: str) -> None:
        step = state.steps[step_id]
        step.status = "running"
        step.started_at = now_iso()
        step.message = None
        self.log_event("step_running", step_id=step_id)
        self.save(state)

    def set_step_success(self, state: RunState, step_id: str, message: str | None = None) -> None:
        step = state.steps[step_id]
        step.status = "success"
        step.finished_at = now_iso()
        step.message = message
        self.log_event("step_success", step_id=step_id, message=message)
        self.save(state)

    def set_step_skipped(self, state: RunState, step_id: str, reason: str) -> None:
        step = state.steps[step_id]
        step.status = "skipped"
        step.finished_at = now_iso()
        step.message = reason
        self.log_event("step_skipped", step_id=step_id, reason=reason)
        self.save(state)

    def set_step_failed(
        self,
        state: RunState,
        step_id: str,
        *,
        error_type: str,
        message: str,
        recovery_hint: str | None = None,
    ) -> None:
        step = state.steps[step_id]
        step.status = "failed"
        step.finished_at = now_iso()
        step.message = message
        state.errors.append(
            RunError(
                step_id=step_id,
                error_type=error_type,
                message=message,
                recovery_hint=recovery_hint,
            )
        )
        self.log_event(
            "step_failed",
            step_id=step_id,
            error_type=error_type,
            message=message,
            recovery_hint=recovery_hint,
        )
        self.save(state)
