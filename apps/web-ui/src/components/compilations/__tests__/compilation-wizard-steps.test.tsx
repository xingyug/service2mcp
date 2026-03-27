import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { WizardStepIndicator } from "../compilation-wizard-steps";

const STEPS = ["Source", "Options", "Auth", "Review"];

describe("WizardStepIndicator", () => {
  it("renders correct number of step indicators", () => {
    render(<WizardStepIndicator steps={STEPS} currentStep={0} />);
    for (const label of STEPS) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("highlights the current step with primary styling", () => {
    const { container } = render(
      <WizardStepIndicator steps={STEPS} currentStep={1} />,
    );
    // Step indicator circles (the number elements)
    const circles = container.querySelectorAll(
      ".flex.h-8.w-8.items-center.justify-center.rounded-full",
    );
    // currentStep=1 → second circle should have bg-primary
    expect(circles[1]?.className).toMatch(/bg-primary\b/);
    expect(circles[1]?.className).toMatch(/text-primary-foreground/);
  });

  it("shows completed steps before current with check icon and primary/10 bg", () => {
    const { container } = render(
      <WizardStepIndicator steps={STEPS} currentStep={2} />,
    );
    const circles = container.querySelectorAll(
      ".flex.h-8.w-8.items-center.justify-center.rounded-full",
    );
    // Steps 0 and 1 are completed — should have bg-primary/10
    expect(circles[0]?.className).toMatch(/bg-primary\/10/);
    expect(circles[1]?.className).toMatch(/bg-primary\/10/);
    // Completed steps render a Check icon (svg) instead of a number
    expect(circles[0]?.querySelector("svg")).toBeInTheDocument();
    expect(circles[1]?.querySelector("svg")).toBeInTheDocument();
  });

  it("shows step number for current and pending steps", () => {
    render(<WizardStepIndicator steps={STEPS} currentStep={1} />);
    // Current step (index 1) shows "2"
    expect(screen.getByText("2")).toBeInTheDocument();
    // Pending steps show "3" and "4"
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("shows pending steps after current with muted styling", () => {
    const { container } = render(
      <WizardStepIndicator steps={STEPS} currentStep={1} />,
    );
    const circles = container.querySelectorAll(
      ".flex.h-8.w-8.items-center.justify-center.rounded-full",
    );
    // Steps 2 and 3 are pending
    expect(circles[2]?.className).toMatch(/bg-muted/);
    expect(circles[3]?.className).toMatch(/bg-muted/);
  });

  it("calls onStepClick when a completed step is clicked", async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();

    render(
      <WizardStepIndicator
        steps={STEPS}
        currentStep={2}
        onStepClick={handleClick}
      />,
    );

    // Step 0 ("Source") is completed and clickable
    await user.click(screen.getByText("Source"));
    expect(handleClick).toHaveBeenCalledWith(0);
  });

  it("calls onStepClick when the current step is clicked", async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();

    render(
      <WizardStepIndicator
        steps={STEPS}
        currentStep={1}
        onStepClick={handleClick}
      />,
    );

    await user.click(screen.getByText("Options"));
    expect(handleClick).toHaveBeenCalledWith(1);
  });

  it("does not call onStepClick for future steps", async () => {
    const user = userEvent.setup();
    const handleClick = vi.fn();

    render(
      <WizardStepIndicator
        steps={STEPS}
        currentStep={1}
        onStepClick={handleClick}
      />,
    );

    // Step 2 ("Auth") is a future step — not clickable
    await user.click(screen.getByText("Auth"));
    expect(handleClick).not.toHaveBeenCalled();
  });

  it("renders connector lines between steps", () => {
    const { container } = render(
      <WizardStepIndicator steps={STEPS} currentStep={0} />,
    );
    // 4 steps → 3 connector lines with h-px class
    const connectors = container.querySelectorAll(".h-px.flex-1");
    expect(connectors.length).toBe(3);
  });

  it("connector lines before current step differ from those after", () => {
    const { container } = render(
      <WizardStepIndicator steps={STEPS} currentStep={2} />,
    );
    const connectors = container.querySelectorAll(".h-px.flex-1");
    // Connectors before completed steps get bg-primary; others bg-border
    // Step indices: 0(completed), 1(completed), 2(current), 3(pending)
    // Connector 0 is between step 0→1 (completed→completed): bg-primary
    // Connector 1 is between step 1→2 (completed→current): bg-border (step 1 < currentStep but connector follows step logic)
    // Just verify third connector is bg-border (after current)
    expect(connectors[2]?.className).toMatch(/bg-border/);
    // And first connector is bg-primary (between two completed steps)
    expect(connectors[0]?.className).toMatch(/bg-primary/);
  });

  it("has nav element with aria-label 'Progress'", () => {
    render(<WizardStepIndicator steps={STEPS} currentStep={0} />);
    expect(screen.getByRole("navigation", { name: "Progress" })).toBeInTheDocument();
  });

  it("renders with a single step correctly", () => {
    render(<WizardStepIndicator steps={["Only Step"]} currentStep={0} />);
    expect(screen.getByText("Only Step")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("all steps are completed when currentStep equals steps length", () => {
    const { container } = render(
      <WizardStepIndicator steps={STEPS} currentStep={4} />,
    );
    const circles = container.querySelectorAll(
      ".flex.h-8.w-8.items-center.justify-center.rounded-full",
    );
    // All 4 steps should show check icons
    for (const circle of circles) {
      expect(circle.querySelector("svg")).toBeInTheDocument();
    }
  });
});
