export type SseParseErrorCode =
  | "invalid_id"
  | "invalid_data"
  | "missing_event"
  | "missing_data";

export class SseParseError extends Error {
  readonly code: SseParseErrorCode;

  constructor(code: SseParseErrorCode, message: string, options?: { cause?: unknown }) {
    super(message);
    this.name = "SseParseError";
    this.code = code;
    if (options && "cause" in options) {
      this.cause = options.cause;
    }
  }
}

export interface ParsedSseEvent<T = unknown> {
  id: number;
  type: string;
  data: T;
}

export interface SseParser<T = unknown> {
  push(chunk: string): ParsedSseEvent<T>[];
  finish(): ParsedSseEvent<T>[];
}

function fieldValue(line: string, separator: number): string {
  const value = separator < 0 ? "" : line.slice(separator + 1);
  return value.startsWith(" ") ? value.slice(1) : value;
}

function parseFrame<T>(frame: string): ParsedSseEvent<T> | null {
  let id: string | undefined;
  let eventType: string | undefined;
  const dataLines: string[] = [];
  let sawField = false;

  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) {
      continue;
    }

    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    const value = fieldValue(line, separator);
    if (field === "id") {
      id = value.trim();
      sawField = true;
    } else if (field === "event") {
      eventType = value.trim();
      sawField = true;
    } else if (field === "data") {
      dataLines.push(value);
      sawField = true;
    }
  }

  if (!sawField) {
    return null;
  }
  if (id === undefined || !/^[1-9]\d*$/.test(id)) {
    throw new SseParseError("invalid_id", "SSE id must be a positive integer");
  }
  const numericId = Number(id);
  if (!Number.isSafeInteger(numericId)) {
    throw new SseParseError("invalid_id", "SSE id must be a safe integer");
  }
  if (!eventType) {
    throw new SseParseError("missing_event", "SSE event field is required");
  }
  if (dataLines.length === 0) {
    throw new SseParseError("missing_data", "SSE data field is required");
  }

  const serialized = dataLines.join("\n");
  try {
    return {
      id: numericId,
      type: eventType,
      data: JSON.parse(serialized) as T,
    };
  } catch (error) {
    throw new SseParseError("invalid_data", "SSE data must be valid JSON", {
      cause: error,
    });
  }
}

export function createSseParser<T = unknown>(): SseParser<T> {
  let remainder = "";

  const drain = (): ParsedSseEvent<T>[] => {
    const events: ParsedSseEvent<T>[] = [];
    while (true) {
      const delimiter = /(?:\r?\n){2}/.exec(remainder);
      if (!delimiter || delimiter.index === undefined) {
        return events;
      }

      const frame = remainder.slice(0, delimiter.index);
      remainder = remainder.slice(delimiter.index + delimiter[0].length);
      const parsed = parseFrame<T>(frame);
      if (parsed) {
        events.push(parsed);
      }
    }
  };

  return {
    push(chunk) {
      remainder += chunk;
      return drain();
    },
    finish() {
      const events = drain();
      const frame = remainder.replace(/\r?\n$/, "");
      remainder = "";
      if (!frame.trim()) {
        return events;
      }
      const parsed = parseFrame<T>(frame);
      return parsed ? [...events, parsed] : events;
    },
  };
}
