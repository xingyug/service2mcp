"use client";

import { useEffect, useRef, useState } from "react";
import type {
  CompilationEvent,
  CompilationEventType,
  CompilationStage,
} from "@/types/api";

export interface UseCompilationEventsReturn {
  events: CompilationEvent[];
  isConnected: boolean;
  error: Error | null;
}

type RawCompilationEvent = {
  event_type?: string;
  stage?: string | null;
  detail?: Record<string, unknown> | null;
  error_detail?: string | null;
  created_at?: string;
  attempt?: number | null;
};

function normalizeEventType(rawEventType: string): CompilationEventType | null {
  switch (rawEventType) {
    case "job.started":
      return "job_started";
    case "job.succeeded":
      return "job_completed";
    case "job.failed":
      return "job_failed";
    case "job.rolled_back":
      return "job_rolled_back";
    case "stage.started":
      return "stage_started";
    case "stage.succeeded":
      return "stage_completed";
    case "stage.retrying":
      return "stage_retrying";
    case "stage.failed":
      return "stage_failed";
    case "rollback.started":
      return "rollback_started";
    case "rollback.succeeded":
      return "rollback_succeeded";
    case "rollback.failed":
      return "rollback_failed";
    default:
      return null;
  }
}

function normalizeStage(rawStage?: string | null): CompilationStage | undefined {
  return rawStage ? (rawStage as CompilationStage) : undefined;
}

function formatDetail(raw: RawCompilationEvent): string | undefined {
  if (raw.error_detail) return raw.error_detail;
  if (!raw.detail) return undefined;
  return JSON.stringify(raw.detail);
}

function normalizeEvent(
  raw: RawCompilationEvent,
  fallbackEventType: string,
): CompilationEvent | null {
  const eventType = normalizeEventType(raw.event_type ?? fallbackEventType);
  if (!eventType || !raw.created_at) return null;
  return {
    type: eventType,
    stage: normalizeStage(raw.stage),
    detail: formatDetail(raw),
    timestamp: raw.created_at,
    attempt: raw.attempt ?? undefined,
  };
}

/**
 * Subscribes to the SSE stream for a compilation job.
 * Pass `null` to disconnect.
 */
export function useCompilationEvents(
  jobId: string | null,
): UseCompilationEventsReturn {
  const [events, setEvents] = useState<CompilationEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!jobId) {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      return;
    }

    const baseUrl =
      process.env.NEXT_PUBLIC_COMPILER_API_URL || "";

    const url = `${baseUrl}/api/v1/compilations/${jobId}/events`;
    const token =
      typeof window !== "undefined"
        ? localStorage.getItem("auth_token")
        : null;

    const controller = new AbortController();
    abortRef.current = controller;

    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    (async () => {
      try {
        const response = await fetch(url, {
          headers,
          signal: controller.signal,
        });
        if (!response.ok || !response.body) {
          setError(new Error(`SSE connection failed: ${response.status}`));
          return;
        }
        setIsConnected(true);
        setError(null);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let currentEventType = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";

          for (const line of lines) {
            if (line.startsWith("event: ")) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
              const data = line.slice(6);
              const eventType = currentEventType || "message";
              try {
                const raw = JSON.parse(data) as RawCompilationEvent;
                const normalized = normalizeEvent(raw, eventType);
                if (normalized) {
                  setEvents((prev) => [...prev, normalized]);
                }
              } catch {
                // Ignore unparseable messages
              }
              currentEventType = "";
            } else if (line.trim() === "") {
              currentEventType = "";
            }
          }
        }
        // Flush any remaining bytes in the TextDecoder internal buffer
        // and process any final buffered line (e.g. stream ended without trailing \n)
        buffer += decoder.decode(new Uint8Array(), { stream: false });
        if (buffer.trim()) {
          const line = buffer;
          if (line.startsWith("event: ")) {
            currentEventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const data = line.slice(6);
            const eventType = currentEventType || "message";
            try {
              const raw = JSON.parse(data) as RawCompilationEvent;
              const normalized = normalizeEvent(raw, eventType);
              if (normalized) {
                setEvents((prev) => [...prev, normalized]);
              }
            } catch {
              // Ignore unparseable messages
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err : new Error("SSE connection error"),
        );
      } finally {
        setIsConnected(false);
      }
    })();

    return () => {
      controller.abort();
      abortRef.current = null;
      setEvents([]);
      setError(null);
      setIsConnected(false);
    };
  }, [jobId]);

  return { events, isConnected, error };
}
