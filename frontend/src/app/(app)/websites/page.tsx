"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, ErrorNote, HealthCell, inputClass } from "@/components/ui";
import { api, fmtDate, fmtInterval, isStaffView, type ClientOrg, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function WebsitesPage() {
  const { can } = useAuth();
  const [sites, setSites] = useState<Website[]>([]);
  const [clients, setClients] = useState<ClientOrg[]>([]);
  const [filters, setFilters] = useState({ q: "", status: "all", client_id: "" });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (can("client.read")) api<ClientOrg[]>("/clients").then(setClients).catch(() => undefined);
  }, [can]);

  useEffect(() => {
    const p = new URLSearchParams({ status: filters.status });
    if (filters.q) p.set("q", filters.q);
    if (filters.client_id) p.set("client_id", filters.client_id);
    const t = setTimeout(() => {
      api<Website[]>(`/websites?${p}`).then(setSites).catch((e) => setError(e.message));
    }, 200);
    return () => clearTimeout(t);
  }, [filters]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Websites</h1>
        {can("website.write") && (
          <Link href="/websites/new" className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white">
            Add website
          </Link>
        )}
      </div>
      <ErrorNote message={error} />
      <div className="grid gap-3 sm:grid-cols-3">
        <input className={inputClass} placeholder="Search name or URL" value={filters.q}
               onChange={(e) => setFilters({ ...filters, q: e.target.value })} />
        <select className={inputClass} value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="paused">Paused</option>
          {can("website.write") && <option value="archived">Archived</option>}
        </select>
        {clients.length > 0 && (
          <select className={inputClass} value={filters.client_id}
                  onChange={(e) => setFilters({ ...filters, client_id: e.target.value })}>
            <option value="">All clients</option>
            {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
      </div>
      <Card title={`${sites.length} website${sites.length === 1 ? "" : "s"}`}>
        {sites.length === 0 ? (
          <p className="text-sm text-slate-500">No websites match.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="hidden pr-4 sm:table-cell">Client</th><th className="pr-4">Health</th>
                  <th className="hidden pr-4 md:table-cell">Monitoring</th><th className="hidden pr-4 md:table-cell">Last check</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sites.map((w) => (
                  <tr key={w.id} className="align-top">
                    <td className="py-2 pr-4">
                      <Link href={`/websites/${w.id}`} className="font-medium hover:underline">{w.name}</Link>
                      <div className="break-all text-xs text-slate-500">{w.base_url}</div>
                      <div className="text-xs text-slate-500 sm:hidden">{w.client_name}</div>
                    </td>
                    <td className="hidden py-2 pr-4 sm:table-cell">{w.client_name}</td>
                    <td className="py-2 pr-4"><HealthCell status={w.health} reason={w.health_reason} /></td>
                    <td className="hidden py-2 pr-4 capitalize md:table-cell">
                      {w.status}
                      {isStaffView(w) && w.status === "active" && (
                        <div className="text-xs normal-case text-slate-500">{fmtInterval(w.check_interval_s)}</div>
                      )}
                    </td>
                    <td className="hidden py-2 pr-4 text-slate-600 md:table-cell">{fmtDate(w.last_checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
