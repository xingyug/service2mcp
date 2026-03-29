import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { StageTimeline } from "../stage-timeline";

const ALL_STAGE_LABELS = [
  "Detect",
  "Extract",
  "Enhance",
  "Validate IR",
  "Generate",
  "Deploy",
  "Validate Runtime",
  "Route",
  "Register",
];

describe("StageTimeline", () => {
  it("renders all 9 real pipeline stages", () => {
    render(<StageTimeline status="pending" />);
    for (const label of ALL_STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("highlights current stage with active (blue) styling", () => {
    render(<StageTimeline status="running" currentStage="extract" />);
    const extractLabel = screen.getByText("Extract");
    expect(extractLabel.className).toMatch(/text-blue/);
  });

  it("shows completed styling for stages before current", () => {
    render(<StageTimeline status="running" currentStage="deploy" />);
    const detectLabel = screen.getByText("Detect");
    expect(detectLabel.className).toMatch(/text-green/);
    const extractLabel = screen.getByText("Extract");
    expect(extractLabel.className).toMatch(/text-green/);
  });

  it("shows pending styling for stages after current", () => {
    render(<StageTimeline status="running" currentStage="extract" />);
    const deployLabel = screen.getByText("Deploy");
    expect(deployLabel.className).toMatch(/text-muted/);
    const registerLabel = screen.getByText("Register");
    expect(registerLabel.className).toMatch(/text-muted/);
  });

  it("shows failed styling for the failedStage when status is failed", () => {
    render(
      <StageTimeline status="failed" failedStage="enhance" />,
    );
    const enhanceLabel = screen.getByText("Enhance");
    expect(enhanceLabel.className).toMatch(/text-red/);
  });

  it("calls onSelectStage when a stage is clicked", async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();

    render(
      <StageTimeline status="running" currentStage="deploy" onSelectStage={handleSelect} />,
    );

    await user.click(screen.getByText("Extract"));
    expect(handleSelect).toHaveBeenCalledWith("extract");
  });

  it("calls onSelectStage with correct key for each stage", async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();

    render(
      <StageTimeline status="pending" onSelectStage={handleSelect} />,
    );

    await user.click(screen.getByText("Validate IR"));
    expect(handleSelect).toHaveBeenCalledWith("validate_ir");
  });

  it("shows all stages as completed when status is succeeded", () => {
    render(<StageTimeline status="succeeded" />);
    for (const label of ALL_STAGE_LABELS) {
      const el = screen.getByText(label);
      expect(el.className).toMatch(/text-green/);
    }
  });

  it("shows all stages as pending when status is pending", () => {
    render(<StageTimeline status="pending" />);
    for (const label of ALL_STAGE_LABELS) {
      const el = screen.getByText(label);
      expect(el.className).toMatch(/text-muted/);
    }
  });

  it("applies selected ring style when selectedStage matches", () => {
    const { container } = render(
      <StageTimeline status="running" currentStage="deploy" selectedStage="deploy" />,
    );
    const buttons = container.querySelectorAll("button");
    const deployButton = Array.from(buttons).find((b) =>
      b.textContent?.includes("Deploy"),
    );
    expect(deployButton?.className).toMatch(/ring/);
  });

  it("renders connector lines between stages", () => {
    const { container } = render(<StageTimeline status="pending" />);
    const connectors = container.querySelectorAll(".h-0\\.5.w-4");
    expect(connectors.length).toBe(8);
  });

  it("shows failed styling for failedStage when status is rolled_back", () => {
    render(
      <StageTimeline status="rolled_back" failedStage="deploy" />,
    );
    const deployLabel = screen.getByText("Deploy");
    expect(deployLabel.className).toMatch(/text-red/);
  });

  it("uses currentStage prop as fallback when status has no mapping", () => {
    render(
      <StageTimeline status="pending" currentStage="enhance" />,
    );
    // With currentStage=enhance, detect and extract should be completed
    const detectLabel = screen.getByText("Detect");
    expect(detectLabel.className).toMatch(/text-green/);
  });
});
