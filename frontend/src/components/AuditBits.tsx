import { SeverityBadge } from "@/components/ui";
import { SEVERITIES, type AuditRun } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  completed: "text-emerald-700", partial: "text-amber-800", failed: "text-red-700 font-medium",
  running: "text-sky-700", queued: "text-slate-600",
};

export function RunStatus({ run }: { run: AuditRun }) {
  return (
    <span className={`capitalize ${STATUS_STYLE[run.status] ?? ""}`} title={run.error ?? undefined}>
      {run.status === "partial" ? "Partial" : run.status}
    </span>
  );
}

export function SeverityCounts({ run }: { run: AuditRun }) {
  const s = run.summary.by_severity;
  if (!s) return <span className="text-slate-400">—</span>;
  const shown = SEVERITIES.filter((k) => s[k]);
  if (!shown.length) return <span className="text-emerald-700">No issues</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((k) => <span key={k} className="inline-flex items-center gap-1"><SeverityBadge severity={k} /><span className="tabular-nums">{s[k]}</span></span>)}
    </div>
  );
}
