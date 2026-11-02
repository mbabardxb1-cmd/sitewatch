"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, SeverityBadge, inputClass } from "@/components/ui";
import { api, fmtDate, SEVERITIES, type IssueComment, type IssueRow, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const STAFF_STATUS: Record<string, string> = {
  open: "Open", acknowledged: "Acknowledged", in_progress: "In progress",
  resolved_pending_verification: "Fixed – awaiting verification", resolved: "Resolved (verified)",
};

function Notes({ issue }: { issue: IssueRow }) {
  const { can } = useAuth();
  const [notes, setNotes] = useState<IssueComment[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api<IssueComment[]>(`/issues/${issue.id}/comments`).then(setNotes).catch((e) => setError(e.message));
  }, [issue.id]);
  useEffect(load, [load]);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api(`/issues/${issue.id}/comments`, { method: "POST", body: { body: text } });
      setText("");
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-2 rounded-md bg-slate-50 p-3">
      <ErrorNote message={error} />
      {issue.resolution_note && <p className="text-sm text-emerald-800">{issue.resolution_note}</p>}
      {issue.detail && Object.keys(issue.detail).length > 0 && (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs text-slate-600">{JSON.stringify(issue.detail, null, 2)}</pre>
      )}
      <h4 className="text-xs font-semibold uppercase text-slate-500">Internal notes (never shown to clients)</h4>
      {notes.length === 0 ? <p className="text-sm text-slate-500">No notes yet.</p> : notes.map((n) => (
        <div key={n.id} className="text-sm">
          <span className="text-xs text-slate-500">{n.author_email} · {fmtDate(n.created_at)}</span>
          <p className="whitespace-pre-wrap">{n.body}</p>
        </div>
      ))}
      {can("issue.note.write") && (
        <form onSubmit={add} className="flex gap-2">
          <input className={inputClass} placeholder="Add a remediation note…" value={text} onChange={(e) => setText(e.target.value)} required />
          <Button type="submit">Add</Button>
        </form>
      )}
    </div>
  );
}

export default function IssuesPage() {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [issues, setIssues] = useState<IssueRow[]>([]);
  const [sites, setSites] = useState<Website[]>([]);
  const [f, setF] = useState({ state: "open", severity: "", website_id: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams({ state: f.state });
    if (f.severity) p.set("severity", f.severity);
    if (f.website_id) p.set("website_id", f.website_id);
    api<IssueRow[]>(`/issues?${p}`).then(setIssues).catch((e) => setError(e.message));
  }, [f]);
  useEffect(load, [load]);
  useEffect(() => { api<Website[]>("/websites").then(setSites).catch(() => undefined); }, []);

  async function setStatus(issue: IssueRow, status: string) {
    setError(null);
    try {
      await api(`/issues/${issue.id}`, { method: "PATCH", body: { status } });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Issues</h1>
      <p className="text-sm text-slate-600">
        {isClient
          ? "Problems found by the monthly technical audits of your websites. An issue is only shown as resolved after a later audit has confirmed the fix."
          : "Issues are created by audits and resolved only when a later audit verifies the fix. Mark an issue “Fixed – awaiting verification” after remediation; the next audit confirms or re-opens it."}
      </p>
      <ErrorNote message={error} />
      <div className="grid gap-2 sm:grid-cols-3">
        <select className={inputClass} aria-label="State" value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}>
          <option value="open">Unresolved</option><option value="resolved">Resolved</option><option value="all">All</option>
        </select>
        <select className={inputClass} aria-label="Severity" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
          <option value="">All severities</option>
          {SEVERITIES.filter((s) => !isClient || s !== "info").map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className={inputClass} aria-label="Website" value={f.website_id} onChange={(e) => setF({ ...f, website_id: e.target.value })}>
          <option value="">All websites</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <Card title={`${issues.length} issue${issues.length === 1 ? "" : "s"}`}>
        {issues.length === 0 ? <p className="text-sm text-slate-500">No issues match.</p> : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Severity</th><th className="pr-4">Issue</th><th className="hidden pr-4 md:table-cell">Website</th>
                  <th className="pr-4">Status</th><th className="hidden pr-4 lg:table-cell">First seen</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {issues.map((i) => (
                  <tr key={i.id} className="align-top">
                    <td className="py-2 pr-4"><SeverityBadge severity={i.severity} /></td>
                    <td className="py-2 pr-4">
                      <div className="break-all">{i.title}</div>
                      <div className="text-xs text-slate-500">
                        {i.category}
                        {!isClient && i.certainty === "warning" && " · warning (may be intermittent)"}
                        {!isClient && (i.reopened_count ?? 0) > 0 && ` · recurred ${i.reopened_count}×`}
                        {!isClient && i.assignee_email && ` · assigned to ${i.assignee_email}`}
                      </div>
                      {!isClient && (
                        <button className="mt-1 text-xs text-slate-600 underline-offset-2 hover:underline" onClick={() => setOpen(open === i.id ? null : i.id)}>
                          {open === i.id ? "Hide details" : `Details & notes (${i.comment_count ?? 0})`}
                        </button>
                      )}
                      {open === i.id && <div className="mt-2"><Notes issue={i} /></div>}
                    </td>
                    <td className="hidden py-2 pr-4 md:table-cell"><Link href={`/websites/${i.website_id}`} className="hover:underline">{i.website_name}</Link></td>
                    <td className="py-2 pr-4">
                      {!isClient && can("issue.manage") && i.status !== "resolved" ? (
                        <select className="rounded-md border border-slate-300 px-2 py-1 text-sm" aria-label="Status" value={i.status}
                                onChange={(e) => setStatus(i, e.target.value)}>
                          {["open", "acknowledged", "in_progress", "resolved_pending_verification"].map((s) => (
                            <option key={s} value={s}>{STAFF_STATUS[s]}</option>
                          ))}
                        </select>
                      ) : (isClient ? i.status : STAFF_STATUS[i.status] ?? i.status)}
                      {i.resolved_at && <div className="text-xs text-slate-500">{fmtDate(i.resolved_at)}</div>}
                    </td>
                    <td className="hidden py-2 pr-4 lg:table-cell">{fmtDate(i.first_seen)}</td>
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
