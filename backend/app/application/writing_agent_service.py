"""One initial decision and at most one answer review per formal Writing submission."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.content.writing_initial_assessment import PROMPT
from app.decision.writing_agent import (
    MODEL_PROMPT_VERSION,
    plan_model_request,
    rules_outcome,
    validate_proposal,
)
from app.decision.writing_agent_schemas import (
    FallbackReason,
    WritingAgentAnswer,
    WritingAgentDecisionView,
    WritingAgentModelAudit,
    WritingAgentOutcome,
    WritingAgentProposal,
    WritingAgentSessionView,
)
from app.decision.writing_schemas import WritingDiagnosticResult
from app.domain.enums import ModelCallStatus
from app.model_provider import ModelProvider, ModelProviderError
from app.persistence.models import AssessmentTask, AssessmentWritingDiagnosticDecision, new_id, utc_now
from app.persistence.repositories import LearningRepository

Decision = AssessmentWritingDiagnosticDecision


class WritingDiagnosticConflict(ValueError):
    """A request conflicts with a saved decision or its evidence version."""


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _charged(decision: Decision) -> int:
    call = decision.model_call
    if call and call.total_tokens > 0:
        return call.total_tokens
    return decision.reserved_tokens


def _answer(decision: Decision, question_id: str) -> WritingAgentAnswer | None:
    body = decision.answer_body
    if body is None:
        return None
    return WritingAgentAnswer(
        evidence_id=f"{decision.id}:answer", question_id=question_id, body=body,
        body_sha256=sha256(body.encode("utf-8")).hexdigest(),
    )


def _view(decision: Decision | None, question_id: str) -> WritingAgentDecisionView | None:
    if decision is None:
        return None
    call = decision.model_call
    return WritingAgentDecisionView(
        id=decision.id, phase=decision.phase, state="completed" if decision.result is not None else "processing",
        allow_model=decision.allow_model, idempotency_key=decision.idempotency_key,
        recover_after=_utc(decision.recover_after),
        recoverable=decision.result is None and _utc(decision.recover_after) <= utc_now(),
        completed_at=_utc(decision.completed_at) if decision.completed_at else None,
        answer=_answer(decision, question_id),
        outcome=WritingAgentOutcome.model_validate(decision.result) if decision.result is not None else None,
        planned_model=decision.planned_model,
        model_call=WritingAgentModelAudit(
            id=call.id, provider_name=call.provider_name, model_name=call.model_name,
            prompt_version=call.prompt_version, status=call.status, error_code=call.error_code,
            reported_tokens=call.total_tokens, attempts=call.attempt_count,
        ) if call else None,
    )


class WritingAgentService:
    def __init__(self, db: Session, settings: Settings, provider: ModelProvider):
        self.db = db
        self.settings = settings
        self.provider = provider

    @property
    def model_available(self) -> bool:
        return (
            self.settings.allow_remote_model_calls and self.settings.app_env != "testing"
            and self.provider.provider_name != "fake" and self.provider.is_configured()
        )

    def _decisions(self, snapshot: WritingDiagnosticResult) -> dict[str, Decision]:
        decisions = self.db.scalars(
            select(Decision).where(Decision.submission_id == snapshot.source.submission_id)
            .execution_options(populate_existing=True),
        )
        by_phase = {decision.phase: decision for decision in decisions}
        if any(item.evidence_version != snapshot.evidence_version for item in by_phase.values()):
            raise WritingDiagnosticConflict("The saved Writing evidence version is unavailable.")
        return by_phase

    def read(self, snapshot: WritingDiagnosticResult) -> WritingAgentSessionView:
        decisions = self._decisions(snapshot)
        initial = decisions.get("initial")
        initial_view = _view(initial, "")
        followup = _view(decisions.get("followup"), initial.id if initial else "")
        if initial_view is None:
            state = "not_started"
        elif followup is not None:
            state = "stopped" if followup.outcome is not None else "processing"
        elif initial_view.outcome is None:
            state = "processing"
        else:
            state = "awaiting_answer" if initial_view.outcome.followup is not None else "stopped"
        return WritingAgentSessionView(
            source=snapshot.source, evidence_version=snapshot.evidence_version, evidence=snapshot.evidence,
            state=state, model_available=self.model_available,
            tokens_charged=sum(_charged(item) for item in decisions.values()),
            tokens_reported=sum(item.model_call.total_tokens for item in decisions.values() if item.model_call),
            tokens_pending_reservation=sum(item.reserved_tokens for item in decisions.values() if item.result is None),
            token_budget=initial.token_budget if initial else max(0, self.settings.diagnostic_session_token_budget),
            followup_limit=initial.followup_limit if initial else self._followup_limit(),
            initial=initial_view, followup=followup,
        )

    def _followup_limit(self) -> int:
        return int(self.settings.max_diagnostic_followup_turns > 0)

    def _local_reason(self, allow_model: bool) -> FallbackReason | None:
        if not self.settings.allow_remote_model_calls:
            return "remote_disabled"
        if not self.model_available:
            return "model_unavailable"
        return None if allow_model else "model_not_requested"

    def _relock_task(self, task_id: str) -> None:
        # Same lock as the assessment router took for the reservation.
        if self.db.get_bind().dialect.name == "sqlite":
            self.db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            self.db.execute(select(AssessmentTask.id).where(AssessmentTask.id == task_id).with_for_update()).one()

    def advance(
        self,
        snapshot: WritingDiagnosticResult,
        *,
        task_id: str,
        key: str,
        allow_model: bool,
        recover_decision_id: str | None = None,
        question_id: str | None = None,
        answer_body: str | None = None,
    ) -> WritingAgentSessionView:
        """The router holds the task write lock until the reservation is saved."""
        phase = "initial" if question_id is None else "followup"
        decisions = self._decisions(snapshot)
        initial = decisions.get("initial")
        prior = None
        if phase == "followup":
            if initial is None or initial.id != question_id or initial.result is None:
                raise WritingDiagnosticConflict("The answer does not refer to the active Writing question.")
            prior = WritingAgentOutcome.model_validate(initial.result)
            if initial.followup_limit == 0 or prior.followup is None:
                raise WritingDiagnosticConflict("There is no Writing question to answer.")

        if any(item.idempotency_key == key and item.phase != phase for item in decisions.values()):
            raise WritingDiagnosticConflict("The idempotency key belongs to a different decision.")
        saved = decisions.get(phase)
        if recover_decision_id is not None and (saved is None or saved.id != recover_decision_id):
            raise WritingDiagnosticConflict("The saved Writing decision is no longer available for recovery.")
        if saved is not None:
            # An identical start from another tab can recover the existing decision.
            if (saved.allow_model != allow_model or saved.answer_body != answer_body
                    or (phase == "followup" and saved.idempotency_key != key)):
                raise WritingDiagnosticConflict("A different request already owns this Writing decision.")
            if saved.result is None and _utc(saved.recover_after) <= utc_now():
                saved.result = rules_outcome(
                    snapshot, prior, _answer(saved, question_id or ""), saved.followup_limit, "interrupted",
                ).model_dump(mode="json")
                saved.completed_at = utc_now()
                self.db.commit()
            return self.read(snapshot)

        token_budget = initial.token_budget if initial else max(0, self.settings.diagnostic_session_token_budget)
        followup_limit = initial.followup_limit if initial else self._followup_limit()
        saved = Decision(
            id=new_id("awd"), submission_id=snapshot.source.submission_id, phase=phase,
            required_initial_phase="initial" if phase == "followup" else None,
            idempotency_key=key, allow_model=allow_model,
            answer_body=answer_body, evidence_version=snapshot.evidence_version, token_budget=token_budget,
            followup_limit=followup_limit, reserved_tokens=0,
            recover_after=utc_now() + timedelta(seconds=min(300, max(30, self.settings.model_timeout_seconds * 4 + 15))),
        )
        self.db.add(saved)
        answer = _answer(saved, question_id or "")
        fallback = self._local_reason(allow_model)
        planned = None
        if fallback is None:
            planned = plan_model_request(
                snapshot, prompt=PROMPT["text"], instructions=PROMPT["instructions"],
                prior=prior, answer=answer, remaining_tokens=min(
                    token_budget, self.settings.diagnostic_session_token_budget,
                ) - sum(_charged(item) for item in decisions.values()),
                followup_limit=followup_limit, settings=self.settings,
            )
            if planned is None:
                fallback = "budget_exhausted"
        if planned is None:
            saved.result = rules_outcome(snapshot, prior, answer, followup_limit, fallback or "model_unavailable").model_dump(mode="json")
            saved.completed_at = utc_now()
            self.db.commit()
            return self.read(snapshot)

        request, saved.reserved_tokens = planned
        saved.planned_model = {
            "provider_name": self.provider.provider_name, "model_name": self.provider.model_name,
            "prompt_version": MODEL_PROMPT_VERSION,
        }
        details: dict[str, Any] = dict(saved.planned_model)
        decision_id = saved.id
        self.db.commit()
        current = self.db.get(Decision, decision_id, populate_existing=True)
        if current is None:
            raise WritingDiagnosticConflict("The Writing decision is no longer available.")
        active = current.result is None and utc_now() < _utc(current.recover_after)
        self.db.rollback()
        if not active:
            self._relock_task(task_id)
            return self.advance(
                snapshot, task_id=task_id, key=key, allow_model=allow_model, recover_decision_id=decision_id,
                question_id=question_id, answer_body=answer_body,
            )
        # No database writer lock is held while a configured model is called.
        outcome = None
        try:
            reply = self.provider.generate_structured(request, WritingAgentProposal)
            usage = reply.usage
            details.update(
                provider_name=reply.provider_name, model_name=reply.model_name, prompt_version=reply.prompt_version,
                prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                total_tokens=max(usage.total_tokens, usage.prompt_tokens + usage.completion_tokens),
                latency_ms=reply.latency_ms, attempt_count=reply.attempt_count,
            )
            proposal = WritingAgentProposal.model_validate(reply.parsed_output.model_dump())
            validate_proposal(proposal, snapshot, prior, answer, followup_limit)
            outcome = WritingAgentOutcome(
                **proposal.model_dump(), method="structured_model", method_version=MODEL_PROMPT_VERSION,
            )
            details.update(status=ModelCallStatus.SUCCEEDED, redacted_summary="Writing proposal accepted.")
        except ModelProviderError as exc:
            failure_reasons: dict[ModelCallStatus, FallbackReason] = {
                ModelCallStatus.INVALID_JSON: "invalid_model_output",
                ModelCallStatus.SCHEMA_ERROR: "invalid_model_output",
                ModelCallStatus.BUDGET_EXCEEDED: "budget_exhausted",
                ModelCallStatus.REMOTE_DISABLED: "model_unavailable",
            }
            fallback = failure_reasons.get(exc.status, "model_error")
            details.update(
                status=exc.status, error_code=exc.error_code, attempt_count=exc.attempt_count,
                latency_ms=exc.latency_ms, redacted_summary="Writing provider call failed.",
            )
        except (ValueError, TypeError, AttributeError, KeyError, IndexError):
            fallback = "invalid_model_output"
            details.update(
                status=ModelCallStatus.SCHEMA_ERROR, error_code="WRITING_PROPOSAL_INVALID",
                redacted_summary="Writing proposal failed validation.",
            )
        if outcome is None:
            outcome = rules_outcome(snapshot, prior, answer, followup_limit, fallback or "model_error")
        self._relock_task(task_id)
        saved = self.db.get(Decision, decision_id, populate_existing=True)
        if saved is None:
            raise WritingDiagnosticConflict("The Writing decision is no longer available.")
        if saved.model_call_id is None:
            audit = LearningRepository(self.db).record_model_call(**details)
            saved.model_call_id = audit.id
        if saved.result is None:
            saved.result = outcome.model_dump(mode="json")
            saved.completed_at = utc_now()
        self.db.commit()
        return self.read(snapshot)
