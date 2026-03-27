"use client";

import { useEffect, useRef, useState } from "react";
import type { CompilationEvent } from "@/types/api";

export interface UseCompilationEventsReturn {
  events: CompilationEvent[];
  isConnected: boolean;
  error: Error | null;
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

  // Reset state when jobId changes (recommended React pattern for derived state)
  const [prevJobId, setPrevJobId] = useState(jobId);
  if (prevJobId !== jobId) {
    setPrevJobId(jobId);
    setEvents([]);
    setError(null);
    setIsConnected(false);
  }

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

    let url = `${baseUrl}/api/v1/compilations/${jobId}/events`;

    // Attach auth token as a query param (EventSource doesn't support headers)
    if (typeof window !== "undefined") {
      const token = localStorage.getItem("auth_token");
      if (token) {
        url += `?token=${encodeURIComponent(token)}`;
      }
    }

    const es = new EventSource(url);
    sourceRef.current = es;

    es.onopen = () => {
      setIsConnected(true);
      setError(null);
    };

    es.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data) as CompilationEvent;
        setEvents((prev) => [...prev, event]);
      } catch {
        // Ignore unparseable messages
      }
    };

    es.onerror = () => {
      // EventSource automatically reconnects on transient errors.
      // Surface a generic error so consumers can display a warning.
      setError(new Error("SSE connection error"));
      setIsConnected(false);
    };

    return () => {
      if (sourceRef.current) {
        sourceRef.current.close();
        sourceRef.current = null;
      }
    };
  }, [jobId]);

  return { events, isConnected, error };
}
