"""In-memory state for one manually controlled chat session."""

from dataclasses import dataclass, field
from typing import Literal

from app.fill_target import FillTarget
from app.freshness import RawVisibleChat
from app.privacy import ChatLine, ConfirmedSnapshot
from core.adaptive_analysis import AdaptiveAnalysisResult


AnalysisSource = Literal["live_verified", "user_confirmed_only"]


@dataclass
class SessionState:
    frame: object | None = None
    fill_target: FillTarget | None = None
    preview_title: str = ""
    preview_lines: list[ChatLine] = field(default_factory=list)
    confirmed: ConfirmedSnapshot | None = None
    candidates: list[str] = field(default_factory=list)
    raw_visible: RawVisibleChat | None = None
    result: AdaptiveAnalysisResult | None = None
    busy_stage: str | None = None
    analysis_source: AnalysisSource = "user_confirmed_only"
    fill_consumed: bool = False

    def can_analyze(self, has_key: bool) -> bool:
        return bool(has_key and self.confirmed and self.confirmed.messages)

    def can_fill(self) -> bool:
        return bool(
            self.analysis_source == "live_verified"
            and self.raw_visible is not None
            and self.fill_target is not None
            and self.result is not None
            and not self.fill_consumed
        )

    def clear_chat(self) -> None:
        self.frame = None
        self.fill_target = None
        self.preview_title = ""
        self.preview_lines.clear()
        self.confirmed = None
        self.candidates.clear()
        self.raw_visible = None
        self.result = None
        self.busy_stage = None
        self.analysis_source = "user_confirmed_only"
        self.fill_consumed = False
