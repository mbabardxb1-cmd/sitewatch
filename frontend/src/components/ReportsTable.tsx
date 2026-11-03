"use client";

import { Button, StatusBadge } from "@/components/ui";
import { fmtDate, periodLabel, type ReportRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function ReportsTable({ rows, onApprove, onWithdraw, showWebsite = true }: {
  rows: ReportRow[];
  onApprove?: (r: ReportRow, email: boolean) => void;
  onWithdraw?: (r: ReportRow) => void;
  showWebsite?: boolean;
}) {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  if (rows.length === 0) {
    return <p className="text-sm text-slate-500">{isClient ? "No reports have been published for you yet." : "No reports yet."}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead className="text-xs uppercase text-slate-500">
          <tr>
            <th className="py-2 pr-4">Period</th>
            {showWebsite && <th className="pr-4">Website</th>}
            {!isClient && <th className="pr-4">Type</th>}
            <th className="pr-4">Overall status</th>
            {!isClient && <th className="hidden pr-4 md:table-cell">Client access</th>}
            <th className="pr-4" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r) => (
            <tr key={r.id} className="align-top">
              <td className="py-2 pr-4 font-medium">{periodLabel(r.period)}</td>
              {showWebsite && <td className="py-2 pr-4">{r.website_name}</td>}
              {!isClient && <td className="py-2 pr-4 capitalize">{r.kind}</td>}
              <td className="py-2 pr-4">
                {r.status === "ready" && r.overall_level ? <StatusBadge status={r.overall_level} />
                  : r.status === "generating" ? <span className="text-sky-700">Generating…</span>
                  : r.status === "failed" ? <span className="font-medium text-red-700" title={r.error ?? ""}>Failed</span> : "—"}
                {r.generated_at && <div className="text-xs text-slate-500">Generated {fmtDate(r.generated_at)}</div>}
              </td>
              {!isClient && (
                <td className="hidden py-2 pr-4 md:table-cell">
                  {r.kind === "technical" ? <span className="text-slate-500">Staff only</span>
                    : r.approved_for_client ? (
                      <div>
                        <span className="text-emerald-700">Published</span>
                        {r.emailed_at && <div className="text-xs text-slate-500">Emailed to {r.emailed_to?.join(", ")}</div>}
                      </div>
                    ) : <span className="text-amber-800">Awaiting approval</span>}
                </td>
              )}
              <td className="py-2 pr-4">
                <div className="flex flex-wrap justify-end gap-2">
                  {r.status === "ready" && (
                    <a className="rounded-md px-3 py-1.5 text-sm font-medium ring-1 ring-slate-300 hover:bg-slate-50"
                       href={`/api/v1/reports/${r.id}/download`}>Download PDF</a>
                  )}
                  {can("report.approve") && r.kind === "client" && r.status === "ready" && !r.approved_for_client && onApprove && (
                    <>
                      <Button variant="secondary" onClick={() => onApprove(r, false)}>Publish</Button>
                      <Button onClick={() => onApprove(r, true)}>Publish &amp; email</Button>
                    </>
                  )}
                  {can("report.approve") && r.approved_for_client && onWithdraw && (
                    <Button variant="danger" onClick={() => onWithdraw(r)}>Withdraw</Button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
