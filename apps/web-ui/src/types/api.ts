// === Compilation Types ===

export type CompilationStatus =
  | "PENDING"
  | "DETECTING"
  | "EXTRACTING"
  | "ENHANCING"
  | "VALIDATING_IR"
  | "GENERATING"
  | "BUILDING"
  | "DEPLOYING"
  | "VALIDATING_RUNTIME"
  | "ROUTING"
  | "REGISTERING"
  | "PUBLISHED"
  | "FAILED"
  | "ROLLING_BACK"
  | "ROLLED_BACK";

export type CompilationStage =
  | "detect"
  | "extract"
  | "enhance"
  | "validate_ir"
  | "generate"
  | "build"
  | "deploy"
  | "validate_runtime"
  | "route"
  | "register";

export type CompilationEventType =
  | "stage_started"
  | "stage_completed"
  | "stage_failed"
  | "job_started"
  | "job_completed"
  | "job_failed";

export interface CompilationCreateRequest {
  source_url?: string;
  source_content?: string;
  created_by: string;
  service_name?: string;
  options?: CompilationOptions;
  auth_config?: AuthConfig;
}

export interface CompilationOptions {
  force_protocol?: "openapi" | "rest" | "graphql" | "sql" | "grpc" | "soap";
  runtime_mode?: "generic" | "codegen";
  skip_enhancement?: boolean;
  tenant?: string;
  environment?: string;
}

export interface CompilationJobResponse {
  job_id: string;
  status: CompilationStatus;
  current_stage?: CompilationStage;
  failed_stage?: CompilationStage;
  progress_pct?: number;
  created_at: string;
  completed_at?: string;
  error_message?: string;
  artifacts?: {
    ir_id?: string;
    image_digest?: string;
    deployment_id?: string;
  };
}

export interface CompilationEvent {
  type: CompilationEventType;
  stage?: CompilationStage;
  detail?: string;
  timestamp: string;
  attempt?: number;
}

// === Service Types ===

export interface ServiceSummary {
  service_id: string;
  name: string;
  protocol: string;
  active_version?: number;
  version_count: number;
  last_compiled?: string;
  tenant?: string;
  environment?: string;
}

export interface ServiceListResponse {
  services: ServiceSummary[];
}

// === IR Types ===

export type RiskLevel = "safe" | "cautious" | "dangerous" | "unknown";
export type FieldSource = "extractor" | "llm" | "user_override";
export type ToolIntent = "discovery" | "action";

export interface Param {
  name: string;
  type: string;
  required: boolean;
  description: string;
  default?: unknown;
  source: FieldSource;
  confidence: number;
}

export interface RiskMetadata {
  writes_state?: boolean;
  destructive?: boolean;
  external_side_effect?: boolean;
  idempotent?: boolean;
  risk_level: RiskLevel;
  confidence: number;
  source: FieldSource;
}

export interface ResponseStrategy {
  pagination?: Record<string, unknown>;
  max_response_bytes?: number;
  field_filter?: string[];
  truncation_policy: "none" | "truncate" | "summarize";
}

export interface Operation {
  id: string;
  name: string;
  description: string;
  method?: string;
  path?: string;
  params: Param[];
  response_schema?: Record<string, unknown>;
  risk: RiskMetadata;
  response_strategy?: ResponseStrategy;
  tags: string[];
  source: FieldSource;
  confidence: number;
  enabled: boolean;
  tool_intent?: ToolIntent;
}

export interface ToolGroup {
  group_id: string;
  label: string;
  description: string;
  operation_ids: string[];
}

export interface AuthConfig {
  type: "bearer" | "basic" | "api_key" | "custom_header" | "oauth2" | "none";
  compile_time_secret_ref?: string;
  runtime_secret_ref?: string;
  header_name?: string;
  username?: string;
  password_secret_ref?: string;
  token_url?: string;
  client_id?: string;
  client_secret_ref?: string;
}

export interface ServiceIR {
  ir_version: string;
  compiler_version: string;
  source_url?: string;
  source_hash: string;
  protocol: string;
  service_name: string;
  service_description: string;
  base_url: string;
  auth: AuthConfig;
  operations: Operation[];
  metadata: Record<string, unknown>;
  created_at: string;
  tenant?: string;
  environment?: string;
  tool_grouping?: ToolGroup[];
}

// === Artifact Types ===

export type ArtifactDiffOp = "add" | "remove" | "modify";

export interface ArtifactVersionResponse {
  service_id: string;
  version_number: number;
  ir: ServiceIR;
  is_active: boolean;
  created_at: string;
  route_config?: Record<string, unknown>;
}

export interface ArtifactVersionListResponse {
  versions: ArtifactVersionResponse[];
}

export interface ArtifactDiffChange {
  field: string;
  old_value?: unknown;
  new_value?: unknown;
}

export interface ArtifactDiffOperation {
  operation_id: string;
  diff_type: ArtifactDiffOp;
  changes?: ArtifactDiffChange[];
}

export interface ArtifactDiffResponse {
  from_version: number;
  to_version: number;
  added_operations: Operation[];
  removed_operations: Operation[];
  changed_operations: ArtifactDiffOperation[];
}

// === Access Control Types ===

export interface TokenValidationRequest {
  token: string;
}

export interface TokenPrincipal {
  subject: string;
  issuer: string;
  audience: string;
  email?: string;
  roles?: string[];
  expires_at?: string;
}

export interface PATCreateRequest {
  username: string;
  name: string;
  email?: string;
}

export interface PATResponse {
  pat_id: string;
  username: string;
  name: string;
  token?: string;
  created_at: string;
  revoked_at?: string;
}

export interface PATListResponse {
  pats: PATResponse[];
}

// === Authorization Types ===

export type SubjectType = "user" | "group";
export type PolicyDecision = "allow" | "deny" | "require_approval";

export interface PolicyCreateRequest {
  subject_type: SubjectType;
  subject_id: string;
  resource_id: string;
  action_pattern: string;
  risk_threshold: RiskLevel;
  decision: PolicyDecision;
}

export interface PolicyUpdateRequest {
  action_pattern?: string;
  risk_threshold?: RiskLevel;
  decision?: PolicyDecision;
}

export interface PolicyResponse {
  policy_id: string;
  subject_type: SubjectType;
  subject_id: string;
  resource_id: string;
  action_pattern: string;
  risk_threshold: RiskLevel;
  decision: PolicyDecision;
  created_at: string;
  updated_at?: string;
}

export interface PolicyListResponse {
  policies: PolicyResponse[];
}

export interface PolicyEvaluationRequest {
  subject_type: SubjectType;
  subject_id: string;
  action: string;
  resource_id: string;
}

export interface PolicyEvaluationResponse {
  decision: PolicyDecision;
  matched_policy_id?: string;
  reason?: string;
}

// === Audit Types ===

export interface AuditLogEntry {
  id: string;
  actor: string;
  action: string;
  resource: string;
  detail?: string;
  timestamp: string;
}

export interface AuditLogListResponse {
  entries: AuditLogEntry[];
}

// === Gateway Types ===

export interface ReconcileResponse {
  synced: number;
  deleted: number;
  errors: number;
}

export interface ServiceRouteRequest {
  service_id: string;
  version_number: number;
  route_config: Record<string, unknown>;
}

export interface ServiceRouteResponse {
  service_id: string;
  status: string;
  message?: string;
}
