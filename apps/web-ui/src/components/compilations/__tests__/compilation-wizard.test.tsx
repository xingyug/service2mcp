import { describe, expect, it } from "vitest";

import {
  INITIAL_FORM_DATA,
  buildRequest,
  type WizardFormData,
} from "../compilation-wizard";

describe("buildRequest", () => {
  it("preserves a locked service id while keeping the display name override", () => {
    const form: WizardFormData = {
      ...INITIAL_FORM_DATA,
      sourceMode: "url",
      sourceUrl: "https://example.com/spec.yaml",
      createdBy: "alice",
      serviceName: "Billing API",
    };

    expect(buildRequest(form, "billing-api")).toEqual({
      source_url: "https://example.com/spec.yaml",
      created_by: "alice",
      service_id: "billing-api",
      service_name: "Billing API",
      options: {
        runtime_mode: "generic",
      },
    });
  });
});
