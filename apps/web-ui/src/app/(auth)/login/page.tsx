"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
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

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [patToken, setPatToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handlePasswordLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const basicToken = btoa(`${username}:${password}`);
      const res = await fetch("/api/v1/authn/validate", {
        method: "POST",
        headers: { Authorization: `Basic ${basicToken}` },
      });

      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `Authentication failed (${res.status})`);
      }

      const data = await res.json().catch(() => ({}));
      const token = data.token ?? basicToken;
      const user = {
        username: data.username ?? username,
        email: data.email,
        roles: data.roles,
      };

      login(token, user);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  async function handlePatLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const res = await fetch("/api/v1/authn/validate", {
        method: "POST",
        headers: { Authorization: `Bearer ${patToken}` },
      });

      if (!res.ok) {
        const body = await res.text();
        throw new Error(body || `Token validation failed (${res.status})`);
      }

      const data = await res.json().catch(() => ({}));
      const user = {
        username: data.username ?? "user",
        email: data.email,
        roles: data.roles,
      };

      login(patToken, user);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-full max-w-md px-4">
      <div className="mb-8 text-center">
        <h1 className="text-3xl font-bold tracking-tight">Tool Compiler v2</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Enterprise API-to-MCP Tool Compilation Platform
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Sign in</CardTitle>
          <CardDescription>
            Choose your preferred authentication method.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="password">
            <TabsList className="mb-4 w-full">
              <TabsTrigger value="password" className="flex-1">
                Password Login
              </TabsTrigger>
              <TabsTrigger value="pat" className="flex-1">
                PAT Token
              </TabsTrigger>
            </TabsList>

            <TabsContent value="password">
              <form onSubmit={handlePasswordLogin} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="username">Username</Label>
                  <Input
                    id="username"
                    placeholder="Enter your username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    required
                    autoComplete="username"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="password">Password</Label>
                  <Input
                    id="password"
                    type="password"
                    placeholder="Enter your password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="current-password"
                  />
                </div>
                {error && (
                  <p className="text-sm text-destructive">{error}</p>
                )}
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? "Signing in…" : "Sign in"}
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
