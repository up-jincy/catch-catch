import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CustomerJourneyResult,
  EvidenceResult,
  InsightReport,
  JourneyEvent,
  RunAccepted,
  RunRequest,
  RunStreamEvent,
} from "../contracts";
import { CustomerIntelligencePage } from "../CustomerIntelligencePage";
import type { CustomerIntelligenceClient } from "../use-run-controller";

const scope = {
  start_at: "2026-07-20T00:00:00+09:00",
  end_at: "2026-08-19T00:00:00+09:00",
  enabled_sources: ["search_history", "search_feedback", "voc"] as const,
  population_description: "검색 실패 후 같은 Topic의 후속 문의 Journey",
};

const journeyEvents: JourneyEvent[] = [
  {
    event_id: "EVT-003-1",
    evidence_id: "EVD-003-1",
    source_id: "search_history",
    occurred_at: "2026-08-01T09:00:00+09:00",
    event_type: "search",
    action: "AI 검색",
    topic: "해외 로밍",
    outcome: "failed",
    text: "로밍 데이터가 연결되지 않아요",
  },
  {
    event_id: "EVT-003-2",
    evidence_id: "EVD-003-2",
    source_id: "voc",
    occurred_at: "2026-08-02T11:30:00+09:00",
    event_type: "voc",
    action: "고객센터 문의",
    topic: "해외 로밍",
    outcome: "unresolved",
    text: "재검색 후에도 해결하지 못해 문의함",
  },
];

function customer(customerId: string, riskScore = 100) {
  return {
    customer_id: customerId,
    risk_score: riskScore,
    risk_level: riskScore >= 75 ? ("high" as const) : ("medium" as const),
    signals: [
      {
        code: "failed_search",
        label: "검색 실패",
        score: 25,
        evidence_ids: [`EVD-${customerId}-SEARCH`],
      },
      {
        code: "unresolved_voc",
        label: "미해결 VOC",
        score: 30,
        evidence_ids: [`EVD-${customerId}-VOC`],
      },
    ],
    evidence_ids: [`EVD-${customerId}-SEARCH`, `EVD-${customerId}-VOC`],
    last_event_at: "2026-08-02T11:30:00+09:00",
  };
}

const sixCustomers = ["CUST-003", "CUST-007", "CUST-011", "CUST-016", "CUST-022", "CUST-028"].map(
  (id, index) => customer(id, 100 - index),
);

const completedReport: InsightReport = {
  analysis_type: "journey",
  scope: { ...scope, enabled_sources: [...scope.enabled_sources] },
  headline: "검색 실패 후 문의로 이어진 고객 6명",
  executive_summary: "요청 기간에 완전한 Journey 패턴이 확인됐습니다.",
  metrics: [
    {
      label: "완전한 Journey 패턴 고객 수",
      value: 6,
      unit: "명",
      result_id: "match_journey_pattern:private-result",
    },
  ],
  findings: [
    {
      title: "완전한 Journey 패턴 확인",
      description: "검색 실패와 후속 문의 조건을 모두 충족했습니다.",
      confidence: "high",
      evidence_ids: ["EVD-003-2"],
    },
  ],
  signal_contributions: [
    {
      source_id: "search_history",
      score: 50,
      signals: [
        {
          code: "failed_search",
          label: "검색 실패와 재검색",
          score: 50,
          evidence_ids: ["EVD-003-1"],
        },
      ],
    },
    {
      source_id: "voc",
      score: 30,
      signals: [
        {
          code: "unresolved_voc",
          label: "미해결 문의",
          score: 30,
          evidence_ids: ["EVD-003-2"],
        },
      ],
    },
  ],
  ranked_customers: sixCustomers,
  representative_journeys: journeyEvents,
  representative_journey_ids: ["get_customer_journey:private-result"],
  recommendations: [
    {
      action_id: "care_call",
      title: "대표 고위험 고객 후속 확인",
      reason: "반복 검색과 미해결 문의가 연결됐습니다.",
      evidence_ids: ["EVD-003-2"],
    },
  ],
  sources_used: ["search_history", "search_feedback", "voc"],
  limitations: [],
};

