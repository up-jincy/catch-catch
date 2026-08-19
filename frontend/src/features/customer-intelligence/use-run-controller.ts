"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import type {
  CustomerJourneyResult,
  EvidenceResult,
  RunAccepted,
  RunRequest,
  RunError,
  RunStreamEvent,
  SourceId,
} from "./contracts";
import { RunClient, RunClientError } from "./run-client";
import { initialRunState, runReducer } from "./run-reducer";

export const RECOMMENDED_QUESTION =
  "AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?";
export const DEFAULT_START_DATE = "2026-07-20";
export const DEFAULT_END_DATE = "2026-08-19";

const SOURCE_ORDER: SourceId[] = [
  "search_history",
  "search_feedback",
  "digital_behavior",
  "subscription",
  "voc",
];

export interface CustomerIntelligenceClient {
  createRun(request: RunRequest, signal?: AbortSignal): Promise<RunAccepted>;
  streamRunEvents(
    runId: string,
    options?: { signal?: AbortSignal; lastEventId?: number },
  ): AsyncIterable<RunStreamEvent>;
  getJourney(
    runId: string,
    customerId: string,
    signal?: AbortSignal,
  ): Promise<CustomerJourneyResult>;
  getEvidence(
    runId: string,
    evidenceId: string,
    signal?: AbortSignal,
  ): Promise<EvidenceResult>;
}

export type DetailState<T> =
  | { status: "idle"; key: null; data: null; error: null }
  | { status: "loading"; key: string; data: null; error: null }
  | { status: "success"; key: string; data: T; error: null }
  | { status: "empty"; key: string; data: T; error: null }
  | { status: "error"; key: string; data: null; error: string };

export type SubmissionErrorKind = "validation" | "network";

const idleDetail = <T,>(): DetailState<T> => ({
  status: "idle",
  key: null,
  data: null,
  error: null,
});

