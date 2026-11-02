"use client";

import { AppShell } from "@/components/AppShell";
import { AuthProvider, useAuth } from "@/lib/auth";

function Gate({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading || !me) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">Loading…</div>;
  }
  return <AppShell>{children}</AppShell>;
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <Gate>{children}</Gate>
    </AuthProvider>
  );
}
