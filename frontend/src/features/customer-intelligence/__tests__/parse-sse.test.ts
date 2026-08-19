import { describe, expect, it } from "vitest";

import {
  SseParseError,
  createSseParser,
} from "../parse-sse";

describe("createSseParser", () => {
  it("joins JSON split across network chunks", () => {
    const parser = createSseParser();

    expect(
      parser.push('id: 1\nevent: tool_completed\ndata: {"tool":"match_'),
    ).toEqual([]);
    expect(parser.push('journey_pattern","count":6}\n\n')).toEqual([
      {
        id: 1,
        type: "tool_completed",
        data: { tool: "match_journey_pattern", count: 6 },
      },
    ]);
  });

  it("handles CRLF split at a chunk boundary and joins multiline data", () => {
    const parser = createSseParser();

    expect(
      parser.push(": heartbeat\r\nid: 2\r\nevent: plan\r\ndata: {\r"),
    ).toEqual([]);
    expect(parser.push('\n data-ignored: true\r\ndata: "steps": ["one"]\r\ndata: }\r\n\r\n')).toEqual([
      {
        id: 2,
        type: "plan",
        data: { steps: ["one"] },
      },
    ]);
  });

  it("ignores comment-only frames and flushes a terminal frame without a blank line", () => {
    const parser = createSseParser();

    expect(parser.push(": keep-alive\n\n")).toEqual([]);
    expect(
      parser.push('id: 3\nevent: done\ndata: {"status":"completed"}'),
    ).toEqual([]);
    expect(parser.finish()).toEqual([
      {
        id: 3,
        type: "done",
        data: { status: "completed" },
      },
    ]);
    expect(parser.finish()).toEqual([]);
  });

  it.each([
    {
      input: 'id: nope\nevent: done\ndata: {"status":"completed"}\n\n',
      code: "invalid_id",
    },
    {
      input:
        'id: 999999999999999999999\nevent: done\ndata: {"status":"completed"}\n\n',
      code: "invalid_id",
    },
    {
      input: "id: 1\nevent: done\ndata: not-json\n\n",
      code: "invalid_data",
    },
    {
      input: "id: 1\ndata: {}\n\n",
      code: "missing_event",
    },
    {
      input: "id: 1\nevent: done\n\n",
      code: "missing_data",
    },
  ] as const)("reports malformed frames with code $code", ({ input, code }) => {
    const parser = createSseParser();

    try {
      parser.push(input);
      throw new Error("expected the parser to reject the malformed frame");
    } catch (error) {
      expect(error).toBeInstanceOf(SseParseError);
      expect((error as SseParseError).code).toBe(code);
    }
  });
});
