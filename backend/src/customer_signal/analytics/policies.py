"""Fixed scoring policy for customer journey risk signals."""

from typing import Literal


FAILED_SEARCH_SCORE = 25
SAME_TOPIC_FAILED_REPEAT_SCORE = 25
NEGATIVE_FEEDBACK_SCORE = 20
SAME_TOPIC_UNRESOLVED_VOC_SCORE = 30

REPEAT_WINDOW_HOURS = 24
VOC_WINDOW_HOURS = 72

HIGH_RISK_MIN_SCORE = 75
MEDIUM_RISK_MIN_SCORE = 40


def risk_level_for_score(score: int | float) -> Literal["high", "medium", "low"]:
    """Classify a bounded risk score using inclusive policy thresholds."""

    if score >= HIGH_RISK_MIN_SCORE:
        return "high"
    if score >= MEDIUM_RISK_MIN_SCORE:
        return "medium"
    return "low"


__all__ = [
    "FAILED_SEARCH_SCORE",
    "HIGH_RISK_MIN_SCORE",
    "MEDIUM_RISK_MIN_SCORE",
    "NEGATIVE_FEEDBACK_SCORE",
    "REPEAT_WINDOW_HOURS",
    "SAME_TOPIC_FAILED_REPEAT_SCORE",
    "SAME_TOPIC_UNRESOLVED_VOC_SCORE",
    "VOC_WINDOW_HOURS",
    "risk_level_for_score",
]
