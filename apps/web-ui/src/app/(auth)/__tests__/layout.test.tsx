import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AuthLayout from "../layout";

describe("AuthLayout", () => {
  it("centers its children inside the auth shell", () => {
    const { container } = render(
      <AuthLayout>
        <div>Login form</div>
      </AuthLayout>,
    );

    expect(screen.getByText("Login form")).toBeInTheDocument();
    expect(container.firstChild).toHaveClass(
      "flex",
      "min-h-screen",
      "items-center",
      "justify-center",
    );
  });
});