const zeroReport: InsightReport = {
  ...completedReport,
  scope: {
    ...completedReport.scope,
    enabled_sources: ["search_history", "search_feedback"],
  },
  headline: "검색 실패 후 문의로 이어진 고객 0명",
  executive_summary: "활성 Source 범위에서는 완전한 Journey 패턴이 확인되지 않았습니다.",
  metrics: [
    {
      label: "완전한 Journey 패턴 고객 수",
      value: 0,
      unit: "명",
      result_id: "match_journey_pattern:zero",
    },
  ],
  findings: [],
  signal_contributions: [],
  ranked_customers: [],
  recommendations: [],
  sources_used: ["search_history", "search_feedback"],
  limitations: ["voc Source가 없어 완전한 패턴 판단이 제한됩니다."],
};

const cust003Journey: CustomerJourneyResult = {
  result_id: "get_customer_journey:CUST-003",
  customer_id: "CUST-003",
  events: [...journeyEvents].reverse(),
  evidence_ids: ["EVD-003-1", "EVD-003-2"],
  stats: { scanned_rows: 12, returned_rows: 2 },
};

const evidence: EvidenceResult = {
  result_id: "get_evidence:EVD-003-1",
  records: [
    {
      evidence_id: "EVD-003-1",
      source_id: "search_history",
      occurred_at: "2026-08-01T09:00:00+09:00",
      masked_customer_id: "CUS***003",
      summary: "AI 검색 실패 원본",
      raw_fields: {
        query: "로밍 데이터가 연결되지 않아요",
        customer_id: "CUS***003",
        rank: 1,
      },
    },
  ],
  evidence_ids: ["EVD-003-1"],
  stats: { scanned_rows: 1, returned_rows: 1 },
};

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

class EventQueue {
  private events: RunStreamEvent[] = [];
  private readers: Array<(event: RunStreamEvent) => void> = [];

  emit(event: RunStreamEvent) {
    const reader = this.readers.shift();
    if (reader) {
      reader(event);
    } else {
      this.events.push(event);
    }
  }

  async next(): Promise<RunStreamEvent> {
    const event = this.events.shift();
    return event ?? new Promise((resolve) => this.readers.push(resolve));
  }
}

class ControlledClient implements CustomerIntelligenceClient {
  readonly requests: RunRequest[] = [];
  readonly runSignals: AbortSignal[] = [];
  readonly queues = new Map<string, EventQueue>();
  readonly createRun = vi.fn(async (request: RunRequest, signal?: AbortSignal) => {
    this.requests.push(request);
    if (signal) this.runSignals.push(signal);
    const runId = `run-${this.requests.length}`;
    this.queues.set(runId, new EventQueue());
    return {
      run_id: runId,
      status_url: `/api/runs/${runId}`,
      events_url: `/api/runs/${runId}/events`,
    } satisfies RunAccepted;
  });
  readonly streamRunEvents = vi.fn(
    async function* (
      this: ControlledClient,
      runId: string,
      options?: { signal?: AbortSignal; lastEventId?: number },
    ) {
      if (options?.signal) this.runSignals.push(options.signal);
      const queue = this.queues.get(runId);
      if (!queue) throw new Error(`unknown run ${runId}`);
      while (true) {
        // Intentionally ignore abort so tests can prove late-event isolation.
        const event = await queue.next();
        yield event;
        if (event.type === "done") return;
      }
    }.bind(this),
  );
  readonly getJourney = vi.fn(
    async (
      _runId: string,
      _customerId: string,
      _signal?: AbortSignal,
    ): Promise<CustomerJourneyResult> => cust003Journey,
  );
  readonly getEvidence = vi.fn(
    async (
      _runId: string,
      _evidenceId: string,
      _signal?: AbortSignal,
    ): Promise<EvidenceResult> => evidence,
  );

  emit(runId: string, event: RunStreamEvent) {
    const queue = this.queues.get(runId);
    if (!queue) throw new Error(`unknown run ${runId}`);
    act(() => queue.emit(event));
  }
}

function event<T extends RunStreamEvent>(value: T): T {
  return value;
}

