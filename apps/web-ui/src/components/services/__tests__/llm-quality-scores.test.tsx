import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LLMQualityScores } from "../llm-quality-scores";

describe("LLMQualityScores", () => {
  it("shows an unavailable state when scores are missing", () => {
    render(<LLMQualityScores />);

    expect(screen.getByText("Quality scores N/A")).toBeInTheDocument();
  });

  it("renders the full score panel with mixed score colors and labels", () => {
    const { container } = render(
      <LLMQualityScores
        scores={{
          accuracy: 0.91,
          completeness: 0.65,
          clarity: 0.4,
          overall: 0.92,
        }}
      />,
    );

    expect(screen.getByText("LLM Quality Scores")).toBeInTheDocument();
    expect(screen.getByText("Overall")).toBeInTheDocument();
    expect(screen.getByText("Accuracy")).toBeInTheDocument();
    expect(screen.getByText("Completeness")).toBeInTheDocument();
    expect(screen.getByText("Clarity")).toBeInTheDocument();
    expect(screen.getByText("91%")).toBeInTheDocument();
    expect(screen.getByText("65%")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByText("92")).toBeInTheDocument();
    expect(container.innerHTML).toContain("var(--color-green-500)");
    expect(container.innerHTML).toContain("bg-yellow-500");
    expect(container.innerHTML).toContain("bg-red-500");
  });

  it("renders the compact layout with medium overall scores", () => {
    const { container } = render(
      <LLMQualityScores
        compact
        scores={{
          accuracy: 0.75,
          completeness: 0.6,
          clarity: 0.55,
          overall: 0.6,
        }}
      />,
    );

    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText("Accuracy")).toBeInTheDocument();
    expect(container.innerHTML).toContain("var(--color-yellow-500)");
    expect(container.innerHTML).toContain("size-14");
  });

  it("renders the compact layout with low overall scores", () => {
    const { container } = render(
      <LLMQualityScores
        compact
        scores={{
          accuracy: 0.2,
          completeness: 0.35,
          clarity: 0.45,
          overall: 0.4,
        }}
      />,
    );

    expect(screen.getByText("40")).toBeInTheDocument();
    expect(container.innerHTML).toContain("var(--color-red-500)");
  });
});
