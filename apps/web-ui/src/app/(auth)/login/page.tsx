"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { authApi, ApiError } from "@/lib/api-client";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";

export default function LoginPage() {
  const router = useRouter();
  const login = useAuthStore((s) => s.login);

  const [jwtToken, setJwtToken] = useState("");
  const [patToken, setPatToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function buildUser(principal: Awaited<ReturnType<typeof authApi.validateToken>>) {
    return {
      username: principal.username,
      subject: principal.subject,
      tokenType: principal.token_type,
      claims: principal.claims,
      email: principal.email,
      roles: principal.roles,
    };
  }

  async function handleJwtLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const principal = await authApi.validateToken({ token: jwtToken });
      login(jwtToken, buildUser(principal));
      router.replace("/");
    } catch (err) {
      if (err instanceof ApiError && typeof err.detail === "object" && err.detail) {
        const detail = (err.detail as { detail?: string }).detail;
        setError(detail ?? err.message);
      } else {
        setError(err instanceof Error ? err.message : "Login failed");
      }
    } finally {
      setLoading(false);
    }
  }

  async function handlePatLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const principal = await authApi.validateToken({ token: patToken });
      login(patToken, buildUser(principal));
      router.replace("/");
    } catch (err) {
      if (err instanceof ApiError && typeof err.detail === "object" && err.detail) {
        const detail = (err.detail as { detail?: string }).detail;
        setError(detail ?? err.message);
      } else {
        setError(err instanceof Error ? err.message : "Login failed");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-md px-4">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight">service2mcp</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Enterprise API-to-MCP Tool Compilation Platform
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>
            Validate a JWT or PAT against the access-control service.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="jwt" onValueChange={() => setError(null)}>
            <TabsList className="mb-4 w-full">
              <TabsTrigger value="jwt" className="flex-1">
                JWT Token
              </TabsTrigger>
              <TabsTrigger value="pat" className="flex-1">
                PAT Token
              </TabsTrigger>
            </TabsList>

            <TabsContent value="jwt">
              <form onSubmit={handleJwtLogin} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="jwt-token">JWT Token</Label>
                  <Input
                    id="jwt-token"
                    type="password"
                    placeholder="Paste a signed JWT"
                    value={jwtToken}
                    onChange={(e) => setJwtToken(e.target.value)}
                    required
                    autoComplete="off"
                  />
                </div>
                {error && (
                  <p className="text-sm text-destructive">{error}</p>
                )}
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? "Validating…" : "Sign in with JWT"}
                </Button>
              </form>
            </TabsContent>

            <TabsContent value="pat">
              <form onSubmit={handlePatLogin} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="pat">Personal Access Token</Label>
                  <Input
                    id="pat"
                    type="password"
                    placeholder="Paste your PAT token"
                    value={patToken}
                    onChange={(e) => setPatToken(e.target.value)}
                    required
                    autoComplete="off"
                  />
                </div>
                {error && (
                  <p className="text-sm text-destructive">{error}</p>
                )}
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? "Validating…" : "Sign in with PAT"}
                </Button>
              </form>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  );
}
