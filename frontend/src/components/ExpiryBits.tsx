import { StatusBadge } from "@/components/ui";
import { fmtDate, fmtDay, type EffectiveDate } from "@/lib/api";

const SOURCE: Record<string, [string, string]> = {
  rdap: ["Verified · RDAP", "bg-emerald-50 text-emerald-800 ring-emerald-600/20"],
  whois: ["Verified · WHOIS", "bg-emerald-50 text-emerald-800 ring-emerald-600/20"],
  registrar_api: ["Verified · registrar API", "bg-emerald-50 text-emerald-800 ring-emerald-600/20"],
  api: ["API-verified", "bg-emerald-50 text-emerald-800 ring-emerald-600/20"],
  certificate: ["Live certificate", "bg-emerald-50 text-emerald-800 ring-emerald-600/20"],
  manual: ["Manual entry", "bg-amber-50 text-amber-900 ring-amber-600/30"],
  unknown: ["Unknown", "bg-slate-100 text-slate-700 ring-slate-500/20"],
};

export function SourceBadge({ source }: { source: string }) {
  const [label, style] = SOURCE[source] ?? [source, SOURCE.unknown[1]];
  return <span className={`inline-flex whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>{label}</span>;
}

export function daysText(d: number | null): string {
  if (d === null) return "";
  if (d < 0) return `expired ${-d} day${d === -1 ? "" : "s"} ago`;
  if (d === 0) return "expires today";
  return `${d} day${d === 1 ? "" : "s"} left`;
}

/** Date + days remaining + status + where the date came from. */
export function ExpiryCell({ e, invalid }: { e: EffectiveDate; invalid?: boolean }) {
  if (!e.date) {
    return (
      <div className="flex flex-col items-start gap-0.5">
        <SourceBadge source="unknown" />
        {e.note && <span className="text-xs text-slate-500">{e.note}</span>}
      </div>
    );
  }
  return (
    <div className="flex flex-col items-start gap-0.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="tabular-nums">{fmtDay(e.date)}</span>
        <StatusBadge status={e.level} />
      </div>
      <span className="text-xs tabular-nums text-slate-600">{invalid ? "certificate not valid" : daysText(e.days_left)}</span>
      <div className="flex flex-wrap items-center gap-1">
        <SourceBadge source={e.source} />
        {e.as_of && <span className="text-xs text-slate-500">{e.source === "manual" ? "entered" : "checked"} {fmtDate(e.as_of)}</span>}
      </div>
      {e.note && <span className="text-xs text-slate-500">{e.note}</span>}
    </div>
  );
}
