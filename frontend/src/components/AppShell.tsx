"use client";

import {
  Activity, AlertTriangle, Bell, Building2, FileText, Globe, LayoutDashboard, LogOut, Menu, Network,
  ScanSearch, Settings, ShieldCheck, Users, X, type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";

type NavItem = { href: string; label: string; perm: string; icon: LucideIcon };

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", perm: "website.read", icon: LayoutDashboard },
      { href: "/websites", label: "Websites", perm: "website.read", icon: Globe },
      { href: "/clients", label: "Clients", perm: "client.manage", icon: Building2 },
    ],
  },
  {
    section: "Monitoring",
    items: [
      { href: "/uptime", label: "Uptime", perm: "incident.read", icon: Activity },
      { href: "/audits", label: "Technical Audits", perm: "audit.read_detailed", icon: ScanSearch },
      { href: "/issues", label: "Issues", perm: "issue.read_summary", icon: AlertTriangle },
      { href: "/domains", label: "Domain & Hosting", perm: "expiry.read", icon: Network },
      { href: "/ssl", label: "SSL Certificates", perm: "expiry.read", icon: ShieldCheck },
    ],
  },
  {
    section: "Reporting",
    items: [
      { href: "/reports", label: "Reports", perm: "report.read", icon: FileText },
      { href: "/notifications", label: "Notifications", perm: "notification.read", icon: Bell },
    ],
  },
  {
    section: "Administration",
    items: [
      { href: "/users", label: "Users & Permissions", perm: "user.manage", icon: Users },
      { href: "/settings", label: "Settings", perm: "settings.manage", icon: Settings },
    ],
  },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-400 to-brand-600 shadow-sm">
        <Activity className="text-white" size={18} strokeWidth={2.5} />
      </span>
      <span className="text-[15px] font-semibold tracking-tight text-white">SiteWatch</span>
    </div>
  );
}

const ROLE_LABEL: Record<string, string> = { super_admin: "Administrator", developer: "Developer", client: "Client" };

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, can, logout } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);

  const initials = (me?.full_name || me?.email || "?").split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((s) => s[0]?.toUpperCase()).join("");

  const sidebar = (
    <div className="flex h-full flex-col bg-slate-900">
      <div className="flex h-16 items-center justify-between px-5">
        <Logo />
        <button className="rounded-md p-1 text-slate-400 hover:text-white lg:hidden" onClick={() => setOpen(false)} aria-label="Close navigation">
          <X size={20} />
        </button>
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
        {NAV.map(({ section, items }) => {
          const visible = items.filter((n) => can(n.perm));
          if (!visible.length) return null;
          return (
            <div key={section}>
              <div className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{section}</div>
              <ul className="space-y-0.5">
                {visible.map((n) => {
                  const active = pathname.startsWith(n.href);
                  const Icon = n.icon;
                  return (
                    <li key={n.href}>
                      <Link href={n.href}
                            className={`group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                              active ? "bg-white/10 text-white" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}>
                        <Icon size={18} className={active ? "text-brand-300" : "text-slate-500 group-hover:text-slate-300"} />
                        {n.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </nav>
      <div className="border-t border-white/10 p-3">
        <div className="flex items-center gap-3 rounded-lg px-2 py-2">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-brand-500/20 text-sm font-semibold text-brand-200">
            {initials}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-white">{me?.full_name || me?.email}</div>
            <div className="truncate text-xs text-slate-400">{me?.roles.map((r) => ROLE_LABEL[r] ?? r).join(", ")}</div>
          </div>
          <button onClick={logout} className="rounded-md p-1.5 text-slate-400 hover:bg-white/5 hover:text-white" title="Sign out" aria-label="Sign out">
            <LogOut size={18} />
          </button>
        </div>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/50" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85%] shadow-xl">{sidebar}</aside>
        </div>
      )}
      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur lg:hidden">
          <button className="rounded-md p-1.5 text-slate-600 hover:bg-slate-100" onClick={() => setOpen(true)} aria-expanded={open} aria-label="Open navigation">
            <Menu size={20} />
          </button>
          <span className="font-semibold tracking-tight">SiteWatch</span>
        </header>
        <main className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
