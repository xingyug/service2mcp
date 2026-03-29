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

const SSE_EVENT_TYPES = [
  "job.started",
  "job.succeeded",
  "job.failed",
  "job.rolled_back",
  "stage.started",
  "stage.succeeded",
  "stage.retrying",
  "stage.failed",
  "rollback.started",
  "rollback.succeeded",
  "rollback.failed",
] as const;

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
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!jobId) {
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
      return;
    }

    const baseUrl =
      process.env.NEXT_PUBLIC_COMPILER_API_URL || "http://localhost:8000";

    const url = `${baseUrl}/api/v1/compilations/${jobId}/events`;
    const token =
      typeof window !== "undefined"
        ? localStorage.getItem("auth_token")
        : null;
    const authUrl = token
      ? `${url}?token=${encodeURIComponent(token)}`
      : url;
    const es = new EventSource(authUrl);
    sourceRef.current = es;

    const listeners = SSE_EVENT_TYPES.map((eventType) => {
      const listener = (msg: MessageEvent<string>) => {
        try {
          const raw = JSON.parse(msg.data) as RawCompilationEvent;
          const normalized = normalizeEvent(raw, eventType);
          if (normalized) {
            setEvents((prev) => [...prev, normalized]);
          }
        } catch {
          // Ignore unparseable messages
        }
      };
      es.addEventListener(eventType, listener as EventListener);
      return { eventType, listener };
    });

    es.onopen = () => {
      setIsConnected(true);
      setError(null);
    };

    es.onerror = () => {
      // EventSource automatically reconnects on transient errors.
      // Surface a generic error so consumers can display a warning.
      setError(new Error("SSE connection error"));
      setIsConnected(false);
    };

    return () => {
      listeners.forEach(({ eventType, listener }) => {
        es.removeEventListener(eventType, listener as EventListener);
      });
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
      setEvents([]);
      setError(null);
      setIsConnected(false);
    };
  }, [jobId]);

  return { events, isConnected, error };
}
