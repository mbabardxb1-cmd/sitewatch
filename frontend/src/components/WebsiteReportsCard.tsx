"use client";

import { useCallback, useEffect, useState } from "react";
import { ReportsTable } from "@/components/ReportsTable";
import { Button, Card, ErrorNote, Field, inputClass } from "@/components/ui";
import { api, fmtDay, type MaintenanceRow, type ReportRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const CATEGORIES = ["update", "fix", "security", "content", "performance", "backup", "other"];

export function WebsiteReportsCard({ websiteId }: { websiteId: string }) {
  const { can } = useAuth();
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [work, setWork] = useState<MaintenanceRow[]>([]);
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ performed_at: today, title: "", description: "", category: "update", visible_to_client: true });
  const [error, setError] = useState<string | null>(null);
  const canLog = can("maintenance.write");

  const load = useCallback(() => {
    api<ReportRow[]>(`/reports?website_id=${websiteId}&limit=12`).then(setReports).catch(() => undefined);
    if (canLog || can("report.read_all")) api<MaintenanceRow[]>(`/websites/${websiteId}/maintenance`).then(setWork).catch(() => undefined);
  }, [websiteId, canLog, can]);
  useEffect(load, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/websites/${websiteId}/maintenance`, { method: "POST", body: { ...form, description: form.description || null } });
      setForm({ ...form, title: "", description: "" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <>
      <Card title="Monthly reports">
        <ReportsTable rows={reports} showWebsite={false} />
      </Card>
      {(canLog || work.length > 0) && (
        <Card title="Maintenance log">
          <p className="mb-3 text-xs text-slate-500">
            Work the team actually performed. It appears in the “Maintenance work performed” section of reports, separately
            from monitoring results. Entries marked internal appear only in technical reports.
          </p>
          <ErrorNote message={error} />
          {canLog && (
            <form onSubmit={add} className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
              <Field label="Date"><input className={inputClass} type="date" max={today} required value={form.performed_at}
                                         onChange={(e) => setForm({ ...form, performed_at: e.target.value })} /></Field>
              <div className="lg:col-span-2"><Field label="Work done"><input className={inputClass} required maxLength={300} value={form.title}
                                         onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field></div>
              <Field label="Type">
                <select className={inputClass} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
              <label className="flex items-end gap-2 pb-2 text-sm">
                <input type="checkbox" checked={form.visible_to_client} onChange={(e) => setForm({ ...form, visible_to_client: e.target.checked })} />
                Show to client
              </label>
              <div className="flex items-end"><Button type="submit">Add</Button></div>
              <div className="sm:col-span-2 lg:col-span-6"><Field label="Details (optional)">
                <input className={inputClass} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field></div>
            </form>
          )}
          {work.length === 0 ? <p className="text-sm text-slate-500">No maintenance recorded.</p> : (
            <ul className="divide-y divide-slate-100 text-sm">
              {work.map((m) => (
                <li key={m.id} className="flex flex-wrap items-start justify-between gap-2 py-2">
                  <div>
                    <span className="tabular-nums text-slate-500">{fmtDay(m.performed_at)}</span> · <span className="font-medium">{m.title}</span>
                    <span className="ml-2 text-xs text-slate-500">{m.category}{m.visible_to_client ? "" : " · internal"}{m.performed_by_email ? ` · ${m.performed_by_email}` : ""}</span>
                    {m.description && <p className="text-xs text-slate-600">{m.description}</p>}
                  </div>
                  {canLog && <button className="text-xs text-red-700 underline" onClick={async () => {
                    await api(`/websites/${websiteId}/maintenance/${m.id}`, { method: "DELETE" }).catch((e) => setError(e.message));
                    load();
                  }}>Delete</button>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </>
  );
}
