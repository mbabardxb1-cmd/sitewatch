"use client";

import { Check } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { ReportsTable } from "@/components/ReportsTable";
import { Button, Card, ErrorNote, Field, inputClass } from "@/components/ui";
import { api, fmtDay, type MaintenanceRow, type ReportRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const CATEGORIES = ["update", "fix", "security", "content", "performance", "backup", "other"];

/** The standard monthly AMC tasks, worded as they appear in the client report. */
export const AMC_TASKS = [
  { key: "updates", title: "Plugin and theme updates", details: "All available plugins and themes updated to their latest versions", category: "update" },
  { key: "cleanup", title: "Cleanup and optimization", details: "Unused and unnecessary plugins and themes removed to keep the website clean and optimized", category: "performance" },
  { key: "forms", title: "Form testing", details: "All website forms checked and confirmed to be working properly", category: "other" },
  { key: "backup", title: "Full website backup", details: "A fresh full backup created, then downloaded and saved for future recovery if needed", category: "backup" },
];

type Row = { key: string; title: string; details: string; category: string; done?: MaintenanceRow };

export function WebsiteReportsCard({ websiteId }: { websiteId: string }) {
  const { can } = useAuth();
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [work, setWork] = useState<MaintenanceRow[]>([]);
  const today = new Date().toISOString().slice(0, 10);
  const [form, setForm] = useState({ performed_at: today, title: "", description: "", category: "update", visible_to_client: true });
  const [error, setError] = useState<string | null>(null);
  const canLog = can("maintenance.write");
  const [adding, setAdding] = useState(false);
  const month = today.slice(0, 7);
  const monthLabel = new Date(`${month}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const thisMonth = work.filter((m) => m.performed_at.slice(0, 7) === month);
  const earlier = work.filter((m) => m.performed_at.slice(0, 7) !== month);
  const norm = (x: string) => x.trim().toLowerCase();
  const rows: Row[] = [
    ...AMC_TASKS.map((t) => ({ ...t, done: thisMonth.find((m) => norm(m.title) === norm(t.title)) })),
    ...thisMonth.filter((m) => !AMC_TASKS.some((t) => norm(t.title) === norm(m.title)))
      .map((m) => ({ key: m.id, title: m.title, details: m.description ?? m.category, category: m.category, done: m })),
  ];

  async function complete(r: Row) {
    setError(null);
    try {
      await api(`/websites/${websiteId}/maintenance`, { method: "POST", body: {
        performed_at: today, title: r.title, description: r.details, category: r.category, visible_to_client: true,
      } });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function remove(id: string) {
    setError(null);
    await api(`/websites/${websiteId}/maintenance/${id}`, { method: "DELETE" }).catch((e) => setError(e.message));
    load();
  }

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
      setAdding(false);
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
        <Card title={`AMC maintenance · ${monthLabel}`}
              description="Work the team performed this month. It appears as the task table in the monthly report.">
          <div className="space-y-5">
            <ErrorNote message={error} />
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-black text-white">
                  <tr>
                    <th className="w-12 px-4 py-3 font-semibold">#</th><th className="px-4 py-3 font-semibold">Task</th>
                    <th className="px-4 py-3 font-semibold">Details</th><th className="w-44 px-4 py-3 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-200">
                  {rows.map((r, i) => (
                    <tr key={r.key} className="align-middle">
                      <td className="px-4 py-3 tabular-nums text-slate-700">{i + 1}</td>
                      <td className="px-4 py-3 font-semibold text-slate-900">{r.title}</td>
                      <td className="px-4 py-3 text-slate-700">
                        {r.details}
                        {r.done && (
                          <div className="mt-0.5 text-xs text-slate-400">
                            {fmtDay(r.done.performed_at)}{r.done.performed_by_email ? ` · ${r.done.performed_by_email}` : ""}
                            {!r.done.visible_to_client && " · internal only"}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {r.done ? (
                          <div className="flex items-center gap-3">
                            <span className="font-semibold text-green-700">Completed</span>
                            {canLog && (
                              <button className="text-xs text-slate-400 hover:text-red-600" title="Undo"
                                      onClick={() => remove(r.done!.id)}>Undo</button>
                            )}
                          </div>
                        ) : canLog ? (
                          <Button size="sm" variant="secondary" onClick={() => complete(r)}><Check size={14} />Mark completed</Button>
                        ) : <span className="text-slate-400">Not yet</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-500">
              Only completed tasks are listed in the client report, each marked “Completed”.
            </p>

            {canLog && (
              <details className="rounded-lg border border-slate-200 p-4" open={adding} onToggle={(e) => setAdding((e.target as HTMLDetailsElement).open)}>
                <summary className="cursor-pointer text-sm font-medium text-brand-600">Add other work</summary>
                <form onSubmit={add} className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
                  <Field label="Date"><input className={inputClass} type="date" max={today} required value={form.performed_at}
                                             onChange={(e) => setForm({ ...form, performed_at: e.target.value })} /></Field>
                  <div className="lg:col-span-2"><Field label="Task"><input className={inputClass} required maxLength={300} value={form.title}
                                             placeholder="e.g. Security patch" onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field></div>
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
                  <div className="sm:col-span-2 lg:col-span-6"><Field label="Details">
                    <input className={inputClass} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field></div>
                </form>
              </details>
            )}

            {earlier.length > 0 && (
              <details>
                <summary className="cursor-pointer text-sm font-medium text-slate-600">Earlier months ({earlier.length} entries)</summary>
                <ul className="mt-2 divide-y divide-slate-100 text-sm">
                  {earlier.map((m) => (
                    <li key={m.id} className="flex flex-wrap items-start justify-between gap-2 py-2">
                      <div>
                        <span className="tabular-nums text-slate-500">{fmtDay(m.performed_at)}</span> · <span className="font-medium">{m.title}</span>
                        <span className="ml-2 text-xs text-slate-500">{m.category}{m.visible_to_client ? "" : " · internal"}</span>
                        {m.description && <p className="text-xs text-slate-600">{m.description}</p>}
                      </div>
                      {canLog && <button className="text-xs text-red-700 underline" onClick={() => remove(m.id)}>Delete</button>}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </Card>
      )}
    </>
  );
}
