"""Conservative intent contract for the single supported MVP analysis."""

from __future__ import annotations


_SEARCH_INTENT_TERMS = ("검색",)
_FAILED_OR_UNRESOLVED_TERMS = (
    "검색실패",
    "검색에실패",
    "검색에서실패",
    "검색으로실패",
    "검색이실패",
    "검색은실패",
    "검색도실패",
    "해결하지못",
    "해결못",
    "해결이안",
    "해결안",
    "해결되지않",
    "해결되지못",
    "미해결",
    "찾지못",
    "못찾",
    "풀리지않",
    "풀지못",
    "답이없",
    "답변이없",
    "결과가없",
    "결과없",
    "만족하지못",
    "불만족",
)
_CONTACT_TRANSITION_TERMS = (
    "고객센터에",
    "고객센터로",
    "고객센터까지",
    "고객지원센터에",
    "고객지원센터로",
    "고객지원센터까지",
    "콜센터",
    "상담",
    "문의",
    "voc",
)
_OPPOSITE_INTENT_TERMS = (
    "성공",
    "해결완료",
    "정상처리",
    "정상적으로해결",
    "문제없이",
    "만족함",
    "만족한",
    "만족했",
    "해결하고",
    "해결한",
    "해결된",
    "해결됐",
    "해결되어",
    "해결되었",
    "답을찾았",
    "답변을얻었",
    "답변을받았",
)
_OTHER_SCENARIO_TERMS = (
    "신규가입",
    "예측",
    "전망",
    "로밍",
    "해지",
    "인터넷품질",
)
_ATTRIBUTE_OR_AGGREGATION_PIVOT_TERMS = (
    "평균",
    "중앙값",
    "합계",
    "총액",
    "최댓값",
    "최솟값",
    "비율",
    "퍼센트",
    "%",
    "분포",
    "상관",
    "추이",
    "나이",
    "연령",
    "성별",
    "주소",
    "전화번호",
    "연락처",
    "휴대폰",
    "이메일",
    "메일주소",
    "생년월일",
    "주민등록번호",
    "매출",
    "수익",
    "소득",
    "금액",
)


def is_supported_target_journey_question(question: str) -> bool:
    """Return whether a question asks only for the bounded failure-to-contact Journey."""

    normalized = "".join(question.casefold().split())
    if any(term in normalized for term in _OTHER_SCENARIO_TERMS):
        return False
    if any(term in normalized for term in _ATTRIBUTE_OR_AGGREGATION_PIVOT_TERMS):
        return False

    resolution_scope = normalized
    for term in _FAILED_OR_UNRESOLVED_TERMS:
        resolution_scope = resolution_scope.replace(term, "")
    if any(term in resolution_scope for term in _OPPOSITE_INTENT_TERMS):
        return False

    return (
        any(term in normalized for term in _SEARCH_INTENT_TERMS)
        and any(term in normalized for term in _FAILED_OR_UNRESOLVED_TERMS)
        and any(term in normalized for term in _CONTACT_TRANSITION_TERMS)
    )


__all__ = ["is_supported_target_journey_question"]
