"use client";

import { useState, useRef, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Upload, AlertCircle } from "lucide-react";

import { useCreateCompilation } from "@/hooks/use-api";
import { useAuthStore } from "@/stores/auth-store";
import type {
  CompilationCreateRequest,
  CompilationOptions,
  AuthConfig,
} from "@/types/api";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { WizardStepIndicator } from "./compilation-wizard-steps";
import { ProtocolSelector } from "./protocol-selector";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SourceMode = "url" | "paste" | "upload";
export type AuthType = AuthConfig["type"];
export type Protocol = NonNullable<CompilationOptions["force_protocol"]> | "";
export type RuntimeMode = NonNullable<CompilationOptions["runtime_mode"]>;

export interface WizardFormData {
  sourceMode: SourceMode;
  sourceUrl: string;
  sourceContent: string;
  sourceFileName: string;
  serviceName: string;
  createdBy: string;

  forceProtocol: Protocol;
  runtimeMode: RuntimeMode;
  skipEnhancement: boolean;
  tenant: string;
  environment: string;

  authType: AuthType;
  bearerSecretRef: string;
  basicUsername: string;
  basicPasswordRef: string;
  apiKeyHeaderName: string;
  apiKeySecretRef: string;
  customHeaderName: string;
  customHeaderValueRef: string;
  oauth2TokenUrl: string;
  oauth2ClientId: string;
  oauth2ClientSecretRef: string;
}

export const INITIAL_FORM_DATA: WizardFormData = {
  sourceMode: "url",
  sourceUrl: "",
  sourceContent: "",
  sourceFileName: "",
  serviceName: "",
  createdBy: "",

  forceProtocol: "",
  runtimeMode: "generic",
  skipEnhancement: false,
  tenant: "",
  environment: "",

  authType: "none",
  bearerSecretRef: "",
  basicUsername: "",
  basicPasswordRef: "",
  apiKeyHeaderName: "",
  apiKeySecretRef: "",
  customHeaderName: "",
  customHeaderValueRef: "",
  oauth2TokenUrl: "",
  oauth2ClientId: "",
  oauth2ClientSecretRef: "",
};

const STEP_LABELS = [
  "Source Input",
  "Protocol & Options",
  "Auth Configuration",
  "Review & Submit",
];

const ACCEPTED_EXTENSIONS = ".yaml,.yml,.json,.proto,.wsdl,.graphql";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function deriveServiceName(url: string): string {
  try {
    const pathname = new URL(url).pathname;
    const filename = pathname.split("/").pop() ?? "";
    return filename.replace(/\.(yaml|yml|json|proto|wsdl|graphql)$/i, "");
  } catch {
    return "";
  }
}

