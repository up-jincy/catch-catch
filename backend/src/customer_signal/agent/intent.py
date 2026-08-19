"""Conservative intent contract for the single supported MVP analysis."""

from __future__ import annotations

import re

_QUESTION_SEPARATORS = re.compile(r"[\s?!.。,]+")
_NEGATED_FAILURE = re.compile(r"실패(?:하지(?:않|못)|(?:가|는|은)?아니)")
_NEGATED_CONTACT = re.compile(
    r"(?:문의|상담|접수)(?:"
    r"(?:를|을)?하지(?:않|못)|"
    r"(?:를|을)?받지(?:않|못)|"
    r"(?:가|는|은)?아니"
    r")|"
    r"(?:고객(?:지원)?센터|콜센터)(?:에|로|까지)?(?:"
    r"가지(?:않|못)|안간|못간|연결되지않"
    r")"
)
_SUPPORTED_TARGET_JOURNEY = re.compile(
    r"^(?:"
    r"(?:ai)?검색(?:에|에서|으로|이|은|도)?실패|"
    r"(?:ai)?검색(?:에|에서|으로)?(?:"
    r"해결하지못|해결못|해결이안|해결안|해결되지않|해결되지못|미해결"
    r")|"
    r"(?:ai)?검색으로답을(?:찾지못|못찾)|"
    r"검색결과로문제가(?:풀리지않|풀지못|해결되지않)|"
    r"다시검색했지만(?:해결되지않|해결하지못|해결못)"
    r")"
    r"(?:후|뒤|한뒤|한후|하고|해|아|된뒤|된후|고)?"
    r"(?:"
    r"문의(?:한)?|"
    r"상담(?:전환|한)|"
    r"상담원에게문의한|"
    r"고객(?:지원)?센터(?:에문의한|에연결된|로이동한|까지간)|"
    r"콜센터(?:에문의한|로연결된|까지간)|"
    r"voc를(?:접수한|남긴)"
    r")"
    r"(?:고객|이용자)"
    r"(?:"
    r"(?:은|는|이|가)?(?:"
    r"몇명(?:이야|인가|인지|입니까|인가요)?|"
    r"얼마나(?:돼|되는지|인지|입니까|인가요)?"
    r")|"
    r"수|"
    r"(?:을|를)?(?:분석|리서치|확인)(?:해줘|해주세요)?|"
    r"(?:여정|journey)(?:을|를)?(?:"
    r"알려줘|알려주세요|분석해줘|분석해주세요|확인해줘|확인해주세요"
    r")?|"
    r"(?:은|는)?"
    r")$"
)


def is_supported_target_journey_question(question: str) -> bool:
    """Return whether a question asks only for the bounded failure-to-contact Journey."""

    normalized = _QUESTION_SEPARATORS.sub("", question.casefold())
    if _NEGATED_FAILURE.search(normalized) or _NEGATED_CONTACT.search(normalized):
        return False
    return _SUPPORTED_TARGET_JOURNEY.fullmatch(normalized) is not None


__all__ = ["is_supported_target_journey_question"]
