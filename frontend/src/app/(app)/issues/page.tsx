"use client";

import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Button, Card, EmptyState, ErrorNote, PageHeader, SeverityBadge, inputClass } from "@/components/ui";
import { api, fmtDate, SEVERITIES, type IssueComment, type IssueRow, type Severity, type Website } from "@/lib/api";
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
    <div className="space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
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

type Group = { key: string; website: string; websiteId: string; category: string; items: IssueRow[]; worst: Severity };

export default function IssuesPage() {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [issues, setIssues] = useState<IssueRow[] | null>(null);
  const [sites, setSites] = useState<Website[]>([]);
  const [f, setF] = useState({ state: "open", severity: "", website_id: "" });
  const [open, setOpen] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    const p = new URLSearchParams({ state: f.state });
    if (f.severity) p.set("severity", f.severity);
    if (f.website_id) p.set("website_id", f.website_id);
    api<IssueRow[]>(`/issues?${p}`).then(setIssues).catch((e) => setError(e.message));
  }, [f]);
  useEffect(load, [load]);
  useEffect(() => { api<Website[]>("/websites").then(setSites).catch(() => undefined); }, []);

  // One group per website and type of problem, worst first: 40 "missing meta description"
  // rows become a single line that can be expanded.
  const groups = useMemo(() => {
    const m = new Map<string, Group>();
    for (const i of issues ?? []) {
      const key = `${i.website_id}|${i.category}`;
      const g = m.get(key) ?? { key, website: i.website_name, websiteId: i.website_id, category: i.category, items: [], worst: i.severity };
      g.items.push(i);
      if (SEVERITIES.indexOf(i.severity) < SEVERITIES.indexOf(g.worst)) g.worst = i.severity;
      m.set(key, g);
    }
    return [...m.values()].sort((a, b) => SEVERITIES.indexOf(a.worst) - SEVERITIES.indexOf(b.worst)
      || a.website.localeCompare(b.website) || b.items.length - a.items.length);
  }, [issues]);

  async function setStatus(list: IssueRow[], status: string) {
    setError(null);
    try {
      for (const issue of list) {
        if (issue.status !== status && issue.status !== "resolved") await api(`/issues/${issue.id}`, { method: "PATCH", body: { status } });
      }
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
      load();
    }
  }

  const toggle = (k: string) => {
    const n = new Set(expanded);
    if (n.has(k)) n.delete(k); else n.add(k);
    setExpanded(n);
  };
  const manage = !isClient && can("issue.manage");
  const statusSelect = (value: string, onChange: (v: string) => void, label: string, placeholder?: string) => (
    <select className="rounded-lg border-0 py-1.5 pl-2.5 pr-8 text-sm shadow-sm ring-1 ring-inset ring-slate-300 focus:ring-2 focus:ring-brand-600"
            aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
      {placeholder && <option value="">{placeholder}</option>}
      {["open", "acknowledged", "in_progress", "resolved_pending_verification"].map((s) => <option key={s} value={s}>{STAFF_STATUS[s]}</option>)}
    </select>
  );

  return (
    <div className="space-y-6">
      <PageHeader title="Issues" icon={<AlertTriangle size={20} />} description={isClient
        ? "Problems found by the technical audits of your websites. An issue is only shown as resolved after a later audit has confirmed the fix."
        : "Issues come only from audits and are resolved only when a later audit verifies the fix. Mark an issue “Fixed – awaiting verification” after remediation; the next audit confirms or re-opens it."} />
      <ErrorNote message={error} />
      <div className="grid gap-3 sm:grid-cols-3">
        <select className={inputClass} aria-label="State" value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}>
          <option value="open">Unresolved</option><option value="resolved">Resolved</option><option value="all">All</option>
        </select>
        <select className={inputClass} aria-label="Severity" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}>
          <option value="">All severities</option>
          {SEVERITIES.filter((s) => !isClient || s !== "info").map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
        </select>
        <select className={inputClass} aria-label="Website" value={f.website_id} onChange={(e) => setF({ ...f, website_id: e.target.value })}>
          <option value="">All websites</option>{sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <Card flush title={issues ? `${issues.length} issue${issues.length === 1 ? "" : "s"} in ${groups.length} group${groups.length === 1 ? "" : "s"}` : "Issues"}
            actions={groups.length > 1 && (
              <button className="text-sm font-medium text-brand-600 hover:text-brand-500"
                      onClick={() => setExpanded(expanded.size ? new Set() : new Set(groups.map((g) => g.key)))}>
                {expanded.size ? "Collapse all" : "Expand all"}
              </button>
            )}>
        {issues === null ? <p className="p-5 text-sm text-slate-500">Loading…</p> : groups.length === 0 ? (
          <EmptyState icon={<AlertTriangle size={20} />} title="No issues match">
            {f.state === "open" ? "Run a technical audit to check a website for problems." : "Try a different filter."}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100">
            {groups.map((g) => {
              const isOpen = expanded.has(g.key);
              const sev = SEVERITIES.filter((s) => g.items.some((i) => i.severity === s));
              return (
                <li key={g.key}>
                  <div className="flex flex-wrap items-center gap-3 px-5 py-3 hover:bg-slate-50/70">
                    <button onClick={() => toggle(g.key)} className="flex min-w-0 flex-1 items-center gap-3 text-left" aria-expanded={isOpen}>
                      {isOpen ? <ChevronDown size={18} className="shrink-0 text-slate-400" /> : <ChevronRight size={18} className="shrink-0 text-slate-400" />}
                      <SeverityBadge severity={g.worst} />
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-slate-900">{g.category}</span>
                        <span className="block truncate text-xs text-slate-500">
                          {g.website} · {g.items.length} {g.items.length === 1 ? "issue" : "issues"}
                          {sev.length > 1 && ` · ${sev.map((s) => `${g.items.filter((i) => i.severity === s).length} ${s}`).join(", ")}`}
                        </span>
                      </span>
                    </button>
                    {manage && g.items.some((i) => i.status !== "resolved") && statusSelect("", (v) => v && setStatus(g.items, v), `Set status of all ${g.category}`, "Set status for all…")}
                  </div>
                  {isOpen && (
                    <div className="overflow-x-auto border-t border-slate-100 bg-slate-50/40">
                      <table className="sw-table">
                        <thead>
                          <tr><th>Severity</th><th>Issue</th><th>Status</th><th className="hidden lg:table-cell">First seen</th></tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 bg-white">
                          {g.items.map((i) => (
                            <tr key={i.id} className="align-top">
                              <td><SeverityBadge severity={i.severity} /></td>
                              <td className="min-w-[16rem]">
                                <div className="break-all text-slate-900">{i.title}</div>
                                <div className="text-xs text-slate-500">
                                  {!isClient && i.certainty === "warning" && "Warning (may be intermittent) · "}
                                  {!isClient && (i.reopened_count ?? 0) > 0 && `Recurred ${i.reopened_count}× · `}
                                  {!isClient && i.assignee_email && `Assigned to ${i.assignee_email} · `}
                                  <Link href={`/websites/${i.website_id}`} className="hover:underline">{i.website_name}</Link>
                                  {i.url && <> · <a href={i.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 hover:underline">open<ExternalLink size={11} /></a></>}
                                </div>
                                {!isClient && (
                                  <button className="mt-1 text-xs font-medium text-brand-600 hover:text-brand-500" onClick={() => setOpen(open === i.id ? null : i.id)}>
                                    {open === i.id ? "Hide details" : `Details & notes (${i.comment_count ?? 0})`}
                                  </button>
                                )}
                                {open === i.id && <div className="mt-2"><Notes issue={i} /></div>}
                              </td>
                              <td>
                                {manage && i.status !== "resolved"
                                  ? statusSelect(i.status, (v) => setStatus([i], v), "Status")
                                  : (isClient ? i.status : STAFF_STATUS[i.status] ?? i.status)}
                                {i.resolved_at && <div className="text-xs text-slate-500">{fmtDate(i.resolved_at)}</div>}
                              </td>
                              <td className="hidden whitespace-nowrap text-slate-600 lg:table-cell">{fmtDate(i.first_seen)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
