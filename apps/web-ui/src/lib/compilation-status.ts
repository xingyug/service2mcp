import type { CompilationStatus } from "@/types/api";

export const ALL_COMPILATION_STATUSES: CompilationStatus[] = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "rolled_back",
];

export const IN_PROGRESS_COMPILATION_STATUSES = new Set<CompilationStatus>([
  "pending",
  "running",
]);

export function isCompilationInProgress(
  status?: CompilationStatus,
): boolean {
  return status != null && IN_PROGRESS_COMPILATION_STATUSES.has(status);
}

export function formatCompilationStatus(status: CompilationStatus): string {
  switch (status) {
    case "pending":
      return "Pending";
    case "running":
      return "Running";
    case "succeeded":
      return "Succeeded";
    case "failed":
      return "Failed";
    case "rolled_back":
      return "Rolled Back";
  }
}
