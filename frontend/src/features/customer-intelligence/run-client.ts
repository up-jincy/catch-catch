import type {
  CustomerJourneyResult,
  EvidenceResult,
  RunAccepted,
  RunEventEnvelope,
  RunEventType,
  RunRequest,
  RunSnapshot,
  RunStreamEvent,
} from "./contracts";
import {
  SseParseError,
  createSseParser,
  type ParsedSseEvent,
} from "./parse-sse";

export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

export type RunClientErrorCode =
  | "http_error"
  | "invalid_response"
  | "network_error"
  | "protocol_error"
  | "stream_ended";

export class RunClientError extends Error {
  readonly code: RunClientErrorCode;
  readonly status?: number;

  constructor(
    code: RunClientErrorCode,
    message: string,
    options?: { status?: number; cause?: unknown },
  ) {
    super(message);
    this.name = "RunClientError";
    this.code = code;
    this.status = options?.status;
    if (options && "cause" in options) {
      this.cause = options.cause;
    }
  }
}

export interface RunClientOptions {
  apiBaseUrl?: string;
  fetchImpl?: typeof fetch;
  maxReconnectAttempts?: number;
}

export interface StreamRunOptions {
  signal?: AbortSignal;
  lastEventId?: number;
  maxReconnectAttempts?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const RUN_EVENT_TYPES = new Set<RunEventType>([
  "plan",
  "tool_started",
  "tool_completed",
  "validating",
  "result",
  "error",
  "fallback",
  "done",
]);

function abortReason(signal: AbortSignal | undefined, fallback: unknown): unknown {
  if (!signal?.aborted) {
    return fallback;
  }
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}

function decodeEvent(
  parsed: ParsedSseEvent<unknown>,
  expectedRunId: string,
): RunStreamEvent {
  if (!RUN_EVENT_TYPES.has(parsed.type as RunEventType) || !isRecord(parsed.data)) {
    throw new RunClientError("protocol_error", "SSE event shape is invalid");
  }

  const envelope = parsed.data as Partial<RunEventEnvelope>;
  if (
    envelope.run_id !== expectedRunId ||
    envelope.type !== parsed.type ||
    !isRecord(envelope.payload)
  ) {
    throw new RunClientError("protocol_error", "SSE run envelope is invalid");
  }

  return {
    id: parsed.id,
    type: parsed.type,
    data: envelope.payload,
  } as RunStreamEvent;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as unknown;
    if (isRecord(body) && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // The status remains actionable even when an upstream body is not JSON.
  }
  return `API request failed with status ${response.status}`;
}

function validateReconnectAttempts(value: number): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new RangeError("maxReconnectAttempts must be a non-negative integer");
  }
  return value;
}

export class RunClient {
  private readonly apiBaseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly maxReconnectAttempts: number;

  constructor(options: RunClientOptions = {}) {
    this.apiBaseUrl = (options.apiBaseUrl ?? DEFAULT_API_BASE_URL).replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.maxReconnectAttempts = validateReconnectAttempts(
      options.maxReconnectAttempts ?? 2,
    );
  }

  async createRun(request: RunRequest, signal?: AbortSignal): Promise<RunAccepted> {
    return this.requestJson<RunAccepted>(
      "/api/runs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      },
      signal,
    );
  }

  async getRun(runId: string, signal?: AbortSignal): Promise<RunSnapshot> {
    return this.requestJson<RunSnapshot>(
      `/api/runs/${encodeURIComponent(runId)}`,
      {},
      signal,
    );
  }

  async getJourney(
    runId: string,
    customerId: string,
    signal?: AbortSignal,
  ): Promise<CustomerJourneyResult> {
    return this.requestJson<CustomerJourneyResult>(
      `/api/runs/${encodeURIComponent(runId)}/customers/${encodeURIComponent(customerId)}/journey`,
      {},
      signal,
    );
  }

  async getEvidence(
    runId: string,
    evidenceId: string,
    signal?: AbortSignal,
  ): Promise<EvidenceResult> {
    return this.requestJson<EvidenceResult>(
      `/api/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}`,
      {},
      signal,
    );
  }

