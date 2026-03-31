import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ServiceDetailLayout from "../layout";

describe("ServiceDetailLayout", () => {
  it("returns its children without adding extra wrappers", () => {
    const { container } = render(
      <ServiceDetailLayout>
        <section>Service detail body</section>
      </ServiceDetailLayout>,
    );

    expect(screen.getByText("Service detail body")).toBeInTheDocument();
    expect(container.firstChild?.nodeName).toBe("SECTION");
  });
});
