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
  AnyRunStreamEvent,
  ArtifactDocument,
  ArtifactListResponse,
  ArtifactSummary,
  CustomerJourneyResult,
  EvidenceResult,
  PublicSourceList,
  RunAccepted,
  RunArtifact,
  RunError,
  RunRequest,
  SourceId,
} from "./contracts";
import { RunClient, RunClientError } from "./run-client";
import { initialRunState, runReducer } from "./run-reducer";
import {
  KNOWN_SOURCE_OPTIONS,
  type SourceOption,
} from "./source-catalog";

export const RECOMMENDED_QUESTION =
  "AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?";
export const DEFAULT_START_DATE = "2026-07-20";
export const DEFAULT_END_DATE = "2026-08-19";

export interface CustomerIntelligenceClient {
  createRun(request: RunRequest, signal?: AbortSignal): Promise<RunAccepted>;
  submitClarification(
    runId: string,
    answer: string,
    signal?: AbortSignal,
  ): Promise<RunAccepted>;
  streamRunEvents(
    runId: string,
    options?: { signal?: AbortSignal; lastEventId?: number },
  ): AsyncIterable<AnyRunStreamEvent>;
  listSources(signal?: AbortSignal): Promise<PublicSourceList>;
  listRunArtifacts(signal?: AbortSignal): Promise<ArtifactListResponse>;
  getRunArtifact(runId: string, signal?: AbortSignal): Promise<RunArtifact>;
  getRunDocument(runId: string, signal?: AbortSignal): Promise<ArtifactDocument>;
  jsonDownloadUrl(runId: string): string;
  markdownDownloadUrl(runId: string): string;
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
export type LoadStatus = "idle" | "loading" | "success" | "error";

const idleDetail = <T,>(): DetailState<T> => ({
  status: "idle",
  key: null,
  data: null,
  error: null,
});

function mergeSourceOptions(
  current: readonly SourceOption[],
  response: PublicSourceList,
): SourceOption[] {
  const byId = new Map(current.map((source) => [source.id, source]));
  for (const manifest of response.items) {
    byId.set(manifest.source_id, {
      id: manifest.source_id,
      label: manifest.label,
      note: manifest.description,
    });
  }
  const known = KNOWN_SOURCE_OPTIONS.map(
    (source) => byId.get(source.id) ?? source,
  );
  const knownIds = new Set(known.map((source) => source.id));
  return [
    ...known,
    ...response.items
      .filter((manifest) => !knownIds.has(manifest.source_id))
      .map((manifest) => byId.get(manifest.source_id)!)
      .sort((left, right) => left.label.localeCompare(right.label, "ko")),
  ];
}

function orderedSources(
  sources: Iterable<SourceId>,
  sourceOptions: readonly SourceOption[],
): SourceId[] {
  const enabled = new Set(sources);
  enabled.add("search_history");
  const ordered = sourceOptions
    .map((source) => source.id)
    .filter((source) => enabled.has(source));
  const known = new Set(ordered);
  for (const source of [...enabled].sort()) {
    if (!known.has(source)) ordered.push(source);
  }
  return ordered;
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function detailError(area: "journey" | "evidence"): string {
  return area === "journey"
    ? "고객 Journey를 불러오지 못했어요."
    : "Evidence를 불러오지 못했어요.";
}

function publicRunError(error: unknown): RunError {
  if (error instanceof RunClientError) {
    return { code: error.code, message: error.message };
  }
  return {
    code: "network_error",
    message: "분석 서버에 연결하지 못했습니다. 연결을 확인해 주세요.",
  };
}

function dateAtSeoulMidnight(date: string): string {
  return `${date}T00:00:00+09:00`;
}

function inputDate(timestamp: string): string {
  return timestamp.slice(0, 10);
}

function isGenericEvent(event: AnyRunStreamEvent): boolean {
  return (
    event.type === "run_started" ||
    event.type === "goal_created" ||
    event.type === "clarification_required" ||
    event.type === "plan_created" ||
    event.type === "plan_revised" ||
    event.type === "step_started" ||
    event.type === "fact_created" ||
    event.type === "analysis_note_created" ||
    event.type === "step_completed" ||
    event.type === "report_validating" ||
    (event.type === "result" &&
      event.data.report.report_kind === "customer_signal")
  );
}

export function useRunController(providedClient?: CustomerIntelligenceClient) {
  const defaultClient = useMemo(() => new RunClient(), []);
  const client = providedClient ?? defaultClient;
  const [runState, dispatch] = useReducer(runReducer, initialRunState);
  const [question, setQuestion] = useState("");
  const [startDate, setStartDate] = useState(DEFAULT_START_DATE);
  const [endDate, setEndDate] = useState(DEFAULT_END_DATE);
  const [sourceOptions, setSourceOptions] = useState<SourceOption[]>([
    ...KNOWN_SOURCE_OPTIONS,
  ]);
  const [enabledSources, setEnabledSources] = useState<SourceId[]>(
    KNOWN_SOURCE_OPTIONS.map((source) => source.id),
  );
  const [isCreating, setIsCreating] = useState(false);
  const [submissionError, setSubmissionError] = useState<string | null>(null);
  const [submissionErrorKind, setSubmissionErrorKind] =
    useState<SubmissionErrorKind | null>(null);
  const [submissionErrorCode, setSubmissionErrorCode] = useState<string | null>(
    null,
  );
  const [clarificationError, setClarificationError] = useState<string | null>(
    null,
  );
  const [historyItems, setHistoryItems] = useState<ArtifactSummary[]>([]);
  const [historyStatus, setHistoryStatus] = useState<LoadStatus>("idle");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [document, setDocument] = useState<ArtifactDocument | null>(null);
  const [documentStatus, setDocumentStatus] = useState<LoadStatus>("idle");
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
  const historyVersionRef = useRef(0);
  const documentVersionRef = useRef(0);
  const selectedCustomerRef = useRef<string | null>(null);
  const evidenceIdRef = useRef<string | null>(null);
  const activeStreamRunRef = useRef<string | null>(null);
  const runAbortRef = useRef<AbortController | null>(null);
  const journeyAbortRef = useRef<AbortController | null>(null);
  const evidenceAbortRef = useRef<AbortController | null>(null);
  const sourcesAbortRef = useRef<AbortController | null>(null);
  const historyAbortRef = useRef<AbortController | null>(null);
  const documentAbortRef = useRef<AbortController | null>(null);

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
      setJourneyState({ status: "loading", key: customerId, data: null, error: null });
      try {
        const result = await client.getJourney(runId, customerId, controller.signal);
        if (
          !mountedRef.current || controller.signal.aborted ||
          runVersionRef.current !== runVersion ||
          journeyVersionRef.current !== requestVersion ||
          selectedCustomerRef.current !== customerId
        ) return;
        setJourneyState({
          status: result.events.length ? "success" : "empty",
          key: customerId,
          data: result,
          error: null,
        });
      } catch (error) {
        if (
          !mountedRef.current || controller.signal.aborted || isAbort(error) ||
          runVersionRef.current !== runVersion ||
          journeyVersionRef.current !== requestVersion
        ) return;
        setJourneyState({
          status: "error", key: customerId, data: null,
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
        status: "loading", key: nextEvidenceId, data: null, error: null,
      });
      try {
        const result = await client.getEvidence(runId, nextEvidenceId, controller.signal);
        if (
          !mountedRef.current || controller.signal.aborted ||
          runVersionRef.current !== runVersion ||
          evidenceVersionRef.current !== requestVersion ||
          evidenceIdRef.current !== nextEvidenceId
        ) return;
        setEvidenceState({
          status: result.records.length ? "success" : "empty",
          key: nextEvidenceId,
          data: result,
          error: null,
        });
      } catch (error) {
        if (
          !mountedRef.current || controller.signal.aborted || isAbort(error) ||
          runVersionRef.current !== runVersion ||
          evidenceVersionRef.current !== requestVersion
        ) return;
        setEvidenceState({
          status: "error", key: nextEvidenceId, data: null,
          error: detailError("evidence"),
        });
      }
    },
    [client],
  );

  const refreshHistory = useCallback(async () => {
    historyVersionRef.current += 1;
    const requestVersion = historyVersionRef.current;
    historyAbortRef.current?.abort();
    const controller = new AbortController();
    historyAbortRef.current = controller;
    setHistoryStatus("loading");
    try {
      const response = await client.listRunArtifacts(controller.signal);
      if (
        !mountedRef.current || controller.signal.aborted ||
        historyVersionRef.current !== requestVersion
      ) return;
      setHistoryItems(response.artifacts);
      setHistoryStatus("success");
    } catch (error) {
      if (
        !mountedRef.current || controller.signal.aborted || isAbort(error) ||
        historyVersionRef.current !== requestVersion
      ) return;
      setHistoryStatus("error");
    }
  }, [client]);

  const loadDocument = useCallback(
    async (runId: string, runVersion: number) => {
      documentVersionRef.current += 1;
      const requestVersion = documentVersionRef.current;
      documentAbortRef.current?.abort();
      const controller = new AbortController();
      documentAbortRef.current = controller;
      setDocumentStatus("loading");
      try {
        const result = await client.getRunDocument(runId, controller.signal);
        if (
          !mountedRef.current || controller.signal.aborted ||
          runVersionRef.current !== runVersion ||
          documentVersionRef.current !== requestVersion
        ) return;
        setDocument(result);
        setDocumentStatus("success");
      } catch (error) {
        if (
          !mountedRef.current || controller.signal.aborted || isAbort(error) ||
          runVersionRef.current !== runVersion ||
          documentVersionRef.current !== requestVersion
        ) return;
        setDocument(null);
        setDocumentStatus("error");
      }
    },
    [client],
  );

  const consumeStream = useCallback(
    async (
      runId: string,
      controller: AbortController,
      runVersion: number,
      lastEventId = 0,
      startsGeneric = false,
    ) => {
      activeStreamRunRef.current = runId;
      let firstLegacyCustomerId: string | null = null;
      let genericRun = startsGeneric;
      try {
        for await (const streamEvent of client.streamRunEvents(runId, {
          signal: controller.signal,
          lastEventId,
        })) {
          if (
            !mountedRef.current || controller.signal.aborted ||
            runVersionRef.current !== runVersion
          ) continue;
          genericRun ||= isGenericEvent(streamEvent);
          dispatch({ kind: "event", runId, event: streamEvent });
          if (
            streamEvent.type === "result" &&
            streamEvent.data.report.report_kind !== "customer_signal"
          ) {
            firstLegacyCustomerId =
              streamEvent.data.report.ranked_customers[0]?.customer_id ?? null;
            selectedCustomerRef.current = firstLegacyCustomerId;
          }
          if (streamEvent.type === "done") {
            if (streamEvent.data.status !== "failed") {
              if (genericRun) {
                void loadDocument(runId, runVersion);
              } else if (
                streamEvent.data.status === "completed" && firstLegacyCustomerId
              ) {
                void loadJourney(runId, firstLegacyCustomerId, runVersion);
              }
            }
            void refreshHistory();
            break;
          }
        }
      } catch (error) {
        if (
          mountedRef.current && runVersionRef.current === runVersion &&
          !controller.signal.aborted && !isAbort(error)
        ) {
          dispatch({ kind: "failed", runId, error: publicRunError(error) });
        }
      } finally {
        if (activeStreamRunRef.current === runId) activeStreamRunRef.current = null;
      }
    },
    [client, loadDocument, loadJourney, refreshHistory],
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
    documentVersionRef.current += 1;
    documentAbortRef.current?.abort();
    setDocument(null);
    setDocumentStatus("idle");
    setSelectedRunId(null);
    dispatch({ kind: "reset" });
    setSubmissionError(null);
    setSubmissionErrorKind(null);
    setSubmissionErrorCode(null);
    setClarificationError(null);
    setIsCreating(true);

    const request: RunRequest = {
      question: normalizedQuestion,
      start_at: dateAtSeoulMidnight(startDate),
      end_at: dateAtSeoulMidnight(endDate),
      enabled_sources: orderedSources(enabledSources, sourceOptions),
    };

    let accepted: RunAccepted;
    try {
      accepted = await client.createRun(request, controller.signal);
    } catch (error) {
      if (
        mountedRef.current && runVersionRef.current === runVersion &&
        !controller.signal.aborted && !isAbort(error)
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
      !mountedRef.current || controller.signal.aborted ||
      runVersionRef.current !== runVersion
    ) return;
    setIsCreating(false);
    setSelectedRunId(accepted.run_id);
    dispatch({ kind: "start", runId: accepted.run_id, request });
    await consumeStream(accepted.run_id, controller, runVersion);
  }, [
    clearDetails, client, consumeStream, enabledSources, endDate, question,
    sourceOptions, startDate,
  ]);

  const submitClarification = useCallback(
    async (answer: string) => {
      const runId = runState.runId;
      const clarificationId = runState.clarification?.clarification_id;
      if (!runId || !clarificationId) return;
      setClarificationError(null);
      let controller = runAbortRef.current;
      if (!controller || controller.signal.aborted) {
        controller = new AbortController();
        runAbortRef.current = controller;
      }
      const runVersion = runVersionRef.current;
      try {
        await client.submitClarification(runId, answer, controller.signal);
        if (
          !mountedRef.current || controller.signal.aborted ||
          runVersionRef.current !== runVersion
        ) return;
        dispatch({
          kind: "clarification_submitted", runId, clarificationId, answer,
        });
        if (activeStreamRunRef.current !== runId) {
          void consumeStream(
            runId, controller, runVersion, runState.lastEventId, true,
          );
        }
      } catch (error) {
        if (
          mountedRef.current && !controller.signal.aborted &&
          runVersionRef.current === runVersion && !isAbort(error)
        ) setClarificationError(publicRunError(error).message);
      }
    },
    [client, consumeStream, runState.clarification, runState.lastEventId, runState.runId],
  );

  const selectHistory = useCallback(
    async (runId: string) => {
      runVersionRef.current += 1;
      const runVersion = runVersionRef.current;
      runAbortRef.current?.abort();
      const controller = new AbortController();
      runAbortRef.current = controller;
      clearDetails();
      setSelectedRunId(runId);
      setIsCreating(false);
      setSubmissionError(null);
      setSubmissionErrorKind(null);
      setSubmissionErrorCode(null);
      setClarificationError(null);
      setDocument(null);
      setDocumentStatus("loading");
      dispatch({ kind: "start", runId });
      try {
        const [artifact, artifactDocument] = await Promise.all([
          client.getRunArtifact(runId, controller.signal),
          client.getRunDocument(runId, controller.signal),
        ]);
        if (
          !mountedRef.current || controller.signal.aborted ||
          runVersionRef.current !== runVersion
        ) return;
        dispatch({ kind: "hydrate_artifact", artifact });
        setQuestion(artifact.request.question);
        setStartDate(inputDate(artifact.request.start_at));
        setEndDate(inputDate(artifact.request.end_at));
        setEnabledSources(artifact.request.enabled_sources);
        setDocument(artifactDocument);
        setDocumentStatus("success");
        const genericArtifact =
          Boolean(artifact.goal || artifact.plan || artifact.facts.length || artifact.notes.length) ||
          artifact.report?.report_kind === "customer_signal";
        if (artifact.status === "queued" || artifact.status === "running") {
          void consumeStream(
            runId, controller, runVersion, artifact.last_event_id, genericArtifact,
          );
        } else if (
          artifact.status === "completed" && !genericArtifact &&
          artifact.report?.ranked_customers[0]
        ) {
          const customerId = artifact.report.ranked_customers[0].customer_id;
          selectedCustomerRef.current = customerId;
          void loadJourney(runId, customerId, runVersion);
        }
      } catch (error) {
        if (
          !mountedRef.current || controller.signal.aborted || isAbort(error) ||
          runVersionRef.current !== runVersion
        ) return;
        setDocumentStatus("error");
        dispatch({
          kind: "failed",
          runId,
          error: {
            code: "artifact_load_failed",
            message: "저장된 Run 기록을 불러오지 못했습니다.",
          },
        });
      }
    },
    [clearDetails, client, consumeStream, loadJourney],
  );

  const toggleSource = useCallback(
    (source: SourceId) => {
      if (source === "search_history") return;
      setEnabledSources((current) => {
        const next = new Set(current);
        if (next.has(source)) next.delete(source);
        else next.add(source);
        return orderedSources(next, sourceOptions);
      });
    },
    [sourceOptions],
  );

  const selectCustomer = useCallback(
    (customerId: string) => {
      if (
        !runState.runId ||
        (runState.phase !== "completed" && runState.phase !== "degraded")
      ) return;
      selectedCustomerRef.current = customerId;
      dispatch({ kind: "select_customer", customerId });
      closeEvidence();
      void loadJourney(runState.runId, customerId, runVersionRef.current);
    },
    [closeEvidence, loadJourney, runState.phase, runState.runId],
  );

  const retryJourney = useCallback(() => {
    if (
      !runState.runId || !selectedCustomerRef.current ||
      (runState.phase !== "completed" && runState.phase !== "degraded")
    ) return;
    void loadJourney(
      runState.runId, selectedCustomerRef.current, runVersionRef.current,
    );
  }, [loadJourney, runState.phase, runState.runId]);

  const openEvidence = useCallback(
    (nextEvidenceId: string, opener: HTMLElement) => {
      if (!runState.runId) return;
      evidenceIdRef.current = nextEvidenceId;
      setEvidenceId(nextEvidenceId);
      setEvidenceOpener(opener);
      void loadEvidence(runState.runId, nextEvidenceId, runVersionRef.current);
    },
    [loadEvidence, runState.runId],
  );

  const retryEvidence = useCallback(() => {
    if (!runState.runId || !evidenceIdRef.current) return;
    void loadEvidence(
      runState.runId, evidenceIdRef.current, runVersionRef.current,
    );
  }, [loadEvidence, runState.runId]);

  useEffect(() => {
    const controller = new AbortController();
    sourcesAbortRef.current = controller;
    void (async () => {
      try {
        const response = await client.listSources(controller.signal);
        if (!mountedRef.current || controller.signal.aborted) return;
        const nextOptions = mergeSourceOptions(KNOWN_SOURCE_OPTIONS, response);
        setSourceOptions(nextOptions);
        setEnabledSources((current) =>
          orderedSources(
            [...current, ...response.items.map((item) => item.source_id)],
            nextOptions,
          ),
        );
      } catch (error) {
        if (!isAbort(error)) {
          // The built-in catalog is the offline fallback.
        }
      }
    })();
    void refreshHistory();
    return () => controller.abort();
  }, [client, refreshHistory]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      runVersionRef.current += 1;
      journeyVersionRef.current += 1;
      evidenceVersionRef.current += 1;
      historyVersionRef.current += 1;
      documentVersionRef.current += 1;
      runAbortRef.current?.abort();
      journeyAbortRef.current?.abort();
      evidenceAbortRef.current?.abort();
      sourcesAbortRef.current?.abort();
      historyAbortRef.current?.abort();
      documentAbortRef.current?.abort();
    };
  }, []);

  const downloadUrls = runState.runId
    ? {
        json: client.jsonDownloadUrl(runState.runId),
        markdown: client.markdownDownloadUrl(runState.runId),
      }
    : null;

  return {
    runState,
    question,
    setQuestion,
    startDate,
    setStartDate,
    endDate,
    setEndDate,
    sourceOptions,
    enabledSources,
    toggleSource,
    isCreating,
    submissionError,
    submissionErrorKind,
    submissionErrorCode,
    clarificationError,
    submitClarification,
    historyItems,
    historyStatus,
    selectedRunId,
    selectHistory,
    document,
    documentStatus,
    downloadUrls,
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

export type ReturnTypeUseRunController = ReturnType<typeof useRunController>;
