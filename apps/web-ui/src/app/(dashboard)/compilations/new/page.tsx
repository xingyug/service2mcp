import { CompilationWizard } from "@/components/compilations/compilation-wizard";

export default function NewCompilationPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">New Compilation</h1>
        <p className="text-muted-foreground">
          Start a new tool compilation job.
        </p>
      </div>
      <CompilationWizard />
    </div>
  );
}
