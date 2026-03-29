import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { useCompilationEvents } from "../use-sse";

type StreamResponse = {
  response: Response;
  emit: (eventType: string, payload: unknown) => void;
  emitRaw: (chunk: string) => void;
  close: () => void;
};

let mockFetch: Mock;

function createStreamResponse(status = 200): StreamResponse {
  const encoder = new TextEncoder();
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
  const stream = new ReadableStream<Uint8Array>({
    start(nextController) {
      controller = nextController;
    },
  });

  return {
    response: {
      ok: status >= 200 && status < 300,
      status,
      body: status >= 200 && status < 300 ? stream : null,
    } as Response,
    emit(eventType, payload) {
      controller?.enqueue(
        encoder.encode(`event: ${eventType}\ndata: ${JSON.stringify(payload)}\n\n`),
      );
    },
    emitRaw(chunk) {
      controller?.enqueue(encoder.encode(chunk));
    },
    close() {
      controller?.close();
    },
  };
}

function lastFetchCall(): [string, RequestInit | undefined] {
  return mockFetch.mock.calls[mockFetch.mock.calls.length - 1] as [
    string,
    RequestInit | undefined,
  ];
}

describe("useCompilationEvents", () => {
  beforeEach(() => {
    mockFetch = vi.fn();
    vi.stubGlobal("fetch", mockFetch);
    localStorage.clear();
  });

  it("subscribes via fetch SSE and normalizes backend payloads", async () => {
    const stream = createStreamResponse();
    mockFetch.mockResolvedValue(stream.response);
    localStorage.setItem("auth_token", "secret-token");

    const { result } = renderHook(() => useCompilationEvents("job-1"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    const [url, options] = lastFetchCall();
    const headers = new Headers(options?.headers as HeadersInit);

    expect(url).toContain("/api/v1/compilations/job-1/events");
    expect(headers.get("Authorization")).toBe("Bearer secret-token");
    expect(headers.get("Accept")).toBe("text/event-stream");

    await act(async () => {
      stream.emit("stage.started", {
        event_type: "stage.started",
        stage: "extract",
        detail: { detected_protocol: "openapi" },
        created_at: "2026-03-29T00:00:00Z",
        attempt: 1,
      });
      await Promise.resolve();
    });

    await waitFor(() =>
      expect(result.current.events).toEqual([
        {
          type: "stage_started",
          stage: "extract",
          detail: '{"detected_protocol":"openapi"}',
          timestamp: "2026-03-29T00:00:00Z",
          attempt: 1,
        },
      ]),
    );

    expect(result.current.isConnected).toBe(true);
    expect(result.current.error).toBeNull();
    await act(async () => {
      stream.close();
      await Promise.resolve();
    });
  });

  it("resets accumulated events and aborts the prior stream when the job id changes", async () => {
    const firstStream = createStreamResponse();
    const secondStream = createStreamResponse();
    mockFetch
      .mockResolvedValueOnce(firstStream.response)
      .mockResolvedValueOnce(secondStream.response);

    const { result, rerender } = renderHook(({ jobId }) => useCompilationEvents(jobId), {
      initialProps: { jobId: "job-1" as string | null },
    });

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    const [, firstOptions] = mockFetch.mock.calls[0] as [string, RequestInit];

    await act(async () => {
      firstStream.emit("job.started", {
        event_type: "job.started",
        created_at: "2026-03-29T00:00:00Z",
      });
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.events).toHaveLength(1));

    await act(async () => {
      rerender({ jobId: "job-2" });
      await Promise.resolve();
    });

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(firstOptions.signal?.aborted).toBe(true));
    await waitFor(() => expect(result.current.events).toEqual([]));

    expect((mockFetch.mock.calls[1] as [string])[0]).toContain(
      "/api/v1/compilations/job-2/events",
    );

    await act(async () => {
      secondStream.close();
      await Promise.resolve();
    });
  });

  it("aborts the stream when disconnected", async () => {
    const stream = createStreamResponse();
    mockFetch.mockResolvedValue(stream.response);

    const { rerender } = renderHook(({ jobId }) => useCompilationEvents(jobId), {
      initialProps: { jobId: "job-1" as string | null },
    });

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    const [, options] = lastFetchCall();

    expect(options?.signal?.aborted).toBe(false);

    await act(async () => {
      rerender({ jobId: null });
      await Promise.resolve();
    });

    await waitFor(() => expect(options?.signal?.aborted).toBe(true));
  });

  it("surfaces a connection error when the SSE endpoint returns a non-OK response", async () => {
    mockFetch.mockResolvedValue(createStreamResponse(401).response);

    const { result } = renderHook(() => useCompilationEvents("job-1"));

    await waitFor(() =>
      expect(result.current.error?.message).toBe("SSE connection failed: 401"),
    );
    expect(result.current.isConnected).toBe(false);
  });

  it("ignores malformed SSE payloads", async () => {
    const stream = createStreamResponse();
    mockFetch.mockResolvedValue(stream.response);

    const { result } = renderHook(() => useCompilationEvents("job-1"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    await act(async () => {
      stream.emitRaw("event: stage.started\ndata: not-json\n\n");
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.isConnected).toBe(true));
    expect(result.current.events).toEqual([]);

    await act(async () => {
      stream.close();
      await Promise.resolve();
    });
  });
});
