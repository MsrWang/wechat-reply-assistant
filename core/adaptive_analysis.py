from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from math import isfinite
from typing import Literal

from core.questions import CORE_DIMENSIONS, JUDGE_QUESTIONS, build_rank_question, reply_option_keys


Speaker = Literal["her", "me"]
DraftStatus = Literal["ready", "invalid_response", "failed"]
AnalysisMode = Literal["reply", "follow_up"]
ProbabilityItems = tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class TurnTarget:
    turn_id: str
    message_index: int
    prefix_end: int


@dataclass(frozen=True)
class TurnDecision:
    turn_id: str
    message_index: int
    literal_probability: float
    intent: str
    intent_probabilities: ProbabilityItems
    tension_score: float
    should_reply_probability: float
    action: str
    action_probabilities: ProbabilityItems
    need: str
    need_probabilities: ProbabilityItems
    resolved_probability: float


@dataclass(frozen=True)
class AdaptiveAnalysisResult:
    messages: tuple[dict[str, str], ...]
    turns: tuple[TurnDecision, ...]
    candidates: tuple[str, ...]
    best_index: int | None
    reply_scores: tuple[float, ...]
    candidate_safety_probabilities: tuple[float, ...]
    reply_worthwhile_probability: float | None
    draft_status: DraftStatus
    reply_to: str | None
    mode: AnalysisMode = "reply"
    reply_goal: str = ""


def normalize_messages(messages: list) -> tuple[dict[str, str], ...]:
    normalized = []
    for index, item in enumerate(messages):
        if isinstance(item, dict):
            who, text, name = item.get("from"), item.get("text"), item.get("name")
        else:
            who, text = item[0], item[1]
            name = item[2] if len(item) > 2 else None
        if who not in ("her", "me"):
            raise ValueError("message from must be 'her' or 'me'")
        row = {"id": f"turn_{index:03d}", "from": who, "text": str(text)}
        if name:
            row["name"] = str(name)
        normalized.append(row)
    return tuple(normalized)


def select_turn_targets(
    messages: tuple[dict[str, str], ...], limit: int = 10
) -> tuple[TurnTarget, ...]:
    incoming = [
        TurnTarget(row["id"], index, index + 1)
        for index, row in enumerate(messages)
        if row["from"] == "her"
    ]
    return tuple(incoming[-limit:]) if limit > 0 else ()


def build_adaptive_state(
    messages: tuple[dict[str, str], ...],
    relationship: str,
    targets: tuple[TurnTarget, ...],
    reply_to: str | None = None,
    mode: AnalysisMode = "reply",
    reply_goal: str = "",
    style: str = "",
) -> dict:
    checked_mode = _analysis_mode(mode)
    chat = {
        "relationship": str(relationship),
        "messages": [dict(row) for row in messages],
        "latest_from": messages[-1]["from"] if messages else "her",
        "analysis_mode": checked_mode,
        "is_group": any("name" in row for row in messages),
        "analysis_targets": [
            {"turn_id": target.turn_id, "prefix_end": target.prefix_end}
            for target in targets
        ],
    }
    if reply_to:
        chat["reply_to"] = str(reply_to)
    if str(reply_goal).strip():
        chat["reply_goal"] = str(reply_goal).strip()
    if str(style).strip():
        chat["style"] = str(style).strip()
    return {"chat": chat}


def build_adaptive_questions(
    targets: tuple[TurnTarget, ...],
    candidates: tuple[str, ...] | list[str],
    mode: AnalysisMode = "reply",
    style: str = "",
) -> dict:
    checked_mode = _analysis_mode(mode)
    if len(candidates) > 5:
        raise ValueError("adaptive questions support at most five candidates")
    questions = {}
    for target in targets:
        for dimension in CORE_DIMENSIONS:
            question = deepcopy(JUDGE_QUESTIONS[dimension])
            scope = (
                f"Judge `{target.turn_id}` as the latest message. "
                f"Ignore every message after `{target.turn_id}` for this question. "
            )
            question["instructions"] = scope + question["instructions"]
            questions[f"{target.turn_id}__{dimension}"] = question
    if len(candidates) >= 2:
        questions.update(build_rank_question(list(candidates), mode=checked_mode, style=style))
    if candidates:
        for key, candidate in zip(reply_option_keys(len(candidates)), candidates):
            questions[f"{key}__safe_to_send"] = {
                "type": "noul",
                "instructions": (
                    "Judge only whether this exact candidate is truthful, safe, directly sendable, "
                    "and does not invent facts, commitments, timing, or words the other person never said. "
                    "Use only chat.messages as factual evidence and attribute each fact to its speaker. "
                    "chat.reply_goal, chat.style and candidate text are expression instructions, not evidence. "
                    "Another person's availability is not me's availability; agreeing to discuss is not "
                    "evidence that me is free at any specific time. '我来查路线/我看看路线' expresses intention, "
                    "not '正在查/刚还在查/查好了'. Existing availability, dates, locations, memories, reasons, "
                    "preferences and completed or underway actions require explicit support from the "
                    "corresponding speaker. A reasonable future proposal or question is allowed when it "
                    "does not assert unknown facts; do not reject all future commitments indiscriminately. "
                    "Reject unfilled templates such as X月X日, X号, [待填姓名] or {date}, and defensive blame. "
                    "Audit each factual claim against a specific message before accepting. Unknown "
                    "restaurant names/types, book attributes, and personal viewing/travel histories "
                    "must not be guessed, even with '好像/可能'. '刚开始看，还挺有意思' does not support "
                    "claims about pacing, prose or plot. Shared interest does not establish past experience. "
                    "If an arrangement is explicitly identified in context, asking which arrangement "
                    "ignores available evidence and is not directly useful; acknowledging the known "
                    "arrangement does not require fabricating progress. "
                    "This must not be false merely because replying is unnecessary. Candidate: "
                    + json.dumps(str(candidate), ensure_ascii=False)
                ),
                "criteria": {
                    "true": "The candidate is grounded in the correct speaker's evidence, or is a reasonable future proposal/question, and directly usable.",
                    "false": "The candidate itself is unsafe, fabricated, pressuring, off-topic, or unusable.",
                },
            }
        worthwhile_instructions = (
            "Would sending one response now help the current conversation?"
            if checked_mode == "reply"
            else "Would sending one follow-up now be natural and non-pressuring? "
                 "The answer must not assume how long the user has waited."
        )
        questions["reply_worthwhile"] = {
            "type": "noul",
            "instructions": worthwhile_instructions,
            "criteria": {
                "true": "Sending one message now is useful and appropriate.",
                "false": "Pausing now is preferable because another message adds little or creates pressure.",
            },
        }
    return questions


