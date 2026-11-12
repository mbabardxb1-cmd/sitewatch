"use client";

import { Settings } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { BrandingCard } from "@/components/BrandingCard";
import { MalwareSettingsCard } from "@/components/MalwareSettingsCard";
import { Button, Card, ErrorNote, Field, inputClass, PageHeader } from "@/components/ui";
import { api, fmtDate } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Values = { report_generation_day: number; uptime_check_retention_days: number; audit_run_retention_months: number; notification_log_retention_days: number };
type SystemSettings = { values: Values; limits: Record<keyof Values, { min: number; max: number; default: number }> };
type LogRow = { id: string; at: string; actor_email: string | null; action: string; target_type: string | null; target_id: string | null; ip: string | null; detail: Record<string, unknown> };

const FIELDS: { key: keyof Values; label: string; hint: string }[] = [
  { key: "report_generation_day", label: "Monthly report day", hint: "Day of the month on which last month's reports are generated." },
  { key: "uptime_check_retention_days", label: "Keep raw uptime checks (days)", hint: "Older checks are summarised into hourly figures, which are kept." },
  { key: "audit_run_retention_months", label: "Keep audit runs (months)", hint: "Each website's latest audit is always kept." },
  { key: "notification_log_retention_days", label: "Keep notification logs (days)", hint: "Delivery history shown on the Notifications page." },
];

export default function SettingsPage() {
  const { can } = useAuth();
  const [sys, setSys] = useState<SystemSettings | null>(null);
  const [form, setForm] = useState<Values | null>(null);
  const [log, setLog] = useState<LogRow[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api<SystemSettings>("/settings/system").then((s) => { setSys(s); setForm(s.values); }).catch((e) => setError(e.message));
  }, []);
  const loadLog = useCallback(() => {
    api<LogRow[]>(`/settings/audit-log?limit=200${filter ? `&action=${encodeURIComponent(filter)}` : ""}`).then(setLog).catch(() => undefined);
  }, [filter]);
  useEffect(loadLog, [loadLog]);

  if (!can("settings.manage")) return <ErrorNote message="You do not have access to settings." />;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      const s = await api<SystemSettings>("/settings/system", { method: "PUT", body: form });
      setSys(s);
      setForm(s.values);
      setSaved(true);
      loadLog();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" icon={<Settings size={20} />} description="Schedules, data retention, report branding and integrations." />
      <ErrorNote message={error} />
      {sys && form && (
        <Card title="Schedules and data retention">
          <form onSubmit={save} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {FIELDS.map((f) => {
                const lim = sys.limits[f.key];
                return (
                  <Field key={f.key} label={f.label} hint={`${f.hint} Allowed ${lim.min}–${lim.max}; default ${lim.default}.`}>
                    <input className={inputClass} type="number" min={lim.min} max={lim.max} required value={form[f.key]}
                           onChange={(e) => setForm({ ...form, [f.key]: Number(e.target.value) })} />
                  </Field>
                );
              })}
            </div>
            <p className="text-xs text-slate-500">
              Monthly reports, issues, incidents, maintenance entries and this security log are never deleted automatically.
              Uptime checks run every 1–5 minutes per website (set on each website); audits run on each website&apos;s audit day.
            </p>
            <div className="flex items-center gap-3"><Button type="submit">Save settings</Button>{saved && <span className="text-sm text-emerald-700">Saved</span>}</div>
          </form>
        </Card>
      )}

      <BrandingCard />
      <MalwareSettingsCard />

      <Card title="Security log" actions={
        <select className="rounded-lg border-0 py-1.5 pl-2.5 pr-8 text-sm shadow-sm ring-1 ring-inset ring-slate-300" aria-label="Filter actions" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All actions</option>
          {["auth", "user", "client", "website", "report", "settings", "integration", "expiry", "issue", "maintenance"].map((a) => <option key={a} value={a}>{a}</option>)}
        </select>}>
        <p className="mb-3 text-xs text-slate-500">Sign-ins, lockouts and every administrative change, with who made it and from which address.</p>
        {log.length === 0 ? <p className="text-sm text-slate-500">No entries.</p> : (
          <div className="max-h-[32rem] overflow-auto">
            <table className="sw-table">
              <thead className="sticky top-0 bg-white text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Time</th><th className="pr-4">Who</th><th className="pr-4">Action</th>
                  <th className="hidden pr-4 md:table-cell">Details</th><th className="hidden pr-4 lg:table-cell">IP</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {log.map((l) => (
                  <tr key={l.id} className="align-top">
                    <td className="py-1.5 pr-4 whitespace-nowrap">{fmtDate(l.at)}</td>
                    <td className="py-1.5 pr-4">{l.actor_email ?? "—"}</td>
                    <td className="py-1.5 pr-4 font-mono text-xs">{l.action}</td>
                    <td className="hidden py-1.5 pr-4 text-xs text-slate-600 md:table-cell">
                      {Object.keys(l.detail).length ? Object.entries(l.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ") : ""}
                    </td>
                    <td className="hidden py-1.5 pr-4 text-xs text-slate-500 lg:table-cell">{l.ip}</td>
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
