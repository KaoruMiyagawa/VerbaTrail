"""Evidence-scoped micro exercises and source-checked revision feedback."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.decision.writing_agent_schemas import (
    WritingAgentEvidenceLink, WritingAgentHypothesis, WritingAgentOutcome, WritingAspect,
)
from app.decision.writing_schemas import NonBlank, WritingDiagnosticResult, WritingEvidence
from app.domain.enums import EvidenceRelation

MAX_REVISION_CHARACTERS = 4000
PRACTICE_VERSION = "writing-micro-practice-v1"
FEEDBACK_VERSION = "writing-revision-feedback-v1"
WORD = re.compile(r"[^\W_]+(?:['’\-][^\W_]+)*")

EXERCISES: dict[WritingAspect, tuple[str, str, str]] = {
    "position": (
        "重述核心立场",
        "Rewrite your main position in one or two sentences. State the choice you make and explain how it answers the original task. Use the passage below as your starting point.",
        "一份新的立场表述，以及学习者对立场如何回应题目的说明。",
    ),
    "reasons": (
        "展开一条理由",
        "Choose a reason in the quoted passage. Explain how it works and add a concrete example. If the reason is only implied, state what you meant before expanding it.",
        "一条明确的理由、一段解释和一个例子，供后续复核理由是否展开。",
    ),
    "reasoning_link": (
        "说明论证联系",
        "Choose a reason or example connected to the quoted passage. Restate your position and explain the step that links them. If you are adding a new reason, identify it as new.",
        "一段把原有理由或例子连接到立场的推理说明，供后续核对两者的关系。",
    ),
}
RULE_ASPECTS: dict[str, WritingAspect] = {
    "position_unclear": "position",
    "reason_underdeveloped": "reasons",
    "reasoning_link_unclear": "reasoning_link",
}


class PracticeHypothesis(BaseModel):
    id: str
    statement: str
    diagnostic_status: str


class WritingPracticePlan(BaseModel):
    version: str
    evidence_version: str
    decision_id: str
    diagnostic_method: str
    diagnostic_method_version: str
    aspect: WritingAspect
    mode: Literal["exploratory"] = "exploratory"
    title: str
    task_prompt: str
    instructions: str
    selection_reason: str
    new_evidence: str
    evaluation_boundary: str
    focus_hypothesis_id: str | None
    hypotheses: list[PracticeHypothesis]
    unresolved_uncertainties: list[str]
    anchor: WritingEvidence
    diagnostic_excerpt: str | None = None


def _focus(hypothesis: WritingAgentHypothesis) -> WritingAspect | None:
    return hypothesis.focus_aspect or RULE_ASPECTS.get(hypothesis.id)


def select_writing_practice(
    snapshot: WritingDiagnosticResult,
    outcome: WritingAgentOutcome,
    *,
    decision_id: str,
    task_prompt: str,
    questioned_hypotheses: list[str],
) -> WritingPracticePlan:
    essay = {item.id: item for item in snapshot.evidence}
    candidates = [
        item for item in outcome.hypotheses
        if item.status not in {"ruled_out", "lowered"} and _focus(item) is not None
    ]

    def anchored_link(item: WritingAgentHypothesis) -> WritingAgentEvidenceLink | None:
        # Older or ungrounded model links can identify a topic, but only an exact
        # essay excerpt may prioritize one explanation over another here.
        return next((link for link in item.evidence_links if link.relation == EvidenceRelation.SUPPORTS
                     and link.excerpt and link.evidence_id in essay
                     and link.excerpt in essay[link.evidence_id].source_reference.quote), None)

    target = max(candidates, key=lambda item: (
        anchored_link(item) is not None, item.id in questioned_hypotheses,
    ), default=None)
    aspect = (_focus(target) if target else None) or "position"
    link = anchored_link(target) if target else None
    contextual_ids = (
        [item.evidence_id for item in target.evidence_links] if target else []
    ) + [evidence_id for item in outcome.argument_analysis if item.aspect == aspect
         for evidence_id in item.evidence_ids]
    anchor = essay[link.evidence_id] if link else next(
        (essay[item] for item in contextual_ids if item in essay), snapshot.evidence[0],
    )
    title, instructions, new_evidence = EXERCISES[aspect]
    if link:
        reason = "该候选假设在诊断中关联了原文摘录。本练习用于获得新的表述，以便复核这种解释。"
    elif target and target.id in questioned_hypotheses:
        reason = "追问曾围绕这一候选假设展开，当前仍缺少足够证据。本练习从相关原文收集一次新的作答。"
    else:
        reason = "现有诊断无法确认特定缺陷。从一段实际原文开始练习，收集可与首次作答对照的文本。"
    return WritingPracticePlan(
        version=PRACTICE_VERSION, evidence_version=snapshot.evidence_version,
        decision_id=decision_id, diagnostic_method=outcome.method,
        diagnostic_method_version=outcome.method_version, aspect=aspect, title=title,
        task_prompt=task_prompt, instructions=instructions, selection_reason=reason,
        new_evidence=new_evidence, focus_hypothesis_id=target.id if target else None,
        evaluation_boundary="修订提供一次有提示的作答样本。文本变化不代表论证质量提高或能力已经掌握。",
        hypotheses=[PracticeHypothesis(id=item.id, statement=item.statement, diagnostic_status=item.status)
                    for item in outcome.hypotheses],
        unresolved_uncertainties=outcome.unresolved_uncertainties,
        anchor=anchor, diagnostic_excerpt=link.excerpt if link else None,
    )


class RevisionObservation(BaseModel):
    metric: Literal["word_count", "code_point_count"]
    original: int
    revision: int


class RevisionCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: NonBlank
    excerpt: str = Field(min_length=1, max_length=600)


class RevisionInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: NonBlank
    original: RevisionCitation
    revision: RevisionCitation


class RevisionHypothesisLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis_id: NonBlank
    relation: EvidenceRelation
    comparison: RevisionInterpretation


class WritingRevisionFeedbackProposal(BaseModel):
    """Response schema for a future explicitly authorized provider adapter."""

    model_config = ConfigDict(extra="forbid")
    changes: list[RevisionInterpretation] = Field(max_length=6)
    hypothesis_evidence: list[RevisionHypothesisLink] = Field(max_length=10)
    unresolved_uncertainties: list[NonBlank] = Field(min_length=1, max_length=6)
    next_step: Literal["more_practice", "independent_transfer", "insufficient_evidence"]
    next_step_reason: NonBlank


class WritingRevisionFeedback(WritingRevisionFeedbackProposal):
    method: Literal["deterministic_comparison", "structured_candidate"]
    version: str = FEEDBACK_VERSION
    observations: list[RevisionObservation]
    text_changed: bool
    capability_evidence_valid: Literal[False] = False


def build_revision_feedback(
    plan: WritingPracticePlan,
    revision_id: str,
    body: str,
    proposal: WritingRevisionFeedbackProposal | dict | None = None,
) -> WritingRevisionFeedback:
    original = plan.anchor.source_reference.quote
    if proposal is not None:
        proposal = WritingRevisionFeedbackProposal.model_validate(proposal)
        valid_hypotheses = {item.id for item in plan.hypotheses}
        for link in proposal.hypothesis_evidence:
            if link.hypothesis_id not in valid_hypotheses:
                raise ValueError("Revision feedback refers to an unknown hypothesis.")
        for comparison in [*proposal.changes, *(link.comparison for link in proposal.hypothesis_evidence)]:
            for citation, expected, text in (
                (comparison.original, plan.anchor.id, original),
                (comparison.revision, revision_id, body),
            ):
                if (citation.evidence_id != expected or not any(c.isalnum() for c in citation.excerpt)
                        or citation.excerpt not in text):
                    raise ValueError("Revision feedback cites text outside the saved comparison.")
    else:
        proposal = WritingRevisionFeedbackProposal(
            changes=[], hypothesis_evidence=[],
            unresolved_uncertainties=[
                *plan.unresolved_uncertainties[:5],
                "修订与原文的语义关系、练习效果及原诊断假设仍待复核。",
            ],
            next_step="insufficient_evidence",
            next_step_reason="先核对修订是否回应练习目标。能力判断仍需要独立作答或迁移证据。",
        )
        return _feedback(plan, body, proposal, "deterministic_comparison")
    return _feedback(plan, body, proposal, "structured_candidate")


def _feedback(
    plan: WritingPracticePlan, body: str, proposal: WritingRevisionFeedbackProposal,
    method: Literal["deterministic_comparison", "structured_candidate"],
) -> WritingRevisionFeedback:
    original = plan.anchor.source_reference.quote
    return WritingRevisionFeedback(
        **proposal.model_dump(), method=method, text_changed=original != body,
        observations=[
            RevisionObservation(metric="word_count", original=len(WORD.findall(original)), revision=len(WORD.findall(body))),
            RevisionObservation(metric="code_point_count", original=len(original), revision=len(body)),
        ],
    )
