"use client";

import { ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ExpiryCell } from "@/components/ExpiryBits";
import { Card, ErrorNote, PageHeader } from "@/components/ui";
import { api, type ExpiryOverviewRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function SslPage() {
  const { me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [rows, setRows] = useState<ExpiryOverviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ websites: ExpiryOverviewRow[] }>("/expiry/overview").then((r) => setRows(r.websites)).catch((e) => setError(e.message));
  }, []);

  const applicable = (rows ?? []).filter((r) => r.ssl.applicable)
    .sort((a, b) => (a.ssl.valid === false ? -1 : 0) - (b.ssl.valid === false ? -1 : 0) || (a.ssl.days_left ?? 1e9) - (b.ssl.days_left ?? 1e9));
  const skipped = (rows ?? []).filter((r) => !r.ssl.applicable);

  return (
    <div className="space-y-6">
      <PageHeader title="SSL Certificates" icon={<ShieldCheck size={20} />} />
      <p className="text-sm text-slate-600">
        Certificates are checked daily (every 6 hours in the last two weeks before expiry) by connecting to each website.
      </p>
      <ErrorNote message={error} />
      {rows && rows.length === 0 && (
        <p className="text-sm text-slate-500">{isClient ? "Certificate information has not been shared with your organization." : "No websites."}</p>
      )}
      {applicable.length > 0 && (
        <Card title="Monitored certificates">
          <div className="overflow-x-auto">
            <table className="sw-table">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Issuer</th><th className="pr-4">Expires</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {applicable.map((r) => (
                  <tr key={r.website_id} className="align-top">
                    <td className="py-2 pr-4">
                      <Link href={`/websites/${r.website_id}#expiry`} className="font-medium hover:underline">{r.website_name}</Link>
                      <div className="break-all text-xs text-slate-500">{r.base_url}</div>
                    </td>
                    <td className="py-2 pr-4">{r.ssl.issuer ?? "—"}</td>
                    <td className="py-2 pr-4"><ExpiryCell e={r.ssl} invalid={r.ssl.valid === false} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {skipped.length > 0 && (
        <Card title="Not monitored">
          <ul className="space-y-1 text-sm">
            {skipped.map((r) => <li key={r.website_id}>{r.website_name} <span className="text-slate-500">— {r.ssl.note}</span></li>)}
          </ul>
        </Card>
      )}
    </div>
  );
}