  async *streamRunEvents(
    runId: string,
    options: StreamRunOptions = {},
  ): AsyncGenerator<RunStreamEvent> {
    let cursor = options.lastEventId ?? 0;
    if (!Number.isInteger(cursor) || cursor < 0) {
      throw new RangeError("lastEventId must be a non-negative integer");
    }
    const maxReconnectAttempts = validateReconnectAttempts(
      options.maxReconnectAttempts ?? this.maxReconnectAttempts,
    );
    let reconnectAttempts = 0;

    while (true) {
      options.signal?.throwIfAborted();
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (cursor > 0) {
        headers["Last-Event-ID"] = String(cursor);
      }

      let response: Response;
      try {
        response = await this.fetchImpl(
          `${this.apiBaseUrl}/api/runs/${encodeURIComponent(runId)}/events`,
          { headers, signal: options.signal },
        );
      } catch (error) {
        if (options.signal?.aborted) {
          throw abortReason(options.signal, error);
        }
        if (reconnectAttempts >= maxReconnectAttempts) {
          throw new RunClientError("network_error", "SSE 연결에 실패했습니다.", {
            cause: error,
          });
        }
        reconnectAttempts += 1;
        continue;
      }

      if (!response.ok) {
        throw new RunClientError("http_error", await errorMessage(response), {
          status: response.status,
        });
      }
      if (!response.body) {
        throw new RunClientError("invalid_response", "SSE 응답 본문이 없습니다.");
      }
      const contentType = response.headers.get("content-type") ?? "";
      if (!contentType.toLowerCase().startsWith("text/event-stream")) {
        throw new RunClientError("invalid_response", "SSE 응답 형식이 아닙니다.");
      }

      const parser = createSseParser<unknown>();
      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      let terminal = false;
      let protocolFailure: unknown;

      try {
        while (true) {
          const { done, value } = await reader.read();
          const parsedEvents = done
            ? [
                ...parser.push(decoder.decode()),
                ...parser.finish(),
              ]
            : parser.push(decoder.decode(value, { stream: true }));

          for (const parsed of parsedEvents) {
            if (parsed.id <= cursor) {
              continue;
            }
            if (parsed.id !== cursor + 1) {
              throw new RunClientError(
                "protocol_error",
                `SSE event sequence skipped from ${cursor} to ${parsed.id}`,
              );
            }
            const event = decodeEvent(parsed, runId);
            cursor = event.id;
            yield event;
            if (event.type === "done") {
              terminal = true;
              return;
            }
          }

          if (done) {
            break;
          }
        }
      } catch (error) {
        if (options.signal?.aborted) {
          throw abortReason(options.signal, error);
        }
        protocolFailure = error;
      } finally {
        reader.releaseLock();
      }

      if (terminal) {
        return;
      }
      if (reconnectAttempts >= maxReconnectAttempts) {
        if (protocolFailure instanceof RunClientError) {
          throw protocolFailure;
        }
        if (protocolFailure instanceof SseParseError) {
          throw new RunClientError("protocol_error", protocolFailure.message, {
            cause: protocolFailure,
          });
        }
        throw new RunClientError(
          "stream_ended",
          "SSE 연결이 완료 이벤트 전에 종료됐습니다.",
          { cause: protocolFailure },
        );
      }
      reconnectAttempts += 1;
    }
  }

  private async requestJson<T>(
    path: string,
    init: RequestInit,
    signal?: AbortSignal,
  ): Promise<T> {
    signal?.throwIfAborted();
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.apiBaseUrl}${path}`, {
        ...init,
        signal,
      });
    } catch (error) {
      if (signal?.aborted) {
        throw abortReason(signal, error);
      }
      throw new RunClientError("network_error", "API 연결에 실패했습니다.", {
        cause: error,
      });
    }
    if (!response.ok) {
      throw new RunClientError("http_error", await errorMessage(response), {
        status: response.status,
      });
    }
    try {
      return (await response.json()) as T;
    } catch (error) {
      throw new RunClientError("invalid_response", "API 응답이 올바른 JSON이 아닙니다.", {
        cause: error,
      });
    }
  }
}
