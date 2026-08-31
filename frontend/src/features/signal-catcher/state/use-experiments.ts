"use client";

import { useCallback, useEffect, useState } from "react";

import type { Experiment } from "./types";

const STORAGE_KEY = "catchers.experiments.v1";

/**
 * 적용한 실험 목록.
 *
 * 실험은 Run 하나보다 오래 살기 때문에 `CatchSession` 밖에 둔다.
 * 첫 화면으로 나갔다 와도, 새로고침해도 이어서 볼 수 있어야 하므로
 * localStorage 에 보관한다. 읽기 실패는 조용히 빈 목록으로 넘긴다.
 */
function read(): Experiment[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as Experiment[]) : [];
  } catch {
    return [];
  }
}

function write(items: Experiment[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // 저장에 실패해도 화면 동작은 막지 않는다.
  }
}

export interface ExperimentStore {
  experiments: Experiment[];
  watching: Experiment[];
  find: (actionId: string) => Experiment | undefined;
  /**
   * 같은 Segment 를 이미 관찰 중인 실험.
   * 동시에 적용하면 어느 쪽 효과인지 귀인할 수 없으므로 적용 전에 알린다.
   * Segment 가 다르면 겹치지 않으므로 동시 진행을 막지 않는다.
   */
  conflictOf: (segmentLabel: string, actionId: string) => Experiment | undefined;
  begin: (item: Omit<Experiment, "startedAt" | "status" | "elapsedDays" | "hits" | "total">) => void;
  advance: (actionId: string, days: number) => void;
  complete: (actionId: string, hits: number, total: number) => void;
  clear: () => void;
}

export function useExperiments(): ExperimentStore {
  const [experiments, setExperiments] = useState<Experiment[]>([]);

  // SSR 과 첫 렌더를 맞추기 위해 마운트 뒤에 읽는다.
  useEffect(() => {
    setExperiments(read());
  }, []);

  const save = useCallback((next: Experiment[]) => {
    setExperiments(next);
    write(next);
  }, []);

  const find = useCallback(
    (actionId: string) => experiments.find((item) => item.actionId === actionId),
    [experiments],
  );

  const conflictOf = useCallback(
    (segmentLabel: string, actionId: string) =>
      experiments.find(
        (item) =>
          item.status === "watching" &&
          item.actionId !== actionId &&
          item.segmentLabel === segmentLabel,
      ),
    [experiments],
  );

  const begin = useCallback<ExperimentStore["begin"]>(
    (item) => {
      const next: Experiment = {
        ...item,
        startedAt: new Date().toISOString(),
        status: "watching",
        elapsedDays: 0,
        hits: 0,
        total: 0,
      };
      save([...experiments.filter((x) => x.actionId !== item.actionId), next]);
    },
    [experiments, save],
  );

  const advance = useCallback(
    (actionId: string, days: number) => {
      save(
        experiments.map((item) =>
          item.actionId === actionId ? { ...item, elapsedDays: days } : item,
        ),
      );
    },
    [experiments, save],
  );

  const complete = useCallback(
    (actionId: string, hits: number, total: number) => {
      save(
        experiments.map((item) =>
          item.actionId === actionId
            ? { ...item, status: "done", elapsedDays: item.observeDays, hits, total }
            : item,
        ),
      );
    },
    [experiments, save],
  );

  const clear = useCallback(() => save([]), [save]);

  return {
    experiments,
    watching: experiments.filter((item) => item.status === "watching"),
    find,
    conflictOf,
    begin,
    advance,
    complete,
    clear,
  };
}
