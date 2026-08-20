"""Scenario-first synthetic dataset generation."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from customer_signal.domain.models import (
    CustomerEvent,
    EventType,
    EvidenceRecord,
    IdentityEdge,
    IdentityRef,
    Scalar,
    SourceId,
    SyntheticDataset,
)


SEOUL = ZoneInfo("Asia/Seoul")
WINDOW_START = datetime(2026, 7, 20, tzinfo=SEOUL)
POSITIVE_CUSTOMER_IDS = (
    "CUST-003",
    "CUST-007",
    "CUST-011",
    "CUST-016",
    "CUST-022",
    "CUST-028",
)
TOPICS = ("인터넷 장애", "로밍", "요금", "기기 변경")
_DIMENSION_ATTRIBUTE_NAMES = frozenset(
    {"authenticated", "contact_channel", "is_repeat", "noise", "product_family", "stage", "status"}
)
_MEASURE_ATTRIBUTE_NAMES = frozenset({"rating", "result_count", "session_depth"})


class _DatasetBuilder:
    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.events: list[CustomerEvent] = []
        self.evidence: list[EvidenceRecord] = []
        self.identity_edges: list[IdentityEdge] = []
        self._customer_sequences: dict[str, int] = {}
        self._source_identities: dict[tuple[str, SourceId], IdentityRef] = {}

    def register_customer_identity_graph(self, customer_id: str) -> None:
        """Create the deterministic cross-source identity path for one customer."""

        customer_number = int(customer_id.rsplit("-", maxsplit=1)[1])
        search_run = IdentityRef(namespace="search_run", value=f"RUN-{customer_number:03d}")
        search_thread = IdentityRef(
            namespace="search_thread",
            value=f"THREAD-{customer_number:03d}",
        )
        digital_session = IdentityRef(
            namespace="digital_session",
            value=f"SESSION-{customer_number:03d}",
        )
        canonical_customer = IdentityRef(
            namespace="canonical_customer",
            value=customer_id,
        )
        subscription = IdentityRef(
            namespace="subscription_entry",
            value=f"ENTR-{customer_number:03d}",
        )
        voc_case = IdentityRef(namespace="voc_case", value=f"VOC-{customer_number:03d}")

        self._source_identities.update(
            {
                (customer_id, "search_history"): search_run,
                (customer_id, "search_feedback"): search_run,
                (customer_id, "digital_behavior"): digital_session,
                (customer_id, "subscription"): subscription,
                (customer_id, "voc"): voc_case,
            }
        )
        self.identity_edges.extend(
            [
                IdentityEdge(
                    left=search_run,
                    right=search_thread,
                    link_type="EXACT",
                    confidence=1.0,
                    provenance="shared search RUN_ID",
                ),
                IdentityEdge(
                    left=search_thread,
                    right=digital_session,
                    link_type="SYNTHETIC",
                    confidence=1.0,
                    provenance="seeded cross-channel bridge",
                ),
                IdentityEdge(
                    left=digital_session,
                    right=canonical_customer,
                    link_type="DECLARED",
                    confidence=1.0,
                    provenance="authenticated digital customer mapping",
                ),
                IdentityEdge(
                    left=canonical_customer,
                    right=subscription,
                    link_type="EXACT",
                    confidence=1.0,
                    provenance="subscription CUST_NO mapping",
                ),
                IdentityEdge(
                    left=subscription,
                    right=voc_case,
                    link_type="DECLARED",
                    confidence=1.0,
                    provenance="VOC ENTR_NO mapping",
                ),
            ]
        )

    def _canonical_customer_id(self, identity: IdentityRef) -> str:
        graph: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for edge in self.identity_edges:
            left = (edge.left.namespace, edge.left.value)
            right = (edge.right.namespace, edge.right.value)
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)

        pending = [(identity.namespace, identity.value)]
        visited: set[tuple[str, str]] = set()
        while pending:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            pending.extend(graph.get(node, set()) - visited)
        resolved = sorted(
            value for namespace, value in visited if namespace == "canonical_customer"
        )
        if len(resolved) != 1:
            raise ValueError("source identity must resolve to exactly one canonical customer")
        return resolved[0]

    def add_event(
        self,
        *,
        customer_id: str,
        occurred_at: datetime,
        source_id: SourceId,
        event_type: EventType,
        action: str,
        topic: str,
        outcome: str,
        text: str,
        attributes: dict[str, Scalar] | None = None,
    ) -> None:
        normalized_attributes = attributes or {}
        identity = self._source_identities[(customer_id, source_id)]
        resolved_customer_id = self._canonical_customer_id(identity)
        sequence = self._customer_sequences.get(customer_id, 0) + 1
        self._customer_sequences[customer_id] = sequence
        customer_number = int(customer_id.rsplit("-", maxsplit=1)[1])
        event_id = f"EVT-{self.seed}-{customer_number:03d}-{sequence:02d}"
        evidence_id = f"EVD-{self.seed}-{customer_number:03d}-{sequence:02d}"
        masked_customer_id = f"CU***{customer_number:03d}"

        event = CustomerEvent(
            event_id=event_id,
            evidence_id=evidence_id,
            source_id=source_id,
            occurred_at=occurred_at,
            event_type=event_type,
            action=action,
            topic=topic,
            outcome=outcome,
            text=text,
            identities=[identity],
            canonical_customer_id=resolved_customer_id,
            attributes=normalized_attributes,
            dimensions={
                name: value
                for name, value in normalized_attributes.items()
                if name in _DIMENSION_ATTRIBUTE_NAMES
            },
            measures={
                name: value
                for name, value in normalized_attributes.items()
                if name in _MEASURE_ATTRIBUTE_NAMES
            },
        )
        evidence = EvidenceRecord(
            evidence_id=evidence_id,
            source_id=source_id,
            occurred_at=occurred_at,
            masked_customer_id=masked_customer_id,
            summary=f"{topic} {event_type} 이벤트: {outcome}",
            raw_fields={
                "customer_ref": masked_customer_id,
                "identity_namespace": identity.namespace,
                "identity_ref": identity.value,
                "action": action,
                "topic": topic,
                "outcome": outcome,
                "text": text,
            },
        )
        self.events.append(event)
        self.evidence.append(evidence)


def _base_time(rng: random.Random, *, latest_day: int) -> datetime:
    return WINDOW_START + timedelta(
        days=rng.randrange(latest_day + 1),
        hours=8 + rng.randrange(8),
        minutes=rng.randrange(6) * 10,
    )


def _add_cross_source_context(
    builder: _DatasetBuilder,
    *,
    customer_id: str,
    occurred_at: datetime,
    topic: str,
    risky: bool,
) -> None:
    builder.add_event(
        customer_id=customer_id,
        occurred_at=occurred_at,
        source_id="digital_behavior",
        event_type="digital_behavior",
        action="visit_support_page",
        topic=topic,
        outcome="abandoned" if risky else "completed",
        text=(
            "장애 해결 페이지를 반복 방문했지만 절차를 완료하지 못했습니다."
            if risky
            else f"{topic} 안내 페이지에서 필요한 정보를 확인했습니다."
        ),
        attributes={"session_depth": 5 if risky else 2, "authenticated": True},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=occurred_at + timedelta(hours=2),
        source_id="subscription",
        event_type="subscription",
        action="review_subscription",
        topic=topic,
        outcome="cancellation_inquiry" if risky else "active",
        text=(
            "현재 가입 상품의 해지 조건과 위약금을 확인했습니다."
            if risky
            else "현재 가입 상품이 정상 상태임을 확인했습니다."
        ),
        attributes={"product_family": "internet", "status": "active"},
    )


def _add_positive_journey(
    builder: _DatasetBuilder,
    rng: random.Random,
    customer_id: str,
) -> None:
    topic = "인터넷 장애"
    failed_at = _base_time(rng, latest_day=25)
    repeated_at = failed_at + timedelta(hours=6 + rng.randrange(12))
    feedback_at = repeated_at + timedelta(hours=1 + rng.randrange(3))
    voc_at = failed_at + timedelta(hours=40 + rng.randrange(24))

    builder.add_event(
        customer_id=customer_id,
        occurred_at=failed_at,
        source_id="search_history",
        event_type="search",
        action="search",
        topic=topic,
        outcome="failed",
        text="인터넷 연결 문제의 해결 방법을 찾지 못했습니다.",
        attributes={"result_count": 0, "is_repeat": False},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=repeated_at,
        source_id="search_history",
        event_type="search",
        action="repeat_search",
        topic=topic,
        outcome="failed",
        text="같은 인터넷 연결 문제를 다시 검색했지만 해결하지 못했습니다.",
        attributes={"result_count": 0, "is_repeat": True},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=feedback_at,
        source_id="search_feedback",
        event_type="feedback",
        action="submit_feedback",
        topic=topic,
        outcome="negative",
        text="안내 내용으로 문제가 해결되지 않았습니다.",
        attributes={"rating": 1},
    )
    _add_cross_source_context(
        builder,
        customer_id=customer_id,
        occurred_at=feedback_at + timedelta(hours=2),
        topic=topic,
        risky=True,
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=voc_at,
        source_id="voc",
        event_type="voc",
        action="contact_customer_service",
        topic=topic,
        outcome="unresolved",
        text="인터넷 장애가 지속되어 고객센터에 문의했습니다.",
        attributes={"contact_channel": "call"},
    )


def _add_successful_search(
    builder: _DatasetBuilder,
    rng: random.Random,
    customer_id: str,
    topic: str,
) -> None:
    searched_at = _base_time(rng, latest_day=25)
    builder.add_event(
        customer_id=customer_id,
        occurred_at=searched_at,
        source_id="search_history",
        event_type="search",
        action="search",
        topic=topic,
        outcome="success",
        text=f"{topic} 안내를 검색해 필요한 정보를 확인했습니다.",
        attributes={"result_count": 4, "is_repeat": False},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=searched_at + timedelta(hours=1),
        source_id="search_feedback",
        event_type="feedback",
        action="submit_feedback",
        topic=topic,
        outcome="positive",
        text="검색 안내가 도움이 되었습니다.",
        attributes={"rating": 5},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=searched_at + timedelta(hours=30),
        source_id="voc",
        event_type="voc",
        action="contact_customer_service",
        topic=TOPICS[(TOPICS.index(topic) + 1) % len(TOPICS)],
        outcome="resolved",
        text="별도 문의가 상담 중 해결되었습니다.",
        attributes={"contact_channel": "chat"},
    )


def _add_failure_without_repeat(
    builder: _DatasetBuilder,
    rng: random.Random,
    customer_id: str,
    topic: str,
) -> None:
    failed_at = _base_time(rng, latest_day=25)
    builder.add_event(
        customer_id=customer_id,
        occurred_at=failed_at,
        source_id="search_history",
        event_type="search",
        action="search",
        topic=topic,
        outcome="failed",
        text=f"{topic} 검색에서 원하는 답을 찾지 못했습니다.",
        attributes={"result_count": 0, "is_repeat": False},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=failed_at + timedelta(hours=2),
        source_id="search_feedback",
        event_type="feedback",
        action="submit_feedback",
        topic=topic,
        outcome="negative",
        text="검색 결과가 충분하지 않았습니다.",
        attributes={"rating": 2},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=failed_at + timedelta(hours=36),
        source_id="voc",
        event_type="voc",
        action="contact_customer_service",
        topic=topic,
        outcome="unresolved",
        text="관련 내용을 고객센터에 문의했습니다.",
        attributes={"contact_channel": "chat"},
    )


def _add_failure_with_late_voc(
    builder: _DatasetBuilder,
    rng: random.Random,
    customer_id: str,
    topic: str,
) -> None:
    failed_at = _base_time(rng, latest_day=23)
    repeated_at = failed_at + timedelta(hours=8 + rng.randrange(8))
    builder.add_event(
        customer_id=customer_id,
        occurred_at=failed_at,
        source_id="search_history",
        event_type="search",
        action="search",
        topic=topic,
        outcome="failed",
        text=f"{topic} 검색에서 원하는 답을 찾지 못했습니다.",
        attributes={"result_count": 0, "is_repeat": False},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=repeated_at,
        source_id="search_history",
        event_type="search",
        action="repeat_search",
        topic=topic,
        outcome="failed",
        text=f"{topic} 내용을 다시 검색했지만 해결하지 못했습니다.",
        attributes={"result_count": 0, "is_repeat": True},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=repeated_at + timedelta(hours=1),
        source_id="search_feedback",
        event_type="feedback",
        action="submit_feedback",
        topic=topic,
        outcome="negative",
        text="반복 검색 후에도 도움이 되지 않았습니다.",
        attributes={"rating": 1},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=failed_at + timedelta(hours=96 + rng.randrange(13)),
        source_id="voc",
        event_type="voc",
        action="contact_customer_service",
        topic=topic,
        outcome="unresolved",
        text="검색 며칠 뒤 고객센터에 문의했습니다.",
        attributes={"contact_channel": "call"},
    )


def _add_failure_without_voc(
    builder: _DatasetBuilder,
    rng: random.Random,
    customer_id: str,
    topic: str,
) -> None:
    failed_at = _base_time(rng, latest_day=25)
    repeated_at = failed_at + timedelta(hours=7 + rng.randrange(10))
    builder.add_event(
        customer_id=customer_id,
        occurred_at=failed_at,
        source_id="search_history",
        event_type="search",
        action="search",
        topic=topic,
        outcome="failed",
        text=f"{topic} 검색에서 원하는 답을 찾지 못했습니다.",
        attributes={"result_count": 0, "is_repeat": False},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=repeated_at,
        source_id="search_history",
        event_type="search",
        action="repeat_search",
        topic=topic,
        outcome="failed",
        text=f"{topic} 내용을 다시 검색했지만 해결하지 못했습니다.",
        attributes={"result_count": 0, "is_repeat": True},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=repeated_at + timedelta(hours=1),
        source_id="search_feedback",
        event_type="feedback",
        action="submit_feedback",
        topic=topic,
        outcome="negative",
        text="검색 결과에 만족하지 못했습니다.",
        attributes={"rating": 2},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=repeated_at + timedelta(hours=3),
        source_id="search_history",
        event_type="search",
        action="search",
        topic=TOPICS[(TOPICS.index(topic) + 2) % len(TOPICS)],
        outcome="success",
        text="다른 주제의 검색은 정상적으로 완료했습니다.",
        attributes={"result_count": 3, "is_repeat": False},
    )
    builder.add_event(
        customer_id=customer_id,
        occurred_at=repeated_at + timedelta(hours=30),
        source_id="voc",
        event_type="voc",
        action="contact_customer_service",
        topic=TOPICS[(TOPICS.index(topic) + 1) % len(TOPICS)],
        outcome="resolved",
        text="분석 대상과 무관한 문의가 상담 중 해결되었습니다.",
        attributes={"contact_channel": "chat", "noise": True},
    )


def _add_generic_analysis_patterns(
    builder: _DatasetBuilder,
    customer_ids: list[str],
) -> None:
    for index, customer_id in enumerate(customer_ids[12:18]):
        builder.add_event(
            customer_id=customer_id,
            occurred_at=WINDOW_START + timedelta(days=27, hours=index),
            source_id="search_feedback",
            event_type="feedback",
            action="submit_feedback",
            topic="요금제 변경",
            outcome="negative",
            text="요금제 변경 안내가 불분명해 불만을 남겼습니다.",
            attributes={"rating": 1},
        )

    for index, customer_id in enumerate(customer_ids[:12]):
        started_at = WINDOW_START + timedelta(days=26, minutes=index)
        builder.add_event(
            customer_id=customer_id,
            occurred_at=started_at,
            source_id="subscription",
            event_type="subscription",
            action="started",
            topic="가입",
            outcome="pending",
            text="가입 신청을 시작했습니다.",
            attributes={"stage": "application"},
        )
        if index < 7:
            builder.add_event(
                customer_id=customer_id,
                occurred_at=started_at + timedelta(hours=4),
                source_id="subscription",
                event_type="subscription",
                action="completed",
                topic="가입",
                outcome="success",
                text="가입 신청이 정상적으로 완료됐습니다.",
                attributes={"stage": "activated"},
            )


def generate_dataset(seed: int = 20260819) -> SyntheticDataset:
    """Build the deterministic 30-customer Journey demo dataset."""

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    if not 0 <= seed <= 99_999_999:
        raise ValueError("seed must be between 0 and 99999999")

    rng = random.Random(seed)
    builder = _DatasetBuilder(seed)
    customers = [f"CUST-{index:03d}" for index in range(1, 31)]
    near_miss_index = 0

    for customer_number, customer_id in enumerate(customers, start=1):
        builder.register_customer_identity_graph(customer_id)
        if customer_id in POSITIVE_CUSTOMER_IDS:
            _add_positive_journey(builder, rng, customer_id)
            continue

        topic = TOPICS[(customer_number - 1) % len(TOPICS)]
        scenario = near_miss_index % 4
        near_miss_index += 1
        if scenario == 0:
            _add_successful_search(builder, rng, customer_id, topic)
        elif scenario == 1:
            _add_failure_without_repeat(builder, rng, customer_id, topic)
        elif scenario == 2:
            _add_failure_with_late_voc(builder, rng, customer_id, topic)
        else:
            _add_failure_without_voc(builder, rng, customer_id, topic)
        _add_cross_source_context(
            builder,
            customer_id=customer_id,
            occurred_at=_base_time(rng, latest_day=25),
            topic=topic,
            risky=False,
        )

    _add_generic_analysis_patterns(builder, customers)
    builder.events.sort(key=lambda event: (event.occurred_at, event.event_id))
    builder.evidence.sort(key=lambda record: (record.occurred_at, record.evidence_id))
    return SyntheticDataset(
        customers=customers,
        events=builder.events,
        evidence=builder.evidence,
        identity_edges=builder.identity_edges,
        ground_truth_customer_ids=list(POSITIVE_CUSTOMER_IDS),
    )
