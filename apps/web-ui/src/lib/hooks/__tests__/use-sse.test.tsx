import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCompilationEvents } from "../use-sse";

class MockEventSource {
  static instances: MockEventSource[] = [];

  onopen: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  readonly listeners = new Map<string, Set<EventListener>>();
  closed = false;

  constructor(public readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: EventListener) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, payload: unknown) {
    const event = {
      data: JSON.stringify(payload),
    } as MessageEvent<string>;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

describe("useCompilationEvents", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
    localStorage.clear();
  });

  it("subscribes to named SSE events and normalizes backend payloads", async () => {
    localStorage.setItem("auth_token", "secret-token");
    const { result } = renderHook(() => useCompilationEvents("job-1"));

    const source = MockEventSource.instances[0];
    expect(source?.url).toContain("/api/v1/compilations/job-1/events");
    expect(source?.url).toContain("token=secret-token");

    act(() => {
      source?.onopen?.(new Event("open"));
      source?.emit("stage.started", {
        event_type: "stage.started",
        stage: "extract",
        detail: { detected_protocol: "openapi" },
        created_at: "2026-03-29T00:00:00Z",
        attempt: 1,
      });
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
  });

  it("resets accumulated events when the job id changes", async () => {
    const { result, rerender } = renderHook(
      ({ jobId }) => useCompilationEvents(jobId),
      {
        initialProps: { jobId: "job-1" as string | null },
      },
    );

    const firstSource = MockEventSource.instances[0];

    act(() => {
      firstSource?.emit("job.started", {
        event_type: "job.started",
        created_at: "2026-03-29T00:00:00Z",
      });
    });

    await waitFor(() => expect(result.current.events).toHaveLength(1));

    rerender({ jobId: "job-2" });

    await waitFor(() => {
      expect(result.current.events).toEqual([]);
      expect(result.current.isConnected).toBe(false);
      expect(result.current.error).toBeNull();
    });

    expect(firstSource?.closed).toBe(true);
    expect(MockEventSource.instances[1]?.url).toContain(
      "/api/v1/compilations/job-2/events",
    );
  });

  it("closes the EventSource when disconnected", async () => {
    const { rerender } = renderHook(({ jobId }) => useCompilationEvents(jobId), {
      initialProps: { jobId: "job-1" as string | null },
    });

    const source = MockEventSource.instances[0];
    expect(source?.closed).toBe(false);

    rerender({ jobId: null });

    await waitFor(() => expect(source?.closed).toBe(true));
  });
});
