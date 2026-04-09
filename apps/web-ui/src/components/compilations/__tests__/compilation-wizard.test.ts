import { describe, expect, it } from "vitest";

import {
  INITIAL_FORM_DATA,
  buildRequest,
  deriveServiceName,
  validateStep,
  type WizardFormData,
} from "../compilation-wizard";

function buildForm(
  overrides: Partial<WizardFormData> = {},
): WizardFormData {
  return {
    ...INITIAL_FORM_DATA,
    createdBy: "alice",
    sourceMode: "url",
    sourceUrl: "https://example.com/spec.yaml",
    ...overrides,
  };
}

describe("compilation-wizard helpers", () => {
  it("derives a service name from a source URL", () => {
    expect(deriveServiceName("https://example.com/specs/petstore.yaml")).toBe(
      "petstore",
    );
  });

  it("returns an empty derived name for invalid URLs", () => {
    expect(deriveServiceName("not-a-url")).toBe("");
  });

  it("builds a basic request without auth config when auth is none", () => {
    const request = buildRequest(buildForm({ serviceName: "Petstore" }));

    expect(request).toEqual({
      created_by: "alice",
      service_name: "Petstore",
      source_url: "https://example.com/spec.yaml",
      options: {
        runtime_mode: "generic",
      },
    });
  });

  it("builds bearer auth config into compilation options", () => {
    const request = buildRequest(
      buildForm({
        authType: "bearer",
        bearerSecretRef: "secret://bearer",
      }),
    );

    expect(request.options?.auth).toEqual({
      type: "bearer",
      runtime_secret_ref: "secret://bearer",
    });
  });

  it("builds basic auth config into compilation options", () => {
    const request = buildRequest(
      buildForm({
        authType: "basic",
        basicUsername: "svc-user",
        basicPasswordRef: "secret://password",
      }),
    );

    expect(request.options?.auth).toEqual({
      type: "basic",
      basic_username: "svc-user",
      basic_password_ref: "secret://password",
    });
  });

  it("builds API key auth config into compilation options", () => {
    const request = buildRequest(
      buildForm({
        authType: "api_key",
        apiKeyHeaderName: "X-API-Key",
        apiKeySecretRef: "secret://apikey",
      }),
    );

    expect(request.options?.auth).toEqual({
      type: "api_key",
      api_key_param: "X-API-Key",
      api_key_location: "header",
      runtime_secret_ref: "secret://apikey",
    });
  });

  it("builds custom header auth config into compilation options", () => {
    const request = buildRequest(
      buildForm({
        authType: "custom_header",
        customHeaderName: "X-Custom-Auth",
        customHeaderValueRef: "secret://header",
      }),
    );

    expect(request.options?.auth).toEqual({
      type: "custom_header",
      header_name: "X-Custom-Auth",
      runtime_secret_ref: "secret://header",
    });
  });

  it("builds OAuth2 auth config into compilation options", () => {
    const request = buildRequest(
      buildForm({
        authType: "oauth2",
        oauth2TokenUrl: "https://auth.example.com/token",
        oauth2ClientId: "client-id",
        oauth2ClientSecretRef: "secret://oauth2",
      }),
    );

    expect(request.options?.auth).toEqual({
      type: "oauth2",
      oauth2: {
        token_url: "https://auth.example.com/token",
        client_id: "client-id",
        client_secret_ref: "secret://oauth2",
      },
    });
  });

  it("uses source_content for pasted/uploaded sources", () => {
    const request = buildRequest(
      buildForm({
        sourceMode: "paste",
        sourceUrl: "",
        sourceContent: '{"openapi":"3.1.0"}',
      }),
    );

    expect(request.source_content).toBe('{"openapi":"3.1.0"}');
    expect(request.source_url).toBeUndefined();
  });

  it("validates missing bearer secret references", () => {
    expect(
      validateStep(
        2,
        buildForm({
          authType: "bearer",
          bearerSecretRef: "",
        }),
      ),
    ).toBe("Secret reference is required for bearer auth.");
  });

  it("validates missing basic auth fields", () => {
    expect(
      validateStep(
        2,
        buildForm({
          authType: "basic",
          basicUsername: "",
          basicPasswordRef: "",
        }),
      ),
    ).toBe("Username is required.");
  });
});
