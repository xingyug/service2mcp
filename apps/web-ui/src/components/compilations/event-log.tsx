"use client";

import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { CompilationEvent, CompilationEventType, CompilationStage } from "@/types/api";

const eventTypeColors: Record<CompilationEventType, string> = {
  stage_started: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  stage_completed:
    "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  stage_failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
  job_started: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
  job_completed:
    "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  job_failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

function formatEventType(type: CompilationEventType): string {
  return type.replace(/_/g, " ");
}

function formatTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString();
  } catch {
    return ts;
  }
}

export function EventLog({
  events,
  isConnected,
  error,
  filterStage,
}: {
  events: CompilationEvent[];
  isConnected: boolean;
  error: Error | null;
  filterStage?: CompilationStage;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const filtered = filterStage
    ? events.filter((e) => e.stage === filterStage)
    : events;

  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [filtered.length]);

  return (
    <div className="flex flex-col gap-2">
      {/* Connection status */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span
          className={cn(
            "inline-block size-2 rounded-full",
            isConnected ? "bg-green-500" : "bg-muted-foreground/40",
          )}
        />
        {isConnected ? "Connected" : "Disconnected"}
        {error && (
          <span className="text-red-500">— {error.message}</span>
        )}
      </div>

      {/* Event stream */}
      <div
        ref={scrollRef}
        className="h-[320px] overflow-y-auto rounded-lg border bg-muted/30 p-2 font-mono text-xs"
      >
        {filtered.length === 0 ? (
          <p className="py-8 text-center text-muted-foreground">
            {isConnected ? "Waiting for events…" : "No events yet"}
          </p>
        ) : (
          <div className="space-y-1">
            {filtered.map((evt, i) => (
              <div key={i} className="flex items-start gap-2">
                <span className="shrink-0 text-muted-foreground">
                  {formatTimestamp(evt.timestamp)}
                </span>
                <span
                  className={cn(
                    "inline-flex shrink-0 items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
                    eventTypeColors[evt.type],
                  )}
                >
                  {formatEventType(evt.type)}
                </span>
                {evt.stage && (
                  <span className="shrink-0 text-foreground/70">
                    [{evt.stage}]
                  </span>
                )}
                {evt.detail && (
                  <span className="text-foreground">{evt.detail}</span>
                )}
                {evt.attempt != null && evt.attempt > 1 && (
                  <span className="text-muted-foreground">
                    (attempt {evt.attempt})
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
