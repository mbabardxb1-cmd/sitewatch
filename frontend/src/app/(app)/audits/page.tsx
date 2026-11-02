"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RunStatus, SeverityCounts } from "@/components/AuditBits";
import { Button, Card, ErrorNote, inputClass } from "@/components/ui";
import { api, fmtDate, type AuditRun, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AuditsPage() {
  const { can } = useAuth();
  const [runs, setRuns] = useState<AuditRun[]>([]);
  const [sites, setSites] = useState<Website[]>([]);
  const [siteId, setSiteId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api<AuditRun[]>("/audits?limit=100").then(setRuns).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
    api<Website[]>("/websites?status=active").then(setSites).catch(() => undefined);
  }, [load]);
  useEffect(() => {
    if (!runs.some((r) => r.status === "queued" || r.status === "running")) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [runs, load]);

  async function startAudit() {
    setError(null);
    setNotice(null);
    try {
      await api(`/websites/${siteId}/audits`, { method: "POST" });
      setNotice("Audit queued. It runs in the background; this list refreshes automatically.");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Technical Audits</h1>
      <p className="text-sm text-slate-600">
        Each website is crawled automatically on its monthly audit day. Every audit lists which checks were
        performed, partly performed or not performed, and why.
      </p>
      <ErrorNote message={error} />
      {notice && <p className="rounded-md bg-sky-50 px-3 py-2 text-sm text-sky-900">{notice}</p>}
      {can("audit.run") && (
        <Card title="Run an audit now">
          <div className="flex flex-wrap items-center gap-3">
            <select className={`${inputClass} max-w-sm`} value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Website">
              <option value="">Select a website…</option>
              {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <Button disabled={!siteId} onClick={startAudit}>Start audit</Button>
          </div>
        </Card>
      )}
      <Card title="Audit history">
        {runs.length === 0 ? <p className="text-sm text-slate-500">No audits have run yet.</p> : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Type</th><th className="pr-4">Status</th>
                  <th className="hidden pr-4 text-right sm:table-cell">Pages</th><th className="pr-4">Issues found</th>
                  <th className="hidden pr-4 md:table-cell">New / resolved</th><th className="hidden pr-4 md:table-cell">Finished</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {runs.map((r) => (
                  <tr key={r.id} className="align-top">
                    <td className="py-2 pr-4"><Link href={`/audits/${r.id}`} className="font-medium hover:underline">{r.website_name}</Link></td>
                    <td className="py-2 pr-4">{r.trigger === "scheduled" ? `Monthly ${r.period}` : "Manual"}</td>
                    <td className="py-2 pr-4"><RunStatus run={r} /></td>
                    <td className="hidden py-2 pr-4 text-right tabular-nums sm:table-cell">{r.pages_crawled || "—"}</td>
                    <td className="py-2 pr-4">{r.status === "completed" || r.status === "partial" ? <SeverityCounts run={r} /> : "—"}</td>
                    <td className="hidden py-2 pr-4 tabular-nums md:table-cell">
                      {r.summary.counts ? `+${r.summary.counts.new + r.summary.counts.recurring} / −${r.summary.counts.resolved}` : "—"}
                    </td>
                    <td className="hidden py-2 pr-4 md:table-cell">{r.finished_at ? fmtDate(r.finished_at) : "—"}</td>
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
