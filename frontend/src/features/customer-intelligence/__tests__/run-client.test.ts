import { afterEach, describe, expect, it, vi } from "vitest";

import type { RunRequest } from "../contracts";
import { RunClient, RunClientError } from "../run-client";
import {
  dynamicSourceId,
  genericArtifact,
  genericDocument,
  genericFact,
  genericGoal,
  genericNote,
  genericPlan,
  genericReport,
} from "./generic-fixtures";

const request: RunRequest = {
  question: "AI 검색 실패 후 문의한 고객이 몇 명이야?",
  start_at: "2026-07-20T00:00:00+09:00",
  end_at: "2026-08-19T00:00:00+09:00",
  enabled_sources: ["search_history", "search_feedback", "voc"],
};

const validReport = {
  analysis_type: "journey",
  scope: {
    start_at: request.start_at,
    end_at: request.end_at,
    enabled_sources: request.enabled_sources,
    population_description: "검색 실패 후 문의 Journey",
  },
  headline: "검색 실패 후 문의로 이어진 고객 6명",
  executive_summary: "완전한 Journey 패턴이 확인됐습니다.",
  metrics: [
    {
      label: "완전한 Journey 패턴 고객 수",
      value: 6,
      unit: "명",
      result_id: "match_journey_pattern:result",
    },
  ],
  findings: [],
  signal_contributions: [],
  ranked_customers: [
    {
      customer_id: "CUST-003",
      risk_score: 100,
      risk_level: "high",
      signals: [],
      evidence_ids: ["EVD-003"],
      last_event_at: "2026-08-01T10:00:00+09:00",
    },
  ],
  representative_journeys: [],
  representative_journey_ids: [],
  recommendations: [],
  sources_used: request.enabled_sources,
  limitations: [],
};

function frame(runId: string, id: number, type: string, payload: unknown): string {
  return `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify({
    run_id: runId,
    type,
    payload,
  })}\n\n`;
}

async function consume(client: RunClient, runId = "run-1") {
  const events = [];
  for await (const event of client.streamRunEvents(runId)) {
    events.push(event);
  }
  return events;
}

