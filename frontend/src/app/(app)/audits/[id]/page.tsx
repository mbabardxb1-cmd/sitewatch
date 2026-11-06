"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { RunStatus } from "@/components/AuditBits";
import { AUDIT_CATEGORIES, CATEGORY_LABEL } from "@/components/AuditLauncher";
import { AuditProgressCard } from "@/components/AuditProgress";
import { Card, CertaintyLabel, CoverageBadge, ErrorNote, Notice, SeverityBadge, Stat, inputClass } from "@/components/ui";
import {
  ApiError, api, fmtDate, SEVERITIES,
  type AuditCompare, type AuditDetail, type AuditFinding, type CompareIssue,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

function IssueList({ title, items, empty }: { title: string; items: CompareIssue[]; empty: string }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold">{title} ({items.length})</h3>
      {items.length === 0 ? <p className="text-sm text-slate-500">{empty}</p> : (
        <ul className="max-h-60 space-y-1 overflow-auto text-sm">
          {items.map((i) => (
            <li key={i.id} className="flex items-start gap-2">
              <SeverityBadge severity={i.severity} /><span className="break-all">{i.title}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function AuditDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const [run, setRun] = useState<AuditDetail | null>(null);
  const [cmp, setCmp] = useState<AuditCompare | null>(null);
  const [findings, setFindings] = useState<AuditFinding[]>([]);
  const [filters, setFilters] = useState({ severity: "", check: "", certainty: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () => api<AuditDetail>(`/audits/${id}`).then((r) => {
      setRun(r);
      if (r.status === "completed" || r.status === "partial") {
        api<AuditCompare>(`/audits/${id}/compare`).then(setCmp).catch(() => undefined);
        api<AuditFinding[]>(`/audits/${id}/findings?limit=5000`).then(setFindings).catch(() => undefined);
      }
      return r;
    }).catch((e) => setError(e instanceof ApiError && e.status === 404 ? "Audit not found." : e.message));
    load();
    const t = setInterval(() => {
      load().then((r) => { if (r && !(r.status === "queued" || r.status === "running")) clearInterval(t); });
    }, 3000);
    return () => clearInterval(t);
  }, [id]);

  const checks = useMemo(() => [...new Set(findings.map((f) => f.check_key))], [findings]);
  const shown = findings.filter((f) => (!filters.severity || f.severity === filters.severity)
    && (!filters.check || f.check_key === filters.check) && (!filters.certainty || f.certainty === filters.certainty));

  if (error) return <ErrorNote message={error} />;
  if (!run) return <p className="text-sm text-slate-500">Loading…</p>;
  const done = run.status === "completed" || run.status === "partial";
  const s = run.summary;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/audits" className="text-sm font-medium text-brand-600 hover:text-brand-500">← Technical Audits</Link>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{run.website_name}: {run.trigger === "scheduled" ? `monthly audit ${run.period}` : "manual audit"}</h1>
          <p className="text-sm text-slate-600">
            <RunStatus run={run} /> · started {fmtDate(run.started_at, "—")} · finished {fmtDate(run.finished_at, "—")}
            {s.duration_s !== undefined && ` · ${s.duration_s}s, ${s.requests} requests`}
          </p>
          <Link href={`/websites/${run.website_id}`} className="text-sm text-slate-500 hover:underline">Website settings</Link>
        </div>
        {done && can("audit.export") && (
          <div className="flex gap-2">
            <a className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50" href={`/api/v1/audits/${id}/export?format=csv`}>Export CSV</a>
            <a className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50" href={`/api/v1/audits/${id}/export?format=json`}>Export JSON</a>
          </div>
        )}
      </div>

      {run.status === "failed" && <ErrorNote message={`The audit failed: ${run.error}`} />}
      {(run.status === "queued" || run.status === "running") && <AuditProgressCard run={run} />}
      {run.categories && (
        <Notice>This audit covered only: <strong>{run.categories.map((c) => CATEGORY_LABEL[c] ?? c).join(", ")}</strong>. Other checks were skipped and none of their issues were changed.</Notice>
      )}
      {run.status === "partial" && (
        <Notice tone="warning">
          Partial audit: some checks were limited or could not run. See “Check coverage” for exactly what was and wasn’t checked.
        </Notice>
      )}

      {done && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Pages crawled" value={run.pages_crawled} note={s.stopped_reason === "max_pages" ? "Stopped at page limit" : undefined} />
            <Stat label="Broken internal links" value={s.broken_internal_links ?? 0} />
            <Stat label="Broken external links" value={s.broken_external_links ?? 0} note={`${s.external_links_checked ?? 0} external links checked`} />
            <Stat label="Could not check" value={s.by_certainty?.could_not_check ?? 0} note="Items needing manual review" />
          </div>

          {cmp && (
            <Card title={cmp.previous_run_id ? `Compared with previous audit (${fmtDate(cmp.previous_finished_at, "—")})` : "First audit for this website"}>
              <div className="mb-4 overflow-x-auto">
                <table className="text-sm tabular-nums">
                  <thead className="text-xs uppercase text-slate-500"><tr><th className="pr-6 text-left">Severity</th><th className="pr-6 text-right">This audit</th>{cmp.previous_run_id && <th className="text-right">Previous</th>}</tr></thead>
                  <tbody>
                    {SEVERITIES.map((k) => (
                      <tr key={k}><td className="py-0.5 pr-6"><SeverityBadge severity={k} /></td>
                        <td className="pr-6 text-right">{cmp.by_severity[k].current}</td>
                        {cmp.previous_run_id && <td className="text-right">{cmp.by_severity[k].previous}</td>}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="grid gap-6 md:grid-cols-3">
                <IssueList title="New" items={cmp.new} empty="No new issues." />
                <IssueList title="Recurring (were resolved, found again)" items={cmp.recurring} empty="None." />
                <IssueList title="Resolved — verified by this audit" items={cmp.resolved} empty="None verified as resolved." />
              </div>
              {cmp.persisting_count > 0 && <p className="mt-3 text-sm text-slate-600">{cmp.persisting_count} issue(s) persist from earlier audits.</p>}
            </Card>
          )}

          <Card title="Check coverage" flush>
            <div className="overflow-x-auto">
              <table className="sw-table">
                <thead><tr><th>Check</th><th>Status</th><th>Notes</th></tr></thead>
                {AUDIT_CATEGORIES.map((cat) => {
                  const rows = run.coverage.filter((c) => c.category === cat.key);
                  if (!rows.length) return null;
                  const Icon = cat.icon;
                  return (
                    <tbody key={cat.key} className="divide-y divide-slate-100">
                      <tr className="bg-slate-50/60 hover:bg-slate-50/60">
                        <td colSpan={3} className="!py-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                          <span className="inline-flex items-center gap-2"><Icon size={14} />{cat.label}</span>
                        </td>
                      </tr>
                      {rows.map((c) => (
                        <tr key={c.check_key} className="align-top">
                          <td>{c.label}</td>
                          <td><CoverageBadge status={c.status} /></td>
                          <td className="text-slate-600">{c.reason ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  );
                })}
              </table>
            </div>
          </Card>

          <Card title={`Findings (${shown.length}${shown.length !== findings.length ? ` of ${findings.length}` : ""})`}>
            <div className="mb-3 grid gap-2 sm:grid-cols-3">
              <select className={inputClass} aria-label="Severity" value={filters.severity} onChange={(e) => setFilters({ ...filters, severity: e.target.value })}>
                <option value="">All severities</option>{SEVERITIES.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              <select className={inputClass} aria-label="Check" value={filters.check} onChange={(e) => setFilters({ ...filters, check: e.target.value })}>
                <option value="">All checks</option>
                {checks.map((k) => <option key={k} value={k}>{findings.find((f) => f.check_key === k)?.check_label}</option>)}
              </select>
              <select className={inputClass} aria-label="Certainty" value={filters.certainty} onChange={(e) => setFilters({ ...filters, certainty: e.target.value })}>
                <option value="">Any certainty</option><option value="confirmed">Confirmed</option>
                <option value="warning">Warning</option><option value="could_not_check">Could not check</option>
              </select>
            </div>
            {shown.length === 0 ? <p className="text-sm text-slate-500">No findings{findings.length ? " match the filters" : ""}.</p> : (
              <div className="overflow-x-auto">
                <table className="sw-table">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr><th className="py-2 pr-4">Severity</th><th className="pr-4">Finding</th><th className="hidden pr-4 md:table-cell">Check</th><th className="pr-4">Certainty</th></tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {shown.map((f) => (
                      <tr key={f.id} className="cursor-pointer align-top hover:bg-slate-50" onClick={() => setOpen(open === f.id ? null : f.id)}>
                        <td className="py-2 pr-4"><SeverityBadge severity={f.severity} /></td>
                        <td className="py-2 pr-4">
                          <div className="break-all">{f.title}</div>
                          {f.source_url && <div className="break-all text-xs text-slate-500">Found on {f.source_url}</div>}
                          {open === f.id && (
                            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-2 text-xs text-slate-700">
                              {JSON.stringify(f.detail, null, 2)}
                            </pre>
                          )}
                        </td>
                        <td className="hidden py-2 pr-4 text-slate-600 md:table-cell">{f.check_label}</td>
                        <td className="py-2 pr-4"><CertaintyLabel c={f.certainty} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