export function buildRequest(form: WizardFormData): CompilationCreateRequest {
  const options: CompilationOptions = {
    runtime_mode: form.runtimeMode,
  };

  if (form.forceProtocol) {
    options.force_protocol =
      form.forceProtocol as CompilationOptions["force_protocol"];
  }
  if (form.skipEnhancement) options.skip_enhancement = true;
  if (form.tenant) options.tenant = form.tenant;
  if (form.environment) options.environment = form.environment;

  if (form.authType !== "none") {
    const authConfig: AuthConfig = { type: form.authType };
    if (form.authType === "bearer") {
      authConfig.compile_time_secret_ref = form.bearerSecretRef;
    } else if (form.authType === "basic") {
      authConfig.username = form.basicUsername;
      authConfig.password_secret_ref = form.basicPasswordRef;
    } else if (form.authType === "api_key") {
      authConfig.header_name = form.apiKeyHeaderName;
      authConfig.compile_time_secret_ref = form.apiKeySecretRef;
    } else if (form.authType === "custom_header") {
      authConfig.header_name = form.customHeaderName;
      authConfig.compile_time_secret_ref = form.customHeaderValueRef;
    } else if (form.authType === "oauth2") {
      authConfig.token_url = form.oauth2TokenUrl;
      authConfig.client_id = form.oauth2ClientId;
      authConfig.client_secret_ref = form.oauth2ClientSecretRef;
    }
    options.auth_config = authConfig;
  }

  const req: CompilationCreateRequest = {
    created_by: form.createdBy,
    options,
  };

  if (form.sourceMode === "url") {
    req.source_url = form.sourceUrl;
  } else {
    req.source_content = form.sourceContent;
  }

  if (form.serviceName) req.service_name = form.serviceName;

  return req;
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

export function validateStep(step: number, form: WizardFormData): string | null {
  switch (step) {
    case 0: {
      if (!form.createdBy.trim()) return "Created by is required.";
      if (form.sourceMode === "url" && !form.sourceUrl.trim())
        return "Source URL is required.";
      if (form.sourceMode === "paste" && !form.sourceContent.trim())
        return "Source content is required.";
      if (form.sourceMode === "upload" && !form.sourceContent.trim())
        return "Please upload a file.";
      return null;
    }
    case 1:
      return null;
    case 2: {
      if (form.authType === "bearer" && !form.bearerSecretRef.trim())
        return "Secret reference is required for bearer auth.";
      if (form.authType === "basic") {
        if (!form.basicUsername.trim()) return "Username is required.";
        if (!form.basicPasswordRef.trim())
          return "Password secret reference is required.";
      }
      if (form.authType === "api_key") {
        if (!form.apiKeyHeaderName.trim()) return "Header name is required.";
        if (!form.apiKeySecretRef.trim())
          return "Secret reference is required.";
      }
      if (form.authType === "custom_header") {
        if (!form.customHeaderName.trim()) return "Header name is required.";
        if (!form.customHeaderValueRef.trim())
          return "Value secret reference is required.";
      }
      if (form.authType === "oauth2") {
        if (!form.oauth2TokenUrl.trim()) return "Token URL is required.";
        if (!form.oauth2ClientId.trim()) return "Client ID is required.";
        if (!form.oauth2ClientSecretRef.trim())
          return "Client secret reference is required.";
      }
      return null;
    }
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Main Wizard
// ---------------------------------------------------------------------------

export function CompilationWizard({
  initialServiceName = "",
}: {
  initialServiceName?: string;
}) {
  const router = useRouter();
  const username = useAuthStore((s) => s.user?.username ?? "");
  const createMutation = useCreateCompilation();

  const [step, setStep] = useState(0);
  const [form, setForm] = useState<WizardFormData>(() => ({
    ...INITIAL_FORM_DATA,
    createdBy: username || INITIAL_FORM_DATA.createdBy,
    serviceName: initialServiceName || INITIAL_FORM_DATA.serviceName,
  }));
  const [error, setError] = useState<string | null>(null);

  const effectiveForm = useMemo(
    () => ({
      ...form,
      serviceName: form.serviceName.trim() || initialServiceName || "",
    }),
    [form, initialServiceName],
  );

  const updateField = useCallback(
    <K extends keyof WizardFormData>(field: K, value: WizardFormData[K]) => {
      setForm((prev) => ({ ...prev, [field]: value }));
      setError(null);
    },
    [],
  );

  const goToStep = useCallback(
    (target: number) => {
      if (target < step) {
        setStep(target);
        setError(null);
        return;
      }
      for (let i = step; i < target; i++) {
        const err = validateStep(i, effectiveForm);
        if (err) {
          setStep(i);
          setError(err);
          return;
        }
      }
      setStep(target);
      setError(null);
    },
    [step, effectiveForm],
  );

  const handleNext = useCallback(() => {
    const err = validateStep(step, effectiveForm);
    if (err) {
      setError(err);
      return;
    }
    setStep((s) => Math.min(s + 1, STEP_LABELS.length - 1));
    setError(null);
  }, [step, effectiveForm]);

  const handleBack = useCallback(() => {
    setStep((s) => Math.max(s - 1, 0));
    setError(null);
  }, []);

  const handleSubmit = useCallback(async () => {
    setError(null);
    const req = buildRequest(effectiveForm);
    try {
      const result = await createMutation.mutateAsync(req);
      toast.success("Compilation job created successfully!");
      router.push(`/compilations/${result.job_id}`);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to create compilation.";
      setError(message);
      toast.error(message);
    }
  }, [effectiveForm, createMutation, router]);

  const handleSourceUrlChange = useCallback(
    (url: string) => {
      updateField("sourceUrl", url);
      if (!form.serviceName) {
        const derived = deriveServiceName(url);
        if (derived) updateField("serviceName", derived);
      }
    },
    [form.serviceName, updateField],
  );

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <WizardStepIndicator
        steps={STEP_LABELS}
        currentStep={step}
        onStepClick={goToStep}
      />

      {error && (
        <div className="flex items-center gap-2 rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {step === 0 && (
        <SourceInputStep
          form={form}
          initialServiceName={initialServiceName}
          updateField={updateField}
          onSourceUrlChange={handleSourceUrlChange}
        />
      )}
      {step === 1 && (
        <ProtocolOptionsStep form={form} updateField={updateField} />
      )}
      {step === 2 && <AuthConfigStep form={form} updateField={updateField} />}
      {step === 3 && <ReviewStep form={effectiveForm} onEditStep={goToStep} />}

      <div className="flex items-center justify-between pt-2">
        <Button variant="outline" onClick={handleBack} disabled={step === 0}>
          Back
        </Button>
        {step < STEP_LABELS.length - 1 ? (
          <Button onClick={handleNext}>Continue</Button>
        ) : (
          <Button onClick={handleSubmit} disabled={createMutation.isPending}>
            {createMutation.isPending ? "Creating…" : "Create Compilation"}
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared step props
// ---------------------------------------------------------------------------

interface StepProps {
  form: WizardFormData;
  updateField: <K extends keyof WizardFormData>(
    field: K,
    value: WizardFormData[K],
  ) => void;
}

// ---------------------------------------------------------------------------
// Step 1 – Source Input
// ---------------------------------------------------------------------------

function SourceInputStep({
  form,
  initialServiceName = "",
  updateField,
  onSourceUrlChange,
}: StepProps & {
  initialServiceName?: string;
  onSourceUrlChange: (url: string) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleFileRead = useCallback(
    (file: File) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        const content = e.target?.result as string;
        updateField("sourceContent", content);
        updateField("sourceFileName", file.name);
      };
      reader.readAsText(file);
    },
    [updateField],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFileRead(file);
    },
    [handleFileRead],
  );

  const handleFileInput = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFileRead(file);
    },
    [handleFileRead],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Source Input</CardTitle>
        <CardDescription>
          Provide the API specification to compile into MCP tools.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Source mode */}
        <div className="space-y-3">
          <Label>Source Type</Label>
          <RadioGroup
            value={form.sourceMode}
            onValueChange={(val) =>
              updateField("sourceMode", val as SourceMode)
            }
            className="flex flex-wrap gap-4"
          >
            {(
              [
                ["url", "URL"],
                ["paste", "Paste Content"],
                ["upload", "Upload File"],
              ] as const
            ).map(([value, label]) => (
              <div key={value} className="flex items-center gap-2">
                <RadioGroupItem value={value} id={`source-${value}`} />
                <Label
                  htmlFor={`source-${value}`}
                  className="cursor-pointer font-normal"
                >
                  {label}
                </Label>
              </div>
            ))}
          </RadioGroup>
        </div>

        <Separator />

        {/* URL mode */}
        {form.sourceMode === "url" && (
          <div className="space-y-2">
            <Label htmlFor="source-url">Specification URL</Label>
            <Input
              id="source-url"
              type="url"
              placeholder="https://api.example.com/openapi.yaml"
              value={form.sourceUrl}
              onChange={(e) => onSourceUrlChange(e.target.value)}
            />
          </div>
        )}

        {/* Paste mode */}
        {form.sourceMode === "paste" && (
          <div className="space-y-2">
            <Label htmlFor="source-content">
              Specification Content (YAML / JSON)
            </Label>
            <Textarea
              id="source-content"
              placeholder="Paste your API spec here…"
              value={form.sourceContent}
              onChange={(e) => updateField("sourceContent", e.target.value)}
              className="min-h-[200px] font-mono text-xs"
            />
          </div>
        )}

        {/* Upload mode */}
        {form.sourceMode === "upload" && (
          <div className="space-y-2">
            <Label>Upload Specification File</Label>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`flex cursor-pointer flex-col items-center gap-3 rounded-lg border-2 border-dashed p-8 transition-colors ${
                isDragging
                  ? "border-primary bg-primary/5"
                  : "border-border hover:border-primary/50"
              }`}
            >
              <Upload className="h-8 w-8 text-muted-foreground" />
              {form.sourceFileName ? (
                <div className="text-center">
                  <p className="text-sm font-medium">{form.sourceFileName}</p>
                  <p className="text-xs text-muted-foreground">
                    Click or drop to replace
                  </p>
                </div>
              ) : (
                <div className="text-center">
                  <p className="text-sm font-medium">
                    Drop a file here or click to browse
                  </p>
                  <p className="text-xs text-muted-foreground">
                    .yaml, .yml, .json, .proto, .wsdl, .graphql
                  </p>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept={ACCEPTED_EXTENSIONS}
                onChange={handleFileInput}
              />
            </div>
          </div>
        )}

        <Separator />

        {/* Service name */}
        <div className="space-y-2">
          <Label htmlFor="service-name">
            Service Name{" "}
            <span className="font-normal text-muted-foreground">
              (optional – auto-derived from URL)
            </span>
          </Label>
          <Input
            id="service-name"
            placeholder="my-api-service"
            value={form.serviceName || initialServiceName}
            onChange={(e) => updateField("serviceName", e.target.value)}
          />
        </div>

        {/* Created by */}
        <div className="space-y-2">
          <Label htmlFor="created-by">Created By</Label>
          <Input
            id="created-by"
            placeholder="username"
            value={form.createdBy}
            onChange={(e) => updateField("createdBy", e.target.value)}
          />
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Step 2 – Protocol & Options
// ---------------------------------------------------------------------------

function ProtocolOptionsStep({ form, updateField }: StepProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Protocol &amp; Options</CardTitle>
        <CardDescription>
          Configure how the API specification should be compiled.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Protocol */}
        <div className="space-y-3">
          <Label>Protocol</Label>
          <ProtocolSelector
            value={form.forceProtocol}
            onChange={(val) => updateField("forceProtocol", val as Protocol)}
          />
        </div>

        <Separator />

        {/* Runtime mode */}
        <div className="space-y-3">
          <Label>Runtime Mode</Label>
          <RadioGroup
            value={form.runtimeMode}
            onValueChange={(val) =>
              updateField("runtimeMode", val as RuntimeMode)
            }
            className="flex gap-6"
          >
            <div className="flex items-center gap-2">
              <RadioGroupItem value="generic" id="mode-generic" />
              <Label
                htmlFor="mode-generic"
                className="cursor-pointer font-normal"
              >
                Generic{" "}
                <Badge variant="secondary" className="ml-1">
                  Recommended
                </Badge>
              </Label>
            </div>
            <div className="flex items-center gap-2">
              <RadioGroupItem value="codegen" id="mode-codegen" />
              <Label
                htmlFor="mode-codegen"
                className="cursor-pointer font-normal"
              >
                Codegen
              </Label>
            </div>
          </RadioGroup>
        </div>

        <Separator />

        {/* Skip LLM Enhancement */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label htmlFor="skip-enhancement">Skip LLM Enhancement</Label>
            <p className="text-xs text-muted-foreground">
              Disable AI-powered enhancement of tool descriptions and
              parameters.
            </p>
          </div>
          <Switch
            id="skip-enhancement"
            checked={form.skipEnhancement}
            onCheckedChange={(val) => updateField("skipEnhancement", val)}
          />
        </div>

        <Separator />

        {/* Tenant & Environment */}
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="tenant">
              Tenant{" "}
              <span className="font-normal text-muted-foreground">
                (optional)
              </span>
            </Label>
            <Input
              id="tenant"
              placeholder="default"
              value={form.tenant}
              onChange={(e) => updateField("tenant", e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="environment">
              Environment{" "}
              <span className="font-normal text-muted-foreground">
                (optional)
              </span>
            </Label>
            <Input
              id="environment"
              placeholder="production"
              value={form.environment}
              onChange={(e) => updateField("environment", e.target.value)}
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Step 3 – Auth Configuration
// ---------------------------------------------------------------------------

function AuthConfigStep({ form, updateField }: StepProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Auth Configuration</CardTitle>
        <CardDescription>
          Configure how compiled tools authenticate against the target API.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <Label>Authentication Type</Label>
          <Select
            value={form.authType}
            onValueChange={(val) => updateField("authType", val as AuthType)}
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none">None</SelectItem>
              <SelectItem value="bearer">Bearer Token</SelectItem>
              <SelectItem value="basic">Basic Auth</SelectItem>
              <SelectItem value="api_key">API Key</SelectItem>
              <SelectItem value="custom_header">Custom Header</SelectItem>
              <SelectItem value="oauth2">OAuth 2.0</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {form.authType !== "none" && <Separator />}

        {/* Bearer */}
        {form.authType === "bearer" && (
          <div className="space-y-2">
            <Label htmlFor="bearer-ref">Secret Reference</Label>
            <Input
              id="bearer-ref"
              placeholder="vault://secrets/api-token"
              value={form.bearerSecretRef}
              onChange={(e) => updateField("bearerSecretRef", e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Reference to the bearer token stored in your secret manager.
            </p>
          </div>
        )}

        {/* Basic */}
        {form.authType === "basic" && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="basic-username">Username</Label>
              <Input
                id="basic-username"
                placeholder="api-user"
                value={form.basicUsername}
                onChange={(e) => updateField("basicUsername", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="basic-password-ref">
                Password Secret Reference
              </Label>
              <Input
                id="basic-password-ref"
                placeholder="vault://secrets/api-password"
                value={form.basicPasswordRef}
                onChange={(e) => updateField("basicPasswordRef", e.target.value)}
              />
            </div>
          </div>
        )}

        {/* API Key */}
        {form.authType === "api_key" && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="apikey-header">Header Name</Label>
              <Input
                id="apikey-header"
                placeholder="X-API-Key"
                value={form.apiKeyHeaderName}
                onChange={(e) =>
                  updateField("apiKeyHeaderName", e.target.value)
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="apikey-ref">Secret Reference</Label>
              <Input
                id="apikey-ref"
                placeholder="vault://secrets/api-key"
                value={form.apiKeySecretRef}
                onChange={(e) => updateField("apiKeySecretRef", e.target.value)}
              />
            </div>
          </div>
        )}

        {/* Custom Header */}
        {form.authType === "custom_header" && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="custom-header-name">Header Name</Label>
              <Input
                id="custom-header-name"
                placeholder="X-Custom-Auth"
                value={form.customHeaderName}
                onChange={(e) =>
                  updateField("customHeaderName", e.target.value)
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="custom-header-ref">Value Secret Reference</Label>
              <Input
                id="custom-header-ref"
                placeholder="vault://secrets/custom-header-value"
                value={form.customHeaderValueRef}
                onChange={(e) =>
                  updateField("customHeaderValueRef", e.target.value)
                }
              />
            </div>
          </div>
        )}

        {/* OAuth2 */}
        {form.authType === "oauth2" && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="oauth2-token-url">Token URL</Label>
              <Input
                id="oauth2-token-url"
                type="url"
                placeholder="https://auth.example.com/oauth/token"
                value={form.oauth2TokenUrl}
                onChange={(e) => updateField("oauth2TokenUrl", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="oauth2-client-id">Client ID</Label>
              <Input
                id="oauth2-client-id"
                placeholder="my-client-id"
                value={form.oauth2ClientId}
                onChange={(e) => updateField("oauth2ClientId", e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="oauth2-client-secret-ref">
                Client Secret Reference
              </Label>
              <Input
                id="oauth2-client-secret-ref"
                placeholder="vault://secrets/oauth-client-secret"
                value={form.oauth2ClientSecretRef}
                onChange={(e) =>
                  updateField("oauth2ClientSecretRef", e.target.value)
                }
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Step 4 – Review & Submit
// ---------------------------------------------------------------------------

function ReviewStep({
  form,
  onEditStep,
}: {
  form: WizardFormData;
  onEditStep: (step: number) => void;
}) {
  const sourceLabel =
    form.sourceMode === "url"
      ? form.sourceUrl
      : form.sourceMode === "upload"
        ? form.sourceFileName
        : "(pasted content)";

  const protocolLabel = form.forceProtocol || "Auto-detect";
  const authLabel =
    form.authType === "none"
      ? "None"
      : form.authType.replace(/_/g, " ").replace(/\b\w/g, (c) =>
          c.toUpperCase(),
        );

  return (
    <div className="space-y-4">
      {/* Source */}
      <Card
        className="cursor-pointer transition-shadow hover:ring-2 hover:ring-primary/20"
        onClick={() => onEditStep(0)}
      >
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            Source Input
            <Badge variant="outline" className="font-normal">
              Edit
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Mode</dt>
              <dd className="font-medium capitalize">{form.sourceMode}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="shrink-0 text-muted-foreground">Source</dt>
              <dd className="truncate font-medium">{sourceLabel}</dd>
            </div>
            {form.serviceName && (
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Service Name</dt>
                <dd className="font-medium">{form.serviceName}</dd>
              </div>
            )}
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Created By</dt>
              <dd className="font-medium">{form.createdBy}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {/* Protocol & Options */}
      <Card
        className="cursor-pointer transition-shadow hover:ring-2 hover:ring-primary/20"
        onClick={() => onEditStep(1)}
      >
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            Protocol &amp; Options
            <Badge variant="outline" className="font-normal">
              Edit
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Protocol</dt>
              <dd className="font-medium capitalize">{protocolLabel}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Runtime Mode</dt>
              <dd className="font-medium capitalize">{form.runtimeMode}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-muted-foreground">LLM Enhancement</dt>
              <dd className="font-medium">
                {form.skipEnhancement ? "Skipped" : "Enabled"}
              </dd>
            </div>
            {form.tenant && (
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Tenant</dt>
                <dd className="font-medium">{form.tenant}</dd>
              </div>
            )}
            {form.environment && (
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Environment</dt>
                <dd className="font-medium">{form.environment}</dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Auth */}
      <Card
        className="cursor-pointer transition-shadow hover:ring-2 hover:ring-primary/20"
        onClick={() => onEditStep(2)}
      >
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            Auth Configuration
            <Badge variant="outline" className="font-normal">
              Edit
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid gap-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-muted-foreground">Auth Type</dt>
              <dd className="font-medium">{authLabel}</dd>
            </div>
            {form.authType === "bearer" && (
              <div className="flex justify-between gap-4">
                <dt className="shrink-0 text-muted-foreground">Secret Ref</dt>
                <dd className="truncate font-medium">
                  {form.bearerSecretRef}
                </dd>
              </div>
            )}
            {form.authType === "basic" && (
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Username</dt>
                <dd className="font-medium">{form.basicUsername}</dd>
              </div>
            )}
            {form.authType === "api_key" && (
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Header</dt>
                <dd className="font-medium">{form.apiKeyHeaderName}</dd>
              </div>
            )}
            {form.authType === "custom_header" && (
              <div className="flex justify-between">
                <dt className="text-muted-foreground">Header</dt>
                <dd className="font-medium">{form.customHeaderName}</dd>
              </div>
            )}
            {form.authType === "oauth2" && (
              <>
                <div className="flex justify-between gap-4">
                  <dt className="shrink-0 text-muted-foreground">Token URL</dt>
                  <dd className="truncate font-medium">
                    {form.oauth2TokenUrl}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-muted-foreground">Client ID</dt>
                  <dd className="font-medium">{form.oauth2ClientId}</dd>
                </div>
              </>
            )}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
