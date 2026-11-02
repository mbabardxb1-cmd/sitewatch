"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/lib/auth";

type NavItem = { href: string; label: string; perm: string; phase?: number };

// `phase` marks sections whose backend is not built yet; they are shown as unavailable
// rather than as fake screens.
const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", perm: "website.read" },
  { href: "/websites", label: "Websites", perm: "website.read" },
  { href: "/clients", label: "Clients", perm: "client.manage" },
  { href: "/uptime", label: "Uptime Monitoring", perm: "incident.read", phase: 3 },
  { href: "/audits", label: "Technical Audits", perm: "audit.read_detailed", phase: 4 },
  { href: "/issues", label: "Issues", perm: "issue.read_summary", phase: 4 },
  { href: "/domains", label: "Domain & Hosting", perm: "expiry.read", phase: 5 },
  { href: "/ssl", label: "SSL Certificates", perm: "expiry.read", phase: 5 },
  { href: "/reports", label: "Reports", perm: "report.read", phase: 6 },
  { href: "/notifications", label: "Notifications", perm: "notification.read", phase: 3 },
  { href: "/users", label: "Users & Permissions", perm: "user.manage" },
  { href: "/settings", label: "Settings", perm: "settings.manage", phase: 8 },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, can, logout } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const items = NAV.filter((n) => can(n.perm));

  const nav = (
    <nav className="space-y-0.5 p-3">
      {items.map((n) => {
        const active = pathname.startsWith(n.href);
        if (n.phase) {
          return (
            <span key={n.href} title={`Available from phase ${n.phase}`}
                  className="flex items-center justify-between rounded-md px-3 py-2 text-sm text-slate-400">
              {n.label}
              <span className="text-[10px] uppercase tracking-wide">Soon</span>
            </span>
          );
        }
        return (
          <Link key={n.href} href={n.href} onClick={() => setOpen(false)}
                className={`block rounded-md px-3 py-2 text-sm ${active ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"}`}>
            {n.label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen lg:flex">
      <aside className="hidden w-60 shrink-0 border-r border-slate-200 bg-white lg:block">
        <div className="px-6 py-4 text-base font-semibold">SiteWatch</div>
        {nav}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
          <button className="rounded-md px-2 py-1 text-sm ring-1 ring-slate-300 lg:hidden" onClick={() => setOpen(!open)}
                  aria-expanded={open} aria-label="Toggle navigation">Menu</button>
          <span className="font-semibold lg:hidden">SiteWatch</span>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden text-slate-600 sm:inline">{me?.email}</span>
            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-700">{me?.roles.join(", ")}</span>
            <button onClick={logout} className="text-slate-600 underline-offset-2 hover:underline">Sign out</button>
          </div>
        </header>
        {open && <div className="border-b border-slate-200 bg-white lg:hidden">{nav}</div>}
        <main className="mx-auto w-full max-w-7xl flex-1 p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
