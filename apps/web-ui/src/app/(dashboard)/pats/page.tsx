"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Key, Plus, Copy, Check, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { authApi } from "@/lib/api-client";
import { queryKeys } from "@/lib/query-keys";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";
import type { PATResponse } from "@/types/api";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

// ── Helpers ─────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

// ── Component ───────────────────────────────────────────────────────────────

export default function PATTokensPage() {
  const user = useAuthStore((s) => s.user);
  const queryClient = useQueryClient();

  const [createOpen, setCreateOpen] = useState(false);
  const [tokenName, setTokenName] = useState("");

  // Created token shown once
  const [createdToken, setCreatedToken] = useState<string | null>(null);
  const [showTokenOpen, setShowTokenOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  // Revoke confirmation
  const [revokeTarget, setRevokeTarget] = useState<PATResponse | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: queryKeys.auth.pats,
    queryFn: () => authApi.listPATs(),
  });

  const pats = useMemo(() => data?.pats ?? [], [data]);

  const createMutation = useMutation({
    mutationFn: () =>
      authApi.createPAT({
        username: user?.username ?? "",
        name: tokenName.trim(),
        email: user?.email,
      }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.pats });
      setCreateOpen(false);
      setTokenName("");
      if (res.token) {
        setCreatedToken(res.token);
        setShowTokenOpen(true);
      }
      toast.success("Token created");
    },
    onError: () => toast.error("Failed to create token"),
  });

  const revokeMutation = useMutation({
    mutationFn: (patId: string) => authApi.revokePAT(patId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.auth.pats });
      setRevokeTarget(null);
      toast.success("Token revoked");
    },
    onError: () => toast.error("Failed to revoke token"),
  });

  function handleCopy() {
    if (!createdToken) return;
    navigator.clipboard.writeText(createdToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Personal Access Tokens</h1>
          <p className="text-sm text-muted-foreground">
            Create and manage personal access tokens for API authentication.
          </p>
        </div>
        <Dialog open={createOpen} onOpenChange={setCreateOpen}>
          <DialogTrigger render={<Button />}>
            <Plus data-icon="inline-start" />
            Create Token
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Personal Access Token</DialogTitle>
              <DialogDescription>
                Give your token a descriptive name so you can identify it later.
              </DialogDescription>
            </DialogHeader>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                createMutation.mutate();
              }}
              className="space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="pat-name">Token Name</Label>
                <Input
                  id="pat-name"
                  placeholder="e.g. CI/CD Pipeline"
                  value={tokenName}
                  onChange={(e) => setTokenName(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="pat-user">Username</Label>
                <Input
                  id="pat-user"
                  value={user?.username ?? ""}
                  disabled
                  className="text-muted-foreground"
                />
              </div>
              <DialogFooter>
                <DialogClose render={<Button variant="outline" />}>
                  Cancel
                </DialogClose>
                <Button
                  type="submit"
                  disabled={!tokenName.trim() || createMutation.isPending}
                >
                  {createMutation.isPending ? "Creating…" : "Create Token"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {/* Show-once token dialog */}
      <Dialog
        open={showTokenOpen}
        onOpenChange={(open) => {
          setShowTokenOpen(open);
          if (!open) setCreatedToken(null);
        }}
      >
        <DialogContent showCloseButton={false}>
          <DialogHeader>
            <DialogTitle>Token Created</DialogTitle>
            <DialogDescription>
              Copy your personal access token now. This token will not be shown
              again.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-2">
            <code className="flex-1 break-all text-xs font-mono">
              {createdToken}
            </code>
            <Button variant="ghost" size="icon-sm" onClick={handleCopy}>
              {copied ? (
                <Check className="size-3.5 text-green-600" />
              ) : (
                <Copy className="size-3.5" />
              )}
            </Button>
          </div>
          <p className="text-xs text-destructive font-medium">
            ⚠ Make sure to copy your token — you won&apos;t be able to see it
            again!
          </p>
          <DialogFooter>
            <Button
              onClick={() => {
                setShowTokenOpen(false);
                setCreatedToken(null);
              }}
            >
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke confirmation */}
      <AlertDialog
        open={!!revokeTarget}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke Token</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to revoke &ldquo;{revokeTarget?.name}
              &rdquo;? This action cannot be undone. Any applications using this
              token will lose access.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={revokeMutation.isPending}
              onClick={() => {
                if (revokeTarget) revokeMutation.mutate(revokeTarget.pat_id);
              }}
            >
              {revokeMutation.isPending ? "Revoking…" : "Revoke"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded-lg" />
          ))}
        </div>
      ) : pats.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-20 text-center">
          <Key className="size-12 text-muted-foreground/40" />
          <p className="text-lg font-medium text-muted-foreground">
            No personal access tokens
          </p>
          <p className="text-sm text-muted-foreground/80">
            Create a token to authenticate with the API.
          </p>
          <Button className="mt-2" onClick={() => setCreateOpen(true)}>
            <Plus data-icon="inline-start" />
            Create Token
          </Button>
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Username</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pats.map((pat) => {
                const isRevoked = !!pat.revoked_at;
                return (
                  <TableRow key={pat.pat_id}>
                    <TableCell>
                      <span
                        className={cn(
                          "font-medium",
                          isRevoked && "line-through text-muted-foreground",
                        )}
                      >
                        {pat.name}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span
                        className={cn(
                          "text-sm",
                          isRevoked && "line-through text-muted-foreground",
                        )}
                      >
                        {pat.username}
                      </span>
                    </TableCell>
                    <TableCell>
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger
                            render={<span />}
                            className={cn(
                              "cursor-default text-sm",
                              isRevoked && "text-muted-foreground",
                            )}
                          >
                            {relativeTime(pat.created_at)}
                          </TooltipTrigger>
                          <TooltipContent>
                            {new Date(pat.created_at).toLocaleString()}
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    </TableCell>
                    <TableCell>
                      {isRevoked ? (
                        <Badge variant="secondary" className="text-muted-foreground">
                          Revoked
                        </Badge>
                      ) : (
                        <Badge className="bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300">
                          Active
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {!isRevoked && (
                        <Button
                          variant="destructive"
                          size="xs"
                          onClick={() => setRevokeTarget(pat)}
                        >
                          <Trash2 data-icon="inline-start" />
                          Revoke
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
