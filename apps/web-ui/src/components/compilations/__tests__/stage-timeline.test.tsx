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
  "Build",
  "Deploy",
  "Validate Runtime",
  "Route",
  "Register",
];

describe("StageTimeline", () => {
  it("renders all 10 pipeline stages", () => {
    render(<StageTimeline status="PENDING" />);
    for (const label of ALL_STAGE_LABELS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("highlights current stage with active (blue) styling", () => {
    render(<StageTimeline status="EXTRACTING" />);
    const extractLabel = screen.getByText("Extract");
    expect(extractLabel.className).toMatch(/text-blue/);
  });

  it("shows completed styling for stages before current", () => {
    render(<StageTimeline status="BUILDING" />);
    // Detect, Extract, Enhance, Validate IR, Generate are before Build
    const detectLabel = screen.getByText("Detect");
    expect(detectLabel.className).toMatch(/text-green/);
    const extractLabel = screen.getByText("Extract");
    expect(extractLabel.className).toMatch(/text-green/);
  });

  it("shows pending styling for stages after current", () => {
    render(<StageTimeline status="EXTRACTING" />);
    // Build, Deploy, etc. are after Extract
    const buildLabel = screen.getByText("Build");
    expect(buildLabel.className).toMatch(/text-muted/);
    const registerLabel = screen.getByText("Register");
    expect(registerLabel.className).toMatch(/text-muted/);
  });

  it("shows failed styling for the failedStage when status is FAILED", () => {
    render(
      <StageTimeline status="FAILED" failedStage="enhance" />,
    );
    const enhanceLabel = screen.getByText("Enhance");
    expect(enhanceLabel.className).toMatch(/text-red/);
  });

  it("calls onSelectStage when a stage is clicked", async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();

    render(
      <StageTimeline status="BUILDING" onSelectStage={handleSelect} />,
    );

    await user.click(screen.getByText("Extract"));
    expect(handleSelect).toHaveBeenCalledWith("extract");
  });

  it("calls onSelectStage with correct key for each stage", async () => {
    const user = userEvent.setup();
    const handleSelect = vi.fn();

    render(
      <StageTimeline status="PENDING" onSelectStage={handleSelect} />,
    );

    await user.click(screen.getByText("Validate IR"));
    expect(handleSelect).toHaveBeenCalledWith("validate_ir");
  });

  it("shows all stages as completed when status is PUBLISHED", () => {
    render(<StageTimeline status="PUBLISHED" />);
    for (const label of ALL_STAGE_LABELS) {
      const el = screen.getByText(label);
      expect(el.className).toMatch(/text-green/);
    }
  });

  it("shows all stages as pending when status is PENDING", () => {
    render(<StageTimeline status="PENDING" />);
    for (const label of ALL_STAGE_LABELS) {
      const el = screen.getByText(label);
      expect(el.className).toMatch(/text-muted/);
    }
  });

  it("applies selected ring style when selectedStage matches", () => {
    const { container } = render(
      <StageTimeline status="BUILDING" selectedStage="build" />,
    );
    const buttons = container.querySelectorAll("button");
    const buildButton = Array.from(buttons).find((b) =>
      b.textContent?.includes("Build"),
    );
    expect(buildButton?.className).toMatch(/ring/);
  });

  it("renders connector lines between stages", () => {
    const { container } = render(<StageTimeline status="PENDING" />);
    // 10 stages → 9 connector lines
    const connectors = container.querySelectorAll(".h-0\\.5.w-4");
    expect(connectors.length).toBe(9);
  });

  it("shows failed styling for failedStage when status is ROLLING_BACK", () => {
    render(
      <StageTimeline status="ROLLING_BACK" failedStage="deploy" />,
    );
    const deployLabel = screen.getByText("Deploy");
    expect(deployLabel.className).toMatch(/text-red/);
  });

  it("uses currentStage prop as fallback when status has no mapping", () => {
    render(
      <StageTimeline status="PENDING" currentStage="enhance" />,
    );
    // With currentStage=enhance, detect and extract should be completed
    const detectLabel = screen.getByText("Detect");
    expect(detectLabel.className).toMatch(/text-green/);
  });
});
