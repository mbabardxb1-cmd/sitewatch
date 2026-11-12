"use client";

import { History, Plus, ScanSearch, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RunStatus, SeverityCounts } from "@/components/AuditBits";
import { AuditLauncher, auditKind, CATEGORY_LABEL } from "@/components/AuditLauncher";
import { AuditProgressCard } from "@/components/AuditProgress";
import { Button, Card, EmptyState, ErrorNote, Notice, PageHeader } from "@/components/ui";
import { api, fmtDate, type AuditRun, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const active = (r: AuditRun) => r.status === "queued" || r.status === "running";

export default function AuditsPage() {
  const { can } = useAuth();
  const [runs, setRuns] = useState<AuditRun[] | null>(null);
  const [sites, setSites] = useState<Website[]>([]);
  const [launcher, setLauncher] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api<AuditRun[]>("/audits?limit=100").then(setRuns).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
    api<Website[]>("/websites?status=active").then(setSites).catch(() => undefined);
  }, [load]);
  const running = (runs ?? []).filter(active);
  useEffect(() => {
    if (!running.length) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [running.length, load]);

  async function remove(r: AuditRun) {
    if (!confirm(`Delete this audit of ${r.website_name}?\n\nIssues that were found only by this audit are deleted too. This cannot be undone.`)) return;
    setError(null);
    try {
      await api(`/audits/${r.id}`, { method: "DELETE" });
      setNotice("Audit deleted.");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  const history = (runs ?? []).filter((r) => !active(r));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Technical Audits" icon={<ScanSearch size={20} />}
        description="Crawl a website to find broken links, SEO, security, form and accessibility problems. Every audit records exactly which checks were performed, partly performed or skipped, and why."
        actions={can("audit.run") && !launcher && <Button onClick={() => setLauncher(true)}><Plus size={16} />New audit</Button>}
      />
      <ErrorNote message={error} />
      {notice && <Notice tone="success">{notice}</Notice>}

      {can("audit.run") && launcher && (
        <Card title="Run an audit" description="Choose a website and the areas to check.">
          <AuditLauncher sites={sites} onCancel={() => setLauncher(false)} onStarted={() => {
            setLauncher(false);
            setNotice(null);
            load();
          }} />
        </Card>
      )}

      {running.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-900">In progress</h2>
          <div className="grid gap-4 lg:grid-cols-2">
            {running.map((r) => <AuditProgressCard key={r.id} run={r} onChanged={load} />)}
          </div>
        </section>
      )}

      <Card title="Audit history" flush actions={<History size={16} className="text-slate-400" />}>
        {runs === null ? <p className="p-5 text-sm text-slate-500">Loading…</p> : history.length === 0 ? (
          <EmptyState icon={<ScanSearch size={20} />} title="No audits yet">
            Start an audit to see broken links, SEO and security findings here.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="sw-table">
              <thead>
                <tr><th>Website</th><th>Audit</th><th>Status</th>
                  <th className="hidden text-right sm:table-cell">Pages</th><th>Issues found</th>
                  <th className="hidden md:table-cell">New / resolved</th><th className="hidden md:table-cell">Finished</th>
                  {can("audit.delete") && <th><span className="sr-only">Actions</span></th>}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {history.map((r) => (
                  <tr key={r.id} className="align-top">
                    <td><Link href={`/audits/${r.id}`} className="font-medium hover:text-brand-600">{r.website_name}</Link></td>
                    <td>
                      <span className="font-medium text-slate-900">{auditKind(r.categories)}</span>
                      <div className="text-xs text-slate-500">
                        {r.trigger === "scheduled" ? `Automatic, ${r.period}` : r.trigger === "auto" ? "Automatic daily scan" : "Started manually"}
                        {auditKind(r.categories) === "Custom audit" && r.categories && `: ${r.categories.map((c) => CATEGORY_LABEL[c] ?? c).join(", ")}`}
                      </div>
                      {r.status === "cancelled" && r.error && <div className="text-xs text-slate-500">{r.error}</div>}
                    </td>
                    <td><RunStatus run={r} /></td>
                    <td className="hidden text-right tabular-nums sm:table-cell">{r.pages_crawled || "—"}</td>
                    <td>{r.status === "completed" || r.status === "partial" ? <SeverityCounts run={r} /> : "—"}</td>
                    <td className="hidden tabular-nums md:table-cell">
                      {r.summary.counts ? `+${r.summary.counts.new + r.summary.counts.recurring} / −${r.summary.counts.resolved}` : "—"}
                    </td>
                    <td className="hidden whitespace-nowrap text-slate-600 md:table-cell">{r.finished_at ? fmtDate(r.finished_at) : "—"}</td>
                    {can("audit.delete") && (
                      <td className="text-right">
                        <button onClick={() => remove(r)} title="Delete audit" aria-label={`Delete audit of ${r.website_name}`}
                                className="rounded-md p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600">
                          <Trash2 size={16} />
                        </button>
                      </td>
                    )}
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
