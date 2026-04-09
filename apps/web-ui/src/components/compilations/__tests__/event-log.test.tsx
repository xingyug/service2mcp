import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { EventLog } from "../event-log";
import type { CompilationEvent } from "@/types/api";

function makeEvent(overrides: Partial<CompilationEvent> = {}): CompilationEvent {
  return {
    type: "stage_started",
    stage: "extract",
    detail: "Starting extraction",
    timestamp: "2024-01-15T10:30:00Z",
    ...overrides,
  };
}

describe("EventLog", () => {
  it("renders event list with timestamps and messages", () => {
    const events: CompilationEvent[] = [
      makeEvent({ detail: "Starting extraction" }),
      makeEvent({
        type: "stage_completed",
        detail: "Extraction complete",
        timestamp: "2024-01-15T10:31:00Z",
      }),
    ];

    render(<EventLog events={events} isConnected={true} error={null} />);
    expect(screen.getByText("Starting extraction")).toBeInTheDocument();
    expect(screen.getByText("Extraction complete")).toBeInTheDocument();
  });

  it("shows 'Connected' indicator when isConnected=true", () => {
    render(<EventLog events={[]} isConnected={true} error={null} />);
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });

  it("shows 'Disconnected' when isConnected=false", () => {
    render(<EventLog events={[]} isConnected={false} error={null} />);
    expect(screen.getByText("Disconnected")).toBeInTheDocument();
  });

  it("filters events by stage when filterStage is provided", () => {
    const events: CompilationEvent[] = [
      makeEvent({ stage: "extract", detail: "Extract event" }),
      makeEvent({ stage: "deploy", detail: "Deploy event" }),
      makeEvent({ stage: "extract", detail: "Another extract event" }),
    ];

    render(
      <EventLog
        events={events}
        isConnected={true}
        error={null}
        filterStage="extract"
      />,
    );

    expect(screen.getByText("Extract event")).toBeInTheDocument();
    expect(screen.getByText("Another extract event")).toBeInTheDocument();
    expect(screen.queryByText("Deploy event")).not.toBeInTheDocument();
  });

  it("shows error message when error prop is set", () => {
    const error = new Error("Connection lost");
    render(<EventLog events={[]} isConnected={false} error={error} />);
    expect(screen.getByText(/Connection lost/)).toBeInTheDocument();
  });

  it("shows empty state 'Waiting for events…' when connected with no events", () => {
    render(<EventLog events={[]} isConnected={true} error={null} />);
    expect(screen.getByText("Waiting for events…")).toBeInTheDocument();
  });

  it("shows empty state 'No events yet' when disconnected with no events", () => {
    render(<EventLog events={[]} isConnected={false} error={null} />);
    expect(screen.getByText("No events yet")).toBeInTheDocument();
  });

  it("displays formatted event type (underscores replaced with spaces)", () => {
    const events: CompilationEvent[] = [
      makeEvent({ type: "stage_started" }),
    ];
    render(<EventLog events={events} isConnected={true} error={null} />);
    expect(screen.getByText("stage started")).toBeInTheDocument();
  });

  it("shows stage tag in brackets when event has a stage", () => {
    const events: CompilationEvent[] = [
      makeEvent({ stage: "extract" }),
    ];
    render(<EventLog events={events} isConnected={true} error={null} />);
    expect(screen.getByText("[extract]")).toBeInTheDocument();
  });

  it("shows attempt number for retried events (attempt > 1)", () => {
    const events: CompilationEvent[] = [
      makeEvent({ attempt: 3, detail: "Retry event" }),
    ];
    render(<EventLog events={events} isConnected={true} error={null} />);
    expect(screen.getByText("(attempt 3)")).toBeInTheDocument();
  });

  it("does not show attempt tag for first attempt (attempt=1)", () => {
    const events: CompilationEvent[] = [
      makeEvent({ attempt: 1, detail: "Some event" }),
    ];
    render(<EventLog events={events} isConnected={true} error={null} />);
    // "(attempt N)" text should not appear for attempt === 1
    expect(screen.queryByText(/\(attempt/)).not.toBeInTheDocument();
  });

  it("renders the scroll container with a ref for auto-scroll", () => {
    const events: CompilationEvent[] = [makeEvent()];
    const { container } = render(
      <EventLog events={events} isConnected={true} error={null} />,
    );
    const scrollContainer = container.querySelector(".overflow-y-auto");
    expect(scrollContainer).toBeInTheDocument();
  });

  it("applies correct color classes to different event types", () => {
    const events: CompilationEvent[] = [
      makeEvent({ type: "stage_failed", detail: "Error occurred" }),
    ];
    render(<EventLog events={events} isConnected={true} error={null} />);
    const badge = screen.getByText("stage failed");
    expect(badge.className).toMatch(/bg-red/);
  });

  it("connection indicator has green dot when connected", () => {
    const { container } = render(
      <EventLog events={[]} isConnected={true} error={null} />,
    );
    const dot = container.querySelector(".bg-green-500");
    expect(dot).toBeInTheDocument();
  });
});
