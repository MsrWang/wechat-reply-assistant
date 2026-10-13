"""Fixed offline example. No capture, credentials, or provider access."""
from core.adaptive_analysis import AdaptiveAnalysisResult, normalize_messages


def demo_result():
    return AdaptiveAnalysisResult(
        messages=normalize_messages([("me", "周末要不要出去走走？"), ("her", "好呀，周六下午吧，我想找个安静点的地方")]),
        turns=(), candidates=("好，那就周六下午，找个安静的地方坐坐。", "可以呀，你有想去的地方吗？"),
        best_index=0, reply_scores=(0.8, 0.7), candidate_safety_probabilities=(0.95, 0.95),
        reply_worthwhile_probability=0.9, reply_goal="", draft_status="ready", reply_to=None,
    )
