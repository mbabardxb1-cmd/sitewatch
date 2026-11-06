"use client";

import { Activity, CheckCircle2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, ErrorNote, inputClass } from "@/components/ui";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/login", { method: "POST", body: { email, password } });
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <div className="relative hidden overflow-hidden bg-slate-900 lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top_left,rgba(99,102,241,0.35),transparent_60%)]" aria-hidden />
        <div className="relative flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-brand-400 to-brand-600">
            <Activity className="text-white" size={20} strokeWidth={2.5} />
          </span>
          <span className="text-lg font-semibold tracking-tight text-white">SiteWatch</span>
        </div>
        <div className="relative space-y-6">
          <h2 className="max-w-md text-3xl font-semibold leading-tight tracking-tight text-white">
            Uptime, audits and renewals for every client website, in one place.
          </h2>
          <ul className="space-y-3 text-sm text-slate-300">
            {["Round-the-clock uptime checks with verified outages", "Technical audits: links, SEO, security, forms",
              "Domain, hosting and SSL expiry tracking", "Monthly PDF reports for your clients"].map((t) => (
              <li key={t} className="flex items-center gap-3"><CheckCircle2 size={18} className="text-brand-300" />{t}</li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-slate-500">Website monitoring &amp; maintenance reporting</p>
      </div>
      <div className="flex items-center justify-center px-4 py-12">
        <form onSubmit={submit} className="w-full max-w-sm space-y-6">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Sign in</h1>
            <p className="mt-1 text-sm text-slate-500">Welcome back. Enter your details to continue.</p>
          </div>
          <ErrorNote message={error} />
          <div className="space-y-4">
            <label className="block space-y-1.5 text-sm">
              <span className="font-medium text-slate-700">Email</span>
              <input className={inputClass} type="email" autoComplete="username" required value={email}
                     onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label className="block space-y-1.5 text-sm">
              <span className="font-medium text-slate-700">Password</span>
              <input className={inputClass} type="password" autoComplete="current-password" required value={password}
                     onChange={(e) => setPassword(e.target.value)} />
            </label>
          </div>
          <Button type="submit" disabled={busy} className="w-full">{busy ? "Signing in…" : "Sign in"}</Button>
        </form>
      </div>
    </main>
  );
}