function responseStream(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

describe("RunClient", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("uses NEXT_PUBLIC_API_BASE_URL when no explicit base is provided", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://configured-api.test/");
    vi.resetModules();
    const { RunClient: EnvironmentRunClient } = await import("../run-client");
    const urls: string[] = [];
    const fetchImpl: typeof fetch = async (input) => {
      urls.push(String(input));
      return Response.json(
        {
          run_id: "run-1",
          status_url: "/api/runs/run-1",
          events_url: "/api/runs/run-1/events",
        },
        { status: 202 },
      );
    };

    await new EnvironmentRunClient({ fetchImpl }).createRun(request);

    expect(urls).toEqual(["http://configured-api.test/api/runs"]);
  });

  it("creates a run against a configurable API base", async () => {
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ input, init });
      return Response.json(
        {
          run_id: "run-1",
          status_url: "/api/runs/run-1",
          events_url: "/api/runs/run-1/events",
        },
        { status: 202 },
      );
    };
    const client = new RunClient({ apiBaseUrl: "http://api.test/", fetchImpl });

    const accepted = await client.createRun(request);

    expect(accepted.run_id).toBe("run-1");
    expect(calls).toHaveLength(1);
    expect(String(calls[0].input)).toBe("http://api.test/api/runs");
    expect(calls[0].init).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  });

  it("calls a browser-style fetch with the global receiver", async () => {
    const fetchImpl = vi.fn(function (this: unknown) {
      if (this !== globalThis) {
        throw new TypeError("Illegal invocation");
      }
      return Promise.resolve(
        Response.json(
          {
            run_id: "run-1",
            status_url: "/api/runs/run-1",
            events_url: "/api/runs/run-1/events",
          },
          { status: 202 },
        ),
      );
    }) as unknown as typeof fetch;
    vi.stubGlobal("fetch", fetchImpl);
    const client = new RunClient({ apiBaseUrl: "http://api.test" });

    await expect(client.createRun(request)).resolves.toMatchObject({
      run_id: "run-1",
    });
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it("throws a typed HTTP error with the public API detail", async () => {
    const fetchImpl: typeof fetch = async () =>
      Response.json({ detail: "Run not found" }, { status: 404 });
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    await expect(client.getRun("missing")).rejects.toMatchObject({
      name: "RunClientError",
      code: "http_error",
      status: 404,
      message: "Run not found",
    });
  });

  it("rejects an empty create-run response instead of casting it", async () => {
    const fetchImpl: typeof fetch = async () => Response.json({}, { status: 202 });
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    await expect(client.createRun(request)).rejects.toMatchObject({
      code: "invalid_response",
    });
  });

  it.each(["snapshot", "journey", "evidence"] as const)(
    "rejects an invalid %s detail response",
    async (endpoint) => {
      const fetchImpl: typeof fetch = async () => Response.json({});
      const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });
      const call =
        endpoint === "snapshot"
          ? client.getRun("run-1")
          : endpoint === "journey"
            ? client.getJourney("run-1", "CUST-003")
            : client.getEvidence("run-1", "EVD-003");

      await expect(call).rejects.toMatchObject({ code: "invalid_response" });
    },
  );

  it("reads split SSE chunks and unwraps the backend run envelope", async () => {
    const fetchImpl: typeof fetch = async () =>
      responseStream([
        'id: 1\nevent: plan\ndata: {"run_id":"run-1","type":"plan","payload":{"steps":["분',
        '석"]}}\n\nid: 2\nevent: done\ndata: {"run_id":"run-1","type":"done","payload":{"status":"completed"}}\n\n',
      ]);
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    const events = [];
    for await (const event of client.streamRunEvents("run-1")) {
      events.push(event);
    }

    expect(events).toEqual([
      { id: 1, type: "plan", data: { steps: ["분석"] } },
      { id: 2, type: "done", data: { status: "completed" } },
    ]);
  });

  it("accepts a structurally valid result report", async () => {
    const fetchImpl: typeof fetch = async () =>
      responseStream([
        frame("run-1", 1, "result", {
          agent_mode: "fixture",
          report: validReport,
        }),
        frame("run-1", 2, "done", { status: "completed" }),
      ]);
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    const events = await consume(client);

    expect(events[0]).toMatchObject({
      id: 1,
      type: "result",
      data: {
        agent_mode: "fixture",
        report: { ranked_customers: [{ customer_id: "CUST-003" }] },
      },
    });
  });

  it("accepts digital behavior and subscription source values at the stream boundary", async () => {
    const fetchImpl: typeof fetch = async () =>
      responseStream([
        frame("run-1", 1, "tool_started", {
          tool: "aggregate_events",
          source: ["digital_behavior", "subscription"],
        }),
        frame("run-1", 2, "done", { status: "completed" }),
      ]);
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    await expect(consume(client)).resolves.toEqual([
      {
        id: 1,
        type: "tool_started",
        data: {
          tool: "aggregate_events",
          source: ["digital_behavior", "subscription"],
        },
      },
      { id: 2, type: "done", data: { status: "completed" } },
    ]);
  });

  it.each([
    ["plan", { steps: [1] }],
    ["tool_started", { tool: 1, source: [] }],
    [
      "tool_completed",
      { tool: "catalog_sources", source: [], count: 1, duration_ms: 2 },
    ],
    ["validating", { result_ids: [1] }],
    ["result", {}],
    [
      "result",
      {
        agent_mode: "fixture",
        report: {
          ...validReport,
          ranked_customers: [
            { ...validReport.ranked_customers[0], risk_score: "100" },
          ],
        },
      },
    ],
    ["result", { report: { ...validReport, report_kind: "unknown" } }],
    ["error", { code: "run_failed" }],
    ["fallback", { reason: 42 }],
    ["done", { status: "bogus" }],
  ] as const)(
    "rejects malformed %s payloads at the stream boundary",
    async (type, payload) => {
      const fetchImpl: typeof fetch = async () =>
        responseStream([frame("run-1", 1, type, payload)]);
      const client = new RunClient({
        apiBaseUrl: "http://api.test",
        fetchImpl,
        maxReconnectAttempts: 0,
      });

      await expect(consume(client)).rejects.toMatchObject({
        name: "RunClientError",
        code: "protocol_error",
      });
    },
  );

  it("reconnects after an early EOF with Last-Event-ID and skips replay duplicates", async () => {
    const headers: Array<HeadersInit | undefined> = [];
    let call = 0;
    const fetchImpl: typeof fetch = async (_input, init) => {
      headers.push(init?.headers);
      call += 1;
      if (call === 1) {
        return responseStream([
          'id: 1\nevent: plan\ndata: {"run_id":"run-1","type":"plan","payload":{"steps":[]}}\n\n',
        ]);
      }
      return responseStream([
        'id: 1\nevent: plan\ndata: {"run_id":"run-1","type":"plan","payload":{"steps":[]}}\n\n',
        'id: 2\nevent: done\ndata: {"run_id":"run-1","type":"done","payload":{"status":"completed"}}\n\n',
      ]);
    };
    const client = new RunClient({
      apiBaseUrl: "http://api.test",
      fetchImpl,
      maxReconnectAttempts: 1,
    });

    const events = [];
    for await (const event of client.streamRunEvents("run-1")) {
      events.push(event);
    }

    expect(events.map((item) => item.id)).toEqual([1, 2]);
    expect(new Headers(headers[0]).has("Last-Event-ID")).toBe(false);
    expect(new Headers(headers[1]).get("Last-Event-ID")).toBe("1");
  });

  it("cancels a failed response body before reconnecting", async () => {
    const encoder = new TextEncoder();
    const checkpoints: string[] = [];
    let cancelled = false;
    let call = 0;
    const fetchImpl: typeof fetch = async () => {
      call += 1;
      if (call === 1) {
        return new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(
                encoder.encode(frame("run-1", 1, "done", { status: "bogus" })),
              );
            },
            cancel() {
              cancelled = true;
              checkpoints.push("cancelled");
            },
          }),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        );
      }
      checkpoints.push(`reconnected:${cancelled}`);
      return responseStream([
        frame("run-1", 1, "done", { status: "completed" }),
      ]);
    };
    const client = new RunClient({
      apiBaseUrl: "http://api.test",
      fetchImpl,
      maxReconnectAttempts: 1,
    });

    await expect(consume(client)).resolves.toMatchObject([
      { id: 1, type: "done", data: { status: "completed" } },
    ]);
    expect(checkpoints).toEqual(["cancelled", "reconnected:true"]);
  });

  it("uses run-scoped, encoded detail URLs", async () => {
    const urls: string[] = [];
    const fetchImpl: typeof fetch = async (input) => {
      urls.push(String(input));
      if (String(input).endsWith("/journey")) {
        return Response.json({
          result_id: "get_customer_journey:1",
          customer_id: "CUST/003",
          events: [],
          evidence_ids: [],
          stats: { scanned_rows: 0, returned_rows: 0 },
        });
      }
      return Response.json({
        result_id: "get_evidence:1",
        records: [],
        evidence_ids: [],
        stats: { scanned_rows: 0, returned_rows: 0 },
      });
    };
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    await client.getJourney("run/one", "CUST/003");
    await client.getEvidence("run/one", "EVD/003");

    expect(urls).toEqual([
      "http://api.test/api/runs/run%2Fone/customers/CUST%2F003/journey",
      "http://api.test/api/runs/run%2Fone/evidence/EVD%2F003",
    ]);
  });

  it("does not retry an aborted stream", async () => {
    let calls = 0;
    const fetchImpl: typeof fetch = async (_input, init) => {
      calls += 1;
      await new Promise<void>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal?.reason), {
          once: true,
        });
      });
      throw new Error("unreachable");
    };
    const controller = new AbortController();
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });
    const consume = async () => {
      for await (const _event of client.streamRunEvents("run-1", {
        signal: controller.signal,
      })) {
        // The request is aborted before any public event arrives.
      }
    };

    const pending = consume();
    controller.abort(new DOMException("stopped", "AbortError"));

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(calls).toBe(1);
  });

  it("reports a typed protocol error when a stream ends before done", async () => {
    const fetchImpl: typeof fetch = async () => responseStream([]);
    const client = new RunClient({
      apiBaseUrl: "http://api.test",
      fetchImpl,
      maxReconnectAttempts: 0,
    });

    const consume = async () => {
      for await (const _event of client.streamRunEvents("run-1")) {
        // No events are expected.
      }
    };

    await expect(consume()).rejects.toBeInstanceOf(RunClientError);
    await expect(consume()).rejects.toMatchObject({ code: "stream_ended" });
  });

  it("decodes a complete generic analysis stream with a dynamic source", async () => {
    const fetchImpl: typeof fetch = async () =>
      responseStream([
        frame("run-1", 1, "goal_created", { goal: genericGoal }),
        frame("run-1", 2, "plan_created", { plan: genericPlan }),
        frame("run-1", 3, "step_started", {
          step_id: "step-aggregate",
          primitive: "aggregate_events",
          started_at: "2026-08-20T01:00:00Z",
        }),
        frame("run-1", 4, "fact_created", {
          step_id: "step-aggregate",
          fact: genericFact,
        }),
        frame("run-1", 5, "analysis_note_created", { note: genericNote }),
        frame("run-1", 6, "step_completed", {
          step_id: "step-aggregate",
          status: "completed",
          result_ids: [genericFact.result_id],
          duration_ms: 1000,
        }),
        frame("run-1", 7, "report_validating", {
          fact_ids: [genericFact.fact_id],
        }),
        frame("run-1", 8, "result", { report: genericReport }),
        frame("run-1", 9, "done", { status: "completed" }),
      ]);
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    const events = await consume(client);

    expect(events.map((event) => event.type)).toEqual([
      "goal_created",
      "plan_created",
      "step_started",
      "fact_created",
      "analysis_note_created",
      "step_completed",
      "report_validating",
      "result",
      "done",
    ]);
    expect(events[0]).toMatchObject({
      type: "goal_created",
      data: { goal: { source_ids: [dynamicSourceId] } },
    });
    expect(events[7]).toMatchObject({
      type: "result",
      data: { report: { report_kind: "customer_signal" } },
    });
  });

  it("submits a clarification answer to the existing encoded run", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init });
      return Response.json(
        {
          run_id: "run/clarification",
          status_url: "/api/runs/run%2Fclarification",
          events_url: "/api/runs/run%2Fclarification/events",
        },
        { status: 202 },
      );
    };
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    const accepted = await client.submitClarification(
      "run/clarification",
      "Topic별로 비교해 줘",
    );

    expect(accepted.run_id).toBe("run/clarification");
    expect(calls).toEqual([
      {
        url: "http://api.test/api/runs/run%2Fclarification/clarification",
        init: expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answer: "Topic별로 비교해 줘" }),
        }),
      },
    ]);
  });

  it("decodes artifact list, detail, document and builds download URLs", async () => {
    const urls: string[] = [];
    const fetchImpl: typeof fetch = async (input) => {
      const url = String(input);
      urls.push(url);
      if (url.endsWith("/api/run-artifacts")) {
        return Response.json({
          artifacts: [
            {
              run_id: genericArtifact.run_id,
              status: "completed",
              question: genericArtifact.request.question,
              headline: genericReport.headline,
              created_at: genericArtifact.created_at,
              updated_at: genericArtifact.updated_at,
              completed_at: genericArtifact.completed_at,
              error_code: null,
            },
          ],
        });
      }
      if (url.endsWith("/document")) {
        return Response.json(genericDocument);
      }
      return Response.json(genericArtifact);
    };
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    const history = await client.listRunArtifacts();
    const artifact = await client.getRunArtifact(genericArtifact.run_id);
    const document = await client.getRunDocument(genericArtifact.run_id);

    expect(history.artifacts[0]).toMatchObject({
      run_id: genericArtifact.run_id,
      status: "completed",
    });
    expect(artifact).toMatchObject({
      schema_version: 1,
      last_event_id: 9,
      report: { report_kind: "customer_signal" },
      facts: [
        {
          payload: { provenance: { scope: { max_events: 1000 } } },
        },
      ],
    });
    expect(document).toMatchObject({
      document_kind: "run_artifact",
      provenance: { last_event_id: 9 },
    });
    expect(urls).toEqual([
      "http://api.test/api/run-artifacts",
      `http://api.test/api/run-artifacts/${genericArtifact.run_id}`,
      `http://api.test/api/run-artifacts/${genericArtifact.run_id}/document`,
    ]);
    expect(client.getRunDownloadUrl("run/one", "json")).toBe(
      "http://api.test/api/run-artifacts/run%2Fone/download.json",
    );
    expect(client.getRunDownloadUrl("run/one", "md")).toBe(
      "http://api.test/api/run-artifacts/run%2Fone/download.md",
    );
    expect(client.jsonDownloadUrl("run/one")).toBe(
      "http://api.test/api/run-artifacts/run%2Fone/download.json",
    );
    expect(client.markdownDownloadUrl("run/one")).toBe(
      "http://api.test/api/run-artifacts/run%2Fone/download.md",
    );
  });

  it("decodes a dynamic public source catalog", async () => {
    const fetchImpl: typeof fetch = async () =>
      Response.json({
        items: [
          {
            source_id: "support_chat_v2",
            label: "상담 채팅",
            description: "상담 채팅 이벤트",
            data_interval: {
              start_at: "2026-07-20T00:00:00Z",
              end_at: "2026-08-20T00:00:00Z",
            },
            refresh_cadence: "daily",
            supported_event_types: ["chat"],
            supported_topics: ["billing"],
            supported_outcomes: ["resolved"],
            dimensions: {
              topic: {
                semantic_type: "category",
                description: "상담 주제",
                allowed_values: ["billing"],
              },
            },
            measures: {
              duration_seconds: {
                semantic_type: "number",
                description: "상담 시간",
                unit: "초",
              },
            },
            capabilities: ["catalog_sources", "aggregate_events"],
            adapter_version: "adapter-v2",
            manifest_version: "manifest-v2",
          },
        ],
      });
    const client = new RunClient({ apiBaseUrl: "http://api.test", fetchImpl });

    const catalog = await client.listSources();

    expect(catalog.items[0]).toMatchObject({
      source_id: "support_chat_v2",
      capabilities: ["catalog_sources", "aggregate_events"],
    });
  });
});
