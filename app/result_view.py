"""Pure projection from typed Jev analysis to immutable display values."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from core.adaptive_analysis import AdaptiveAnalysisResult, TurnDecision


_DISPLAY_CERTAINTY = 0.65
_REPLY_ACCEPTABLE = 0.65

ReplyRole = Literal["recommended", "fallback", "other"]
ReplyState = Literal["reply", "pause", "unsafe", "copy_only", "unreviewed", "empty"]

_CHOICES = {
    "true_intent": {
        "confirm_you_care": "希望确认你在意", "vent_anger": "表达不满或受伤",
        "request_action": "希望你采取行动", "seek_explanation": "希望了解原因",
        "casual_chat": "交流与分享", "close_topic": "平和结束话题",
    },
    "best_action": {
        "check_history": "先核对聊天记录", "apologize": "为已知问题道歉",
        "give_commitment": "给出具体承诺", "explain": "说明事实与原因",
        "acknowledge": "回应并表达理解", "say_less": "简短回应或留白",
        "make_plan": "商量具体安排",
    },
    "she_needs": {
        "apology": "真诚道歉", "action": "具体行动或安排",
        "explanation": "清楚的解释", "care": "关注与在意",
        "nothing": "没有明确额外要求",
    },
}


@dataclass(frozen=True)
class ReplyView:
    source_index: int
    text: str
    role: ReplyRole
    percent_text: str | None
    copy_allowed: bool
    fill_allowed: bool
    safe_to_send: bool

    @property
    def recommended(self) -> bool:
        return self.role == "recommended"


@dataclass(frozen=True)
class TurnCardView:
    message_index: int
    turn_id: str
    rows: tuple[str, ...]
    tension_level: int
    trend_text: str
    urgent: bool


@dataclass(frozen=True)
class ResultView:
    turn_cards: tuple[TurnCardView, ...]
    ordered_replies: tuple[ReplyView, ...]
    action_text: str
    reply_fill_allowed: bool
    status_text: str
    heading_text: str
    reply_state: ReplyState
    reason_lines: tuple[str, ...] = ()
    toggle_text: str | None = None

    @property
    def stop_recommended(self) -> bool:
        return self.reply_state == "pause"

    @property
    def replies_rejected(self) -> bool:
        return self.reply_state == "unsafe"


def _choice_text(dimension: str, value: str) -> str:
    return _CHOICES[dimension].get(value, "暂未判断")


def _score(values: tuple[float, ...], index: int) -> float | None:
    if index >= len(values):
        return None
    value = values[index]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0.0 <= value <= 1.0
    ):
        return None
    return float(value)


def _trend(previous: float | None, current: float) -> str:
    if previous is None or abs(current - previous) < 1.0:
        return "紧张度持平"
    return "紧张度上升" if current > previous else "紧张度下降"


def _stopped(turn: TurnDecision, was_tense: bool) -> bool:
    return (
        was_tense and turn.resolved_probability >= _DISPLAY_CERTAINTY and turn.need == "nothing"
    ) or (turn.tension_score >= 6 and turn.need == "nothing" and turn.action == "say_less")


def _card_rows(turn: TurnDecision, stop: bool, was_tense: bool) -> tuple[str, ...]:
    rows = [
        f"真实意图 · {_choice_text('true_intent', turn.intent)}",
        f"紧张度 {round(turn.tension_score)}/9",
    ]
    if was_tense and turn.resolved_probability >= _DISPLAY_CERTAINTY:
        rows.extend((
            f"紧张已解除 · {round(turn.resolved_probability * 100)}%",
            "建议结束，不必继续补充" if stop else "对话正在降温",
        ))
    elif turn.tension_score >= 6:
        rows.extend((
            f"对方需要 · {_choice_text('she_needs', turn.need)}",
            f"最佳行动 · {_choice_text('best_action', turn.action)}",
        ))
    elif turn.should_reply_probability >= _DISPLAY_CERTAINTY:
        rows.extend((
            f"应该给出具体内容 · {round(turn.should_reply_probability * 100)}%",
            f"最佳行动 · {_choice_text('best_action', turn.action)}",
        ))
    elif turn.literal_probability < _DISPLAY_CERTAINTY:
        rows.extend((
            f"存在潜台词 · {round((1 - turn.literal_probability) * 100)}%",
            f"最佳行动 · {_choice_text('best_action', turn.action)}",
        ))
    else:
        rows.append(f"对方需要 · {_choice_text('she_needs', turn.need)}")
    return tuple(rows[:4])


def project_result(result: AdaptiveAnalysisResult, *, allow_fill: bool = True) -> ResultView:
    """Project model judgments without introducing a recommendation or score."""
    turns = result.turns
    latest = turns[-1] if turns else None
    mode = result.mode
    cards = []
    for position, turn in enumerate(turns):
        if position < len(turns) - 10:
            continue
        previous = turns[position - 1].tension_score if position else None
        was_tense = any(prior.tension_score >= 2 for prior in turns[:position])
        cards.append(TurnCardView(
            message_index=turn.message_index,
            turn_id=turn.turn_id,
            rows=_card_rows(turn, _stopped(turn, was_tense), was_tense),
            tension_level=round(turn.tension_score),
            trend_text=_trend(previous, turn.tension_score),
            urgent=turn.tension_score >= 6,
        ))

    candidates = result.candidates
    scores = tuple(_score(result.reply_scores, index) for index in range(len(candidates)))
    complete_scores = len(result.reply_scores) == len(candidates) and all(
        score is not None for score in scores
    )
    safety_scores = tuple(
        _score(result.candidate_safety_probabilities, index)
        for index in range(len(candidates))
    )
    complete_safety = (
        len(result.candidate_safety_probabilities) == len(candidates)
        and all(score is not None for score in safety_scores)
    )
    worthwhile = result.reply_worthwhile_probability
    complete_worthwhile = (
        isinstance(worthwhile, (int, float))
        and not isinstance(worthwhile, bool)
        and isfinite(worthwhile)
        and 0.0 <= worthwhile <= 1.0
    )
    safe_indexes = {
        index for index, score in enumerate(safety_scores)
        if score is not None and score >= _REPLY_ACCEPTABLE
    }
    best = result.best_index
    ranked = isinstance(best, int) and not isinstance(best, bool) and 0 <= best < len(candidates)
    ranked_safe = ranked and best in safe_indexes
    review_complete = complete_scores and complete_safety and complete_worthwhile
    if result.draft_status != "ready" or not candidates:
        base_state: ReplyState = "empty"
    elif not review_complete:
        base_state = "unreviewed"
    elif not safe_indexes:
        base_state = "unsafe"
    elif not ranked_safe:
        base_state = "unreviewed"
    elif worthwhile < _REPLY_ACCEPTABLE:
        base_state = "pause"
    else:
        base_state = "reply"
    reply_state: ReplyState = (
        "copy_only" if not allow_fill and base_state in ("reply", "pause")
        else base_state
    )
    fill_allowed = allow_fill and base_state in ("reply", "pause")
    order = list(range(len(candidates)))
    if ranked and complete_scores:
        order = [best] + sorted(
            (index for index in order if index != best),
            key=lambda index: (-float(scores[index]), index),
        )
    replies = tuple(ReplyView(
        source_index=index,
        text=str(candidates[index]),
        role=(
            "recommended" if index == best and base_state == "reply"
            else "fallback" if index == best and base_state == "pause"
            else "other"
        ),
        percent_text=None if scores[index] is None else f"{round(scores[index] * 100)}%",
        copy_allowed=True,
        fill_allowed=fill_allowed and index in safe_indexes,
        safe_to_send=index in safe_indexes,
    ) for index in order)

    count = len(candidates)
    reason_lines: tuple[str, ...] = ()
    if base_state == "pause":
        reasons = ["继续回复的必要性较低"]
        if latest is not None and latest.need == "nothing":
            reasons.append("Jev 判断对方可能暂时不需要补充回应")
        reason_lines = tuple(reasons[:2])
    if result.draft_status == "invalid_response":
        status = "未生成回复：模型返回格式异常"
    elif result.draft_status == "failed" or not candidates:
        status = "未生成回复：起草服务暂时不可用"
    elif reply_state == "unsafe":
        status = "分析仍可查看，但这些候选不允许直接填入；请确认内容后重新生成。"
    elif reply_state == "unreviewed":
        status = f"生成 {count} 条，Jev 判断未完成，候选仅供复制"
    elif reply_state == "copy_only":
        status = f"生成 {count} 条，当前为手工内容模式，仅可复制"
    elif reply_state == "pause":
        status = f"生成 {count} 条，Jev 更建议暂停，仍保留 1 条安全备选"
    else:  # reply
        status = f"生成 {count} 条，Jev 推荐 1 条，可填入推荐回复"
    if base_state == "pause" and result.reply_goal:
        status += "；已按本次回应目的生成备选；Jev 仍更建议暂停。"
    if mode == "follow_up" and reply_state in ("reply", "pause", "copy_only"):
        status += "；主动跟进建议无法判断你已经等待了多久。"
    action = ""
    if reply_state == "reply" and mode == "reply" and latest and latest.action in _CHOICES["best_action"]:
        action = "建议：" + _choice_text("best_action", latest.action)
    heading = {
        "reply": "本轮建议：回复",
        "pause": "本轮建议：先停在这里",
        "unsafe": "本轮候选质量不足",
    }.get(base_state, "回复建议")
    toggle_text = None
    if count and reply_state in ("unsafe", "unreviewed"):
        toggle_text = "查看未采用候选（仅复制）"
    elif count > 1:
        toggle_text = "查看其他备选"
    return ResultView(
        tuple(cards), replies, action, bool(fill_allowed), status, heading,
        reply_state, reason_lines, toggle_text,
    )


def project_unreviewed_candidates(candidates: tuple[str, ...] | list[str]) -> ResultView:
    replies = tuple(
        ReplyView(index, str(value), "other", None, True, False, False)
        for index, value in enumerate(candidates)
    )
    count = len(replies)
    return ResultView(
        (), replies, "", False, f"生成 {count} 条，Jev 判断未完成，候选仅供复制",
        "回复建议", "unreviewed", (),
        "查看未采用候选（仅复制）" if count else None,
    )