async function startRun(user: ReturnType<typeof userEvent.setup>) {
  await user.click(
    screen.getByRole("button", { name: /검색 실패 후 상담 전환 고객 찾기/ }),
  );
  await user.click(screen.getByRole("button", { name: /분석 시작/ }));
  await waitFor(() => expect(screen.getByText("분석 중")).toBeInTheDocument());
}

function finishRun(
  client: ControlledClient,
  runId: string,
  report: InsightReport = completedReport,
  options?: { fallback?: string },
) {
  client.emit(
    runId,
    event({ id: 1, type: "plan", data: { steps: ["Source 확인", "패턴 분석"] } }),
  );
  client.emit(
    runId,
    event({
      id: 2,
      type: "tool_started",
      data: { tool: "match_journey_pattern", source: report.scope.enabled_sources },
    }),
  );
  client.emit(
    runId,
    event({
      id: 3,
      type: "tool_completed",
      data: {
        tool: "match_journey_pattern",
        source: report.scope.enabled_sources,
        count: report.ranked_customers.length,
        duration_ms: 18,
        result_id: "private-tool-result-id",
      },
    }),
  );
  let nextId = 4;
  if (options?.fallback) {
    client.emit(
      runId,
      event({
        id: nextId,
        type: "fallback",
        data: {
          reason: options.fallback,
          from: "gemini",
          to: "fixture",
        },
      }),
    );
    nextId += 1;
  }
  client.emit(
    runId,
    event({ id: nextId, type: "validating", data: { result_ids: ["private"] } }),
  );
  client.emit(
    runId,
    event({
      id: nextId + 1,
      type: "result",
      data: { agent_mode: "fixture", report },
    }),
  );
  client.emit(
    runId,
    event({ id: nextId + 2, type: "done", data: { status: "completed" } }),
  );
}

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});

