"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RunStatus, SeverityCounts } from "@/components/AuditBits";
import { Button, Card, ErrorNote, Field, SeverityBadge, inputClass } from "@/components/ui";
import { api, fmtDate, SEVERITIES, type AuditRun, type IssueRow, type Severity } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function WebsiteAuditCard({ websiteId, auditDay, archived }: { websiteId: string; auditDay?: number; archived: boolean }) {
  const { can } = useAuth();
  const staff = can("audit.read_detailed");
  const [runs, setRuns] = useState<AuditRun[]>([]);
  const [issues, setIssues] = useState<IssueRow[]>([]);
  const [important, setImportant] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    api<IssueRow[]>(`/issues?website_id=${websiteId}&state=open`).then(setIssues).catch(() => undefined);
    if (staff) {
      api<AuditRun[]>(`/audits?website_id=${websiteId}&limit=5`).then(setRuns).catch((e) => setError(e.message));
      api<string[]>(`/websites/${websiteId}/important-urls`).then((u) => setImportant(u.join("\n"))).catch(() => undefined);
    }
  }, [websiteId, staff]);
  useEffect(load, [load]);

  const counts = SEVERITIES.reduce((acc, s) => ({ ...acc, [s]: issues.filter((i) => i.severity === s).length }), {} as Record<Severity, number>);

  async function start() {
    setError(null);
    try {
      await api(`/websites/${websiteId}/audits`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function saveImportant(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      const urls = await api<string[]>(`/websites/${websiteId}/important-urls`, {
        method: "PUT", body: { urls: important.split("\n").map((s) => s.trim()).filter(Boolean) },
      });
      setImportant(urls.join("\n"));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <Card title="Technical audit" actions={staff && can("audit.run") && !archived ? <Button variant="secondary" onClick={start}>Run audit now</Button> : undefined}>
      <ErrorNote message={error} />
      <div className="space-y-4">
        <div>
          <h3 className="mb-1 text-sm font-medium">Open issues</h3>
          {issues.length === 0 ? <p className="text-sm text-slate-500">No open issues from audits.</p> : (
            <div className="flex flex-wrap items-center gap-3 text-sm">
              {SEVERITIES.filter((s) => counts[s]).map((s) => <span key={s} className="inline-flex items-center gap-1"><SeverityBadge severity={s} />{counts[s]}</span>)}
              <Link href="/issues" className="text-slate-600 underline-offset-2 hover:underline">View issues</Link>
            </div>
          )}
        </div>
        {staff && (
          <>
            <div>
              <h3 className="mb-1 text-sm font-medium">Recent audits {auditDay && <span className="font-normal text-slate-500">· scheduled on day {auditDay} of each month</span>}</h3>
              {runs.length === 0 ? <p className="text-sm text-slate-500">No audits yet.</p> : (
                <ul className="divide-y divide-slate-100 text-sm">
                  {runs.map((r) => (
                    <li key={r.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-1.5">
                      <Link href={`/audits/${r.id}`} className="font-medium hover:underline">{fmtDate(r.finished_at ?? r.created_at)}</Link>
                      <span>{r.trigger === "scheduled" ? "Monthly" : "Manual"}</span>
                      <RunStatus run={r} />
                      {(r.status === "completed" || r.status === "partial") && <SeverityCounts run={r} />}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {can("website.crawl_config") && (
              <form onSubmit={saveImportant} className="space-y-2">
                <Field label="Important pages" hint="One per line (e.g. /pricing). Audits always check these and raise a high-severity issue if one becomes unavailable. The homepage is tracked automatically after the first audit.">
                  <textarea className={`${inputClass} font-mono`} rows={3} value={important} onChange={(e) => setImportant(e.target.value)} />
                </Field>
                <div className="flex items-center gap-3"><Button type="submit" variant="secondary">Save important pages</Button>{saved && <span className="text-sm text-emerald-700">Saved</span>}</div>
              </form>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
