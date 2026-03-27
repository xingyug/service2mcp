"use client";

import { useEffect, useRef, useState, useCallback } from "react";
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

  const cleanup = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    setIsConnected(false);
  }, []);

  useEffect(() => {
    if (!jobId) {
      cleanup();
      return;
    }

    // Reset state for a new connection
    setEvents([]);
    setError(null);

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
      cleanup();
    };
  }, [jobId, cleanup]);

  return { events, isConnected, error };
}
