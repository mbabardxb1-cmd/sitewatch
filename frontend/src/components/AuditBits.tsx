import { SeverityBadge } from "@/components/ui";
import { SEVERITIES, type AuditRun } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  partial: "bg-amber-50 text-amber-800 ring-amber-600/25",
  failed: "bg-red-50 text-red-700 ring-red-600/20",
  running: "bg-brand-50 text-brand-700 ring-brand-600/20",
  queued: "bg-slate-100 text-slate-600 ring-slate-500/15",
};

export function RunStatus({ run }: { run: AuditRun }) {
  return (
    <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${STATUS_STYLE[run.status] ?? ""}`}
          title={run.error ?? undefined}>
      {run.status === "partial" ? "Partial" : run.status}
    </span>
  );
}

export function SeverityCounts({ run }: { run: AuditRun }) {
  const s = run.summary.by_severity;
  if (!s) return <span className="text-slate-400">—</span>;
  const shown = SEVERITIES.filter((k) => s[k]);
  if (!shown.length) return <span className="text-sm text-emerald-700">No issues</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((k) => <span key={k} className="inline-flex items-center gap-1"><SeverityBadge severity={k} /><span className="tabular-nums">{s[k]}</span></span>)}
    </div>
  );
}
