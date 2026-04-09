"use client";

import { useSearchParams } from "next/navigation";

import { CompilationWizard } from "@/components/compilations/compilation-wizard";

export default function NewCompilationPage() {
  const searchParams = useSearchParams();
  const initialServiceId = searchParams.get("service_id") ?? "";
  const initialServiceName =
    searchParams.get("service_name") ?? initialServiceId;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">New Compilation</h1>
        <p className="text-muted-foreground">
          Start a new tool compilation job.
        </p>
      </div>
      <CompilationWizard
        initialServiceName={initialServiceName}
        initialServiceId={initialServiceId}
      />
    </div>
  );
}