describe("CustomerIntelligencePage", () => {
  it("추천 질문부터 6명 Insight, 시간순 Journey, 마스킹 Evidence까지 탐색한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    render(<CustomerIntelligencePage client={client} />);

    await startRun(user);
    finishRun(client, "run-1");

    expect(await screen.findByRole("heading", { name: /고객 6명/ })).toBeInTheDocument();
    expect(screen.getByText(/18ms/)).toBeInTheDocument();
    expect(screen.queryByText("private-tool-result-id")).not.toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: /완전한 Journey 패턴 일치 고객/ }),
    ).toBeInTheDocument();

    await waitFor(() =>
      expect(client.getJourney).toHaveBeenCalledWith(
        "run-1",
        "CUST-003",
        expect.any(AbortSignal),
      ),
    );
    const timeline = await screen.findByRole("list", { name: /CUST-003 고객 Journey/ });
    const events = within(timeline).getAllByRole("listitem");
    expect(events[0]).toHaveTextContent("AI 검색");
    expect(events[1]).toHaveTextContent("고객센터 문의");

    const opener = within(events[0]).getByRole("button", { name: /근거 보기/ });
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: /Evidence/ });
    expect(within(dialog).getAllByText("CUS***003")).toHaveLength(2);
    expect(within(dialog).getByText("로밍 데이터가 연결되지 않아요")).toBeInTheDocument();
    expect(within(dialog).getByText("customer_id")).toBeInTheDocument();
  });

  it("필수 검색 이력을 유지하고 VOC 제외와 exclusive 종료일을 요청에 반영한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    render(<CustomerIntelligencePage client={client} />);

    const history = screen.getByRole("checkbox", { name: /검색 이력/ });
    const voc = screen.getByRole("checkbox", { name: /VOC/ });
    expect(history).toBeChecked();
    expect(history).toBeDisabled();
    expect(voc).toBeChecked();

    await user.click(voc);
    await startRun(user);

    expect(client.requests[0]).toEqual({
      question: "AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?",
      start_at: "2026-07-20T00:00:00+09:00",
      end_at: "2026-08-19T00:00:00+09:00",
      enabled_sources: ["search_history", "search_feedback"],
    });
    expect(screen.getByText(/종료일은 포함하지 않아요/)).toBeInTheDocument();
  });

  it("새 실행이 이전 모든 요청을 중단하고 이전 Run의 늦은 결과를 격리한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    render(<CustomerIntelligencePage client={client} />);

    await startRun(user);
    await user.click(screen.getByRole("button", { name: /분석 시작/ }));
    await waitFor(() => expect(client.createRun).toHaveBeenCalledTimes(2));
    expect(client.runSignals.slice(0, 2).some((signal) => signal.aborted)).toBe(true);

    finishRun(client, "run-1", completedReport);
    finishRun(client, "run-2", zeroReport);

    expect(await screen.findByRole("heading", { name: /고객 0명/ })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /고객 6명/ })).not.toBeInTheDocument();
    expect(client.getJourney).not.toHaveBeenCalled();
  });

  it("result가 와도 done 전에는 상세 동작을 열지 않고 완료 뒤 첫 Journey를 조회한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    render(<CustomerIntelligencePage client={client} />);

    await startRun(user);
    client.emit(
      "run-1",
      event({
        id: 1,
        type: "result",
        data: { agent_mode: "fixture", report: completedReport },
      }),
    );
    expect((await screen.findAllByText("검증된 Insight 구성")).length).toBeGreaterThan(
      0,
    );

    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "CUST-007 Journey 보기" }),
    ).not.toBeInTheDocument();
    expect(client.getJourney).not.toHaveBeenCalled();

    client.emit(
      "run-1",
      event({ id: 2, type: "done", data: { status: "completed" } }),
    );

    expect(await screen.findByRole("heading", { name: /고객 6명/ })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "CUST-007 Journey 보기" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(client.getJourney).toHaveBeenCalledTimes(1));
  });

  it("빈 질문과 잘못된 날짜는 서버 장애로 오해시키지 않고 입력 옆에서 안내한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    render(<CustomerIntelligencePage client={client} />);

    await user.click(screen.getByRole("button", { name: "분석 시작" }));

    expect(screen.getByRole("alert")).toHaveTextContent("분석할 질문을 입력해 주세요.");
    expect(
      screen.queryByRole("heading", { name: "분석 서버에 연결하지 못했어요" }),
    ).not.toBeInTheDocument();
    expect(client.createRun).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("분석 질문"), {
      target: { value: "검색 실패 후 문의한 고객을 찾아줘" },
    });
    fireEvent.change(screen.getByLabelText("종료일 · 미포함"), {
      target: { value: "2026-07-20" },
    });
    await user.click(screen.getByRole("button", { name: "분석 시작" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "종료일은 시작일보다 뒤여야 합니다.",
    );
    expect(
      screen.queryByRole("heading", { name: "분석 서버에 연결하지 못했어요" }),
    ).not.toBeInTheDocument();
    expect(client.createRun).not.toHaveBeenCalled();
  });

  it("고객 Journey 요청 순서가 뒤집혀도 현재 선택 고객만 표시한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    const first = deferred<CustomerJourneyResult>();
    const second = deferred<CustomerJourneyResult>();
    client.getJourney.mockImplementation(async (_runId, customerId) =>
      customerId === "CUST-003" ? first.promise : second.promise,
    );
    render(<CustomerIntelligencePage client={client} />);

    await startRun(user);
    finishRun(client, "run-1");
    await waitFor(() => expect(client.getJourney).toHaveBeenCalledTimes(1));
    await user.click(
      screen.getByRole("button", { name: "CUST-007 Journey 보기" }),
    );
    expect(
      screen.getByRole("button", { name: "CUST-007 Journey 보기" }),
    ).toHaveAttribute("aria-pressed", "true");

    second.resolve({
      ...cust003Journey,
      customer_id: "CUST-007",
      events: journeyEvents.map((item) => ({
        ...item,
        event_id: item.event_id.replace("003", "007"),
        text: "CUST-007 현재 Journey",
      })),
    });
    expect((await screen.findAllByText("CUST-007 현재 Journey")).length).toBe(2);

    first.resolve({
      ...cust003Journey,
      events: journeyEvents.map((item) => ({ ...item, text: "늦게 온 CUST-003" })),
    });
    await act(async () => first.promise);
    expect(screen.queryByText("늦게 온 CUST-003")).not.toBeInTheDocument();
    expect(screen.getAllByText("CUST-007 현재 Journey")).toHaveLength(2);
  });

  it("Evidence Drawer가 포커스를 가두고 Escape 뒤 opener로 복귀하며 늦은 응답을 버린다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    const slowEvidence = deferred<EvidenceResult>();
    client.getEvidence.mockImplementation(async () => slowEvidence.promise);
    render(<CustomerIntelligencePage client={client} />);

    await startRun(user);
    finishRun(client, "run-1");
    const timeline = await screen.findByRole("list", { name: /CUST-003 고객 Journey/ });
    const opener = within(timeline).getAllByRole("button", { name: /근거 보기/ })[0];
    await user.click(opener);

    const dialog = screen.getByRole("dialog", { name: /Evidence/ });
    const close = within(dialog).getByRole("button", { name: "Evidence 닫기" });
    expect(close).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
    await user.tab();
    expect(close).toHaveFocus();
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
    slowEvidence.resolve(evidence);
    await act(async () => slowEvidence.promise);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Gemini 실패 후 완료된 결과를 degraded Fixture Replay로 명확히 표시한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    render(<CustomerIntelligencePage client={client} />);

    await startRun(user);
    finishRun(client, "run-1", completedReport, { fallback: "Gemini 응답 시간 초과" });

    expect(await screen.findByText("제한 모드로 완료")).toBeInTheDocument();
    expect(screen.getByText("Fixture Replay")).toBeInTheDocument();
    expect(screen.getAllByText("Gemini 응답 시간 초과").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: /고객 6명/ })).toBeInTheDocument();
  });

  it("지원하지 않는 질문과 네트워크 오류를 서로 다른 복구 안내로 표시한다", async () => {
    const user = userEvent.setup();
    const unsupported = new ControlledClient();
    const { unmount } = render(<CustomerIntelligencePage client={unsupported} />);

    fireEvent.change(screen.getByLabelText("분석 질문"), {
      target: { value: "이번 달 신규 가입 매출을 예측해 줘" },
    });
    await user.click(screen.getByRole("button", { name: /분석 시작/ }));
    await waitFor(() => expect(unsupported.createRun).toHaveBeenCalled());
    unsupported.emit(
      "run-1",
      event({
        id: 1,
        type: "error",
        data: {
          code: "unsupported_question",
          message: "검색 실패와 고객 문의 Journey 질문만 지원합니다.",
        },
      }),
    );
    unsupported.emit(
      "run-1",
      event({ id: 2, type: "done", data: { status: "failed" } }),
    );

    expect(await screen.findByText("아직 이 질문은 지원하지 않아요")).toBeInTheDocument();
    expect(
      screen.getByText("검색 실패 후 고객센터 문의로 이어진 Journey"),
    ).toBeInTheDocument();
    unmount();

    const network = new ControlledClient();
    network.createRun.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<CustomerIntelligencePage client={network} />);
    await user.click(screen.getByRole("button", { name: /검색 실패 후 상담 전환 고객 찾기/ }));
    await user.click(screen.getByRole("button", { name: /분석 시작/ }));

    expect(await screen.findByText("분석 서버에 연결하지 못했어요")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 분석" })).toBeInTheDocument();
  });

  it("VOC 제외 결과는 후보를 만들지 않고 0명과 Source 한계를 정직하게 안내한다", async () => {
    const user = userEvent.setup();
    const client = new ControlledClient();
    render(<CustomerIntelligencePage client={client} />);

    await user.click(screen.getByRole("checkbox", { name: /VOC/ }));
    await startRun(user);
    finishRun(client, "run-1", zeroReport);

    expect(await screen.findByRole("heading", { name: /고객 0명/ })).toBeInTheDocument();
    expect(screen.getByText(/VOC Source를 켜고 다시 분석/)).toBeInTheDocument();
    expect(screen.getByText(/완전한 패턴 판단이 제한/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("CUST-003")).not.toBeInTheDocument();
  });
});