function orderedSources(sources: Iterable<SourceId>): SourceId[] {
  const enabled = new Set(sources);
  enabled.add("search_history");
  return SOURCE_ORDER.filter((source) => enabled.has(source));
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function detailError(area: "journey" | "evidence"): string {
  if (area === "journey") {
    return "고객 Journey를 불러오지 못했어요.";
  }
  return "Evidence를 불러오지 못했어요.";
}

function publicRunError(error: unknown): RunError {
  if (error instanceof RunClientError) {
    return {
      code: error.code,
      message: error.message,
    };
  }
  return {
    code: "network_error",
    message: "분석 서버에 연결하지 못했습니다. 연결을 확인해 주세요.",
  };
}

function dateAtSeoulMidnight(date: string): string {
  return `${date}T00:00:00+09:00`;
}

export function useRunController(providedClient?: CustomerIntelligenceClient) {
  const defaultClient = useMemo(() => new RunClient(), []);
  const client = providedClient ?? defaultClient;
  const [runState, dispatch] = useReducer(runReducer, initialRunState);
  const [question, setQuestion] = useState("");
  const [startDate, setStartDate] = useState(DEFAULT_START_DATE);
  const [endDate, setEndDate] = useState(DEFAULT_END_DATE);
  const [enabledSources, setEnabledSources] = useState<SourceId[]>([
    "search_history",
    "search_feedback",
    "digital_behavior",
    "subscription",
    "voc",
  ]);
  const [isCreating, setIsCreating] = useState(false);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [submissionErrorKind, setSubmissionErrorKind] =
    useState<SubmissionErrorKind | null>(null);
  const [submissionErrorCode, setSubmissionErrorCode] = useState<string | null>(
    null,
  );
  const [journeyState, setJourneyState] = useState<
    DetailState<CustomerJourneyResult>
  >(idleDetail);
  const [evidenceState, setEvidenceState] = useState<DetailState<EvidenceResult>>(
    idleDetail,
  );
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [evidenceOpener, setEvidenceOpener] = useState<HTMLElement | null>(null);

  const mountedRef = useRef(true);
  const runVersionRef = useRef(0);
  const journeyVersionRef = useRef(0);
  const evidenceVersionRef = useRef(0);
  const selectedCustomerRef = useRef<string | null>(null);
  const evidenceIdRef = useRef<string | null>(null);
  const runAbortRef = useRef<AbortController | null>(null);
  const journeyAbortRef = useRef<AbortController | null>(null);
  const evidenceAbortRef = useRef<AbortController | null>(null);

  const closeEvidence = useCallback(() => {
    evidenceVersionRef.current += 1;
    evidenceAbortRef.current?.abort();
    evidenceAbortRef.current = null;
    evidenceIdRef.current = null;
    setEvidenceId(null);
    setEvidenceOpener(null);
    setEvidenceState(idleDetail());
  }, []);

  const clearDetails = useCallback(() => {
    journeyVersionRef.current += 1;
    journeyAbortRef.current?.abort();
    journeyAbortRef.current = null;
    selectedCustomerRef.current = null;
    setJourneyState(idleDetail());
    closeEvidence();
  }, [closeEvidence]);

  const loadJourney = useCallback(
    async (runId: string, customerId: string, runVersion: number) => {
      journeyVersionRef.current += 1;
      const requestVersion = journeyVersionRef.current;
      journeyAbortRef.current?.abort();
      const controller = new AbortController();
      journeyAbortRef.current = controller;
      setJourneyState({
        status: "loading",
        key: customerId,
        data: null,
        error: null,
      });

      try {
        const result = await client.getJourney(runId, customerId, controller.signal);
        if (
          !mountedRef.current ||
          controller.signal.aborted ||
          runVersionRef.current !== runVersion ||
          journeyVersionRef.current !== requestVersion ||
          selectedCustomerRef.current !== customerId
        ) {
          return;
        }
        setJourneyState({
          status: result.events.length ? "success" : "empty",
          key: customerId,
          data: result,
          error: null,
        });
      } catch (error) {
        if (
          !mountedRef.current ||
          controller.signal.aborted ||
          isAbort(error) ||
          runVersionRef.current !== runVersion ||
          journeyVersionRef.current !== requestVersion
        ) {
          return;
        }
        setJourneyState({
          status: "error",
          key: customerId,
          data: null,
          error: detailError("journey"),
        });
      }
    },
    [client],
  );

  const loadEvidence = useCallback(
    async (runId: string, nextEvidenceId: string, runVersion: number) => {
      evidenceVersionRef.current += 1;
      const requestVersion = evidenceVersionRef.current;
      evidenceAbortRef.current?.abort();
      const controller = new AbortController();
      evidenceAbortRef.current = controller;
      setEvidenceState({
        status: "loading",
        key: nextEvidenceId,
        data: null,
        error: null,
      });

      try {
        const result = await client.getEvidence(
          runId,
          nextEvidenceId,
          controller.signal,
        );
        if (
          !mountedRef.current ||
          controller.signal.aborted ||
          runVersionRef.current !== runVersion ||
          evidenceVersionRef.current !== requestVersion ||
          evidenceIdRef.current !== nextEvidenceId
        ) {
          return;
        }
        setEvidenceState({
          status: result.records.length ? "success" : "empty",
          key: nextEvidenceId,
          data: result,
          error: null,
        });
      } catch (error) {
        if (
          !mountedRef.current ||
          controller.signal.aborted ||
          isAbort(error) ||
          runVersionRef.current !== runVersion ||
          evidenceVersionRef.current !== requestVersion ||
          evidenceIdRef.current !== nextEvidenceId
        ) {
          return;
        }
        setEvidenceState({
          status: "error",
          key: nextEvidenceId,
          data: null,
          error: detailError("evidence"),
        });
      }
    },
    [client],
  );

  const run = useCallback(async () => {
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion) {
      setSubmissionError("분석할 질문을 입력해 주세요.");
      setSubmissionErrorKind("validation");
      setSubmissionErrorCode("local_validation");
      return;
    }
    if (!startDate || !endDate || startDate >= endDate) {
      setSubmissionError("종료일은 시작일보다 뒤여야 합니다.");
      setSubmissionErrorKind("validation");
      setSubmissionErrorCode("local_validation");
      return;
    }

    runVersionRef.current += 1;
    const runVersion = runVersionRef.current;
    runAbortRef.current?.abort();
    const controller = new AbortController();
    runAbortRef.current = controller;
    clearDetails();
    dispatch({ kind: "reset" });
    setSubmissionError(null);
    setSubmissionErrorKind(null);
    setSubmissionErrorCode(null);
    setIsCreating(true);

    const request: RunRequest = {
      question: normalizedQuestion,
      start_at: dateAtSeoulMidnight(startDate),
      end_at: dateAtSeoulMidnight(endDate),
      enabled_sources: orderedSources(enabledSources),
    };

    let accepted: RunAccepted;
    try {
      accepted = await client.createRun(request, controller.signal);
    } catch (error) {
      if (
        mountedRef.current &&
        runVersionRef.current === runVersion &&
        !controller.signal.aborted &&
        !isAbort(error)
      ) {
        const failure = publicRunError(error);
        setSubmissionError(failure.message);
        setSubmissionErrorKind("network");
        setSubmissionErrorCode(failure.code);
      }
      if (mountedRef.current && runVersionRef.current === runVersion) {
        setIsCreating(false);
      }
      return;
    }

    if (
      !mountedRef.current ||
      controller.signal.aborted ||
      runVersionRef.current !== runVersion
    ) {
      return;
    }
    setIsCreating(false);
    dispatch({ kind: "start", runId: accepted.run_id });

    let firstCustomerId: string | null = null;
    let terminalCompleted = false;
    try {
      for await (const streamEvent of client.streamRunEvents(accepted.run_id, {
        signal: controller.signal,
      })) {
        if (
          !mountedRef.current ||
          controller.signal.aborted ||
          runVersionRef.current !== runVersion
        ) {
          continue;
        }
        dispatch({
          kind: "event",
          runId: accepted.run_id,
          event: streamEvent,
        });
        if (streamEvent.type === "result") {
          firstCustomerId =
            streamEvent.data.report.ranked_customers[0]?.customer_id ?? null;
          selectedCustomerRef.current = firstCustomerId;
        }
        if (streamEvent.type === "done") {
          terminalCompleted = streamEvent.data.status === "completed";
          break;
        }
      }
    } catch (error) {
      if (
        mountedRef.current &&
        runVersionRef.current === runVersion &&
        !controller.signal.aborted &&
        !isAbort(error)
      ) {
        const failure = publicRunError(error);
        dispatch({
          kind: "failed",
          runId: accepted.run_id,
          error: failure,
        });
      }
      return;
    }

    if (
      terminalCompleted &&
      firstCustomerId &&
      mountedRef.current &&
      runVersionRef.current === runVersion &&
      !controller.signal.aborted
    ) {
      await loadJourney(accepted.run_id, firstCustomerId, runVersion);
    }
  }, [
    clearDetails,
    client,
    enabledSources,
    endDate,
    loadJourney,
    question,
    startDate,
  ]);

  const toggleSource = useCallback((source: SourceId) => {
    if (source === "search_history") return;
    setEnabledSources((current) => {
      const next = new Set(current);
      if (next.has(source)) next.delete(source);
      else next.add(source);
      return orderedSources(next);
    });
  }, []);

  const selectCustomer = useCallback(
    (customerId: string) => {
      if (
        !runState.runId ||
        (runState.phase !== "completed" && runState.phase !== "degraded")
      ) {
        return;
      }
      selectedCustomerRef.current = customerId;
      dispatch({ kind: "select_customer", customerId });
      closeEvidence();
      void loadJourney(runState.runId, customerId, runVersionRef.current);
    },
    [closeEvidence, loadJourney, runState.phase, runState.runId],
  );

  const retryJourney = useCallback(() => {
    if (
      !runState.runId ||
      !selectedCustomerRef.current ||
      (runState.phase !== "completed" && runState.phase !== "degraded")
    ) {
      return;
    }
    void loadJourney(
      runState.runId,
      selectedCustomerRef.current,
      runVersionRef.current,
    );
  }, [loadJourney, runState.phase, runState.runId]);

  const openEvidence = useCallback(
    (nextEvidenceId: string, opener: HTMLElement) => {
      if (
        !runState.runId ||
        (runState.phase !== "completed" && runState.phase !== "degraded")
      ) {
        return;
      }
      evidenceIdRef.current = nextEvidenceId;
      setEvidenceId(nextEvidenceId);
      setEvidenceOpener(opener);
      void loadEvidence(
        runState.runId,
        nextEvidenceId,
        runVersionRef.current,
      );
    },
    [loadEvidence, runState.phase, runState.runId],
  );

  const retryEvidence = useCallback(() => {
    if (
      !runState.runId ||
      !evidenceIdRef.current ||
      (runState.phase !== "completed" && runState.phase !== "degraded")
    ) {
      return;
    }
    void loadEvidence(
      runState.runId,
      evidenceIdRef.current,
      runVersionRef.current,
    );
  }, [loadEvidence, runState.phase, runState.runId]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      runVersionRef.current += 1;
      journeyVersionRef.current += 1;
      evidenceVersionRef.current += 1;
      runAbortRef.current?.abort();
      journeyAbortRef.current?.abort();
      evidenceAbortRef.current?.abort();
    };
  }, []);

  return {
    runState,
    question,
    setQuestion,
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    enabledSources,
    toggleSource,
    isCreating,
    submissionError,
    submissionErrorKind,
    submissionErrorCode,
    run,
    selectCustomer,
    journeyState,
    retryJourney,
    evidenceId,
    evidenceState,
    evidenceOpener,
    openEvidence,
    closeEvidence,
    retryEvidence,
  };
}