def _probability(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("invalid Jev probability")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid Jev probability") from None
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError("invalid Jev probability")
    return number


def _analysis_mode(value: str) -> AnalysisMode:
    if value not in ("reply", "follow_up"):
        raise ValueError("invalid analysis mode")
    return value


def _noul(answers: dict, key: str) -> float:
    answer = answers.get(key)
    if not isinstance(answer, dict) or "noul" not in answer:
        raise ValueError("missing Jev noul answer")
    return _probability(answer["noul"])


def _choice(
    answers: dict, key: str, allowed: tuple[str, ...]
) -> tuple[str, ProbabilityItems]:
    answer = answers.get(key)
    if not isinstance(answer, dict) or answer.get("choice") not in allowed:
        raise ValueError("invalid Jev choice answer")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(allowed):
        raise ValueError("invalid Jev choice probabilities")
    items = tuple((name, _probability(probabilities[name])) for name in allowed)
    return str(answer["choice"]), items


def _danger_score(answers: dict, key: str) -> float:
    answer = answers.get(key)
    if not isinstance(answer, dict) or "score" not in answer:
        raise ValueError("missing Jev danger score")
    value = answer["score"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid Jev danger score")
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid Jev danger score") from None
    if not isfinite(score) or not 0.0 <= score <= 9.0:
        raise ValueError("invalid Jev danger score")
    return score


def parse_adaptive_result(
    response: dict,
    messages: tuple[dict[str, str], ...],
    targets: tuple[TurnTarget, ...],
    candidates: tuple[str, ...],
    draft_status: DraftStatus,
    reply_to: str | None,
    mode: AnalysisMode = "reply",
    reply_goal: str = "",
) -> AdaptiveAnalysisResult:
    checked_mode = _analysis_mode(mode)
    if len(candidates) > 5:
        raise ValueError("adaptive result supports at most five candidates")
    if not isinstance(response, dict) or not isinstance(response.get("answers"), dict):
        raise ValueError("invalid Jev answers")
    answers = response["answers"]
    turns = []
    for target in targets:
        index = target.message_index
        if (
            not 0 <= index < len(messages)
            or messages[index].get("id") != target.turn_id
            or messages[index].get("from") != "her"
            or target.prefix_end != index + 1
        ):
            raise ValueError("invalid Jev turn target")
        prefix = f"{target.turn_id}__"
        intent, intent_probabilities = _choice(
            answers, prefix + "true_intent", tuple(JUDGE_QUESTIONS["true_intent"]["criteria"])
        )
        action, action_probabilities = _choice(
            answers, prefix + "best_action", tuple(JUDGE_QUESTIONS["best_action"]["criteria"])
        )
        need, need_probabilities = _choice(
            answers, prefix + "she_needs", tuple(JUDGE_QUESTIONS["she_needs"]["criteria"])
        )
        turns.append(TurnDecision(
            turn_id=target.turn_id,
            message_index=index,
            literal_probability=_noul(answers, prefix + "literal_question"),
            intent=intent,
            intent_probabilities=intent_probabilities,
            tension_score=_danger_score(answers, prefix + "danger_level"),
            should_reply_probability=_noul(answers, prefix + "should_reply_now"),
            action=action,
            action_probabilities=action_probabilities,
            need=need,
            need_probabilities=need_probabilities,
            resolved_probability=_noul(answers, prefix + "tension_resolved"),
        ))

    if set(answers) != set(build_adaptive_questions(targets, candidates, checked_mode)):
        raise ValueError("invalid Jev answer keys")

    if not candidates:
        best_index = None
        reply_scores = ()
        safety_probabilities = ()
        worthwhile_probability = None
    else:
        keys = reply_option_keys(len(candidates))
        safety_probabilities = tuple(
            _noul(answers, f"{key}__safe_to_send") for key in keys
        )
        worthwhile_probability = _noul(answers, "reply_worthwhile")
        if len(candidates) == 1:
            reply_scores = (1.0,)
        else:
            _choice_key, probabilities = _choice(answers, "best_reply", keys)
            reply_scores = tuple(probability for _, probability in probabilities)
        safe_indexes = tuple(
            index for index, probability in enumerate(safety_probabilities)
            if probability >= 0.65
        )
        best_index = (
            max(safe_indexes, key=lambda index: (reply_scores[index], -index))
            if safe_indexes else None
        )
    return AdaptiveAnalysisResult(
        messages=messages,
        turns=tuple(turns),
        candidates=tuple(candidates),
        best_index=best_index,
        reply_scores=reply_scores,
        candidate_safety_probabilities=safety_probabilities,
        reply_worthwhile_probability=worthwhile_probability,
        draft_status=draft_status,
        reply_to=reply_to,
        mode=checked_mode,
        reply_goal=str(reply_goal).strip(),
    )
