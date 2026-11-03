"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ExpiryCell } from "@/components/ExpiryBits";
import { Card, ErrorNote } from "@/components/ui";
import { api, type ExpiryOverviewRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function DomainsPage() {
  const { me } = useAuth();
  const isClient = me?.roles.includes("client");
  const [rows, setRows] = useState<ExpiryOverviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ websites: ExpiryOverviewRow[] }>("/expiry/overview").then((r) => setRows(r.websites)).catch((e) => setError(e.message));
  }, []);

  const sorted = [...(rows ?? [])].sort((a, b) =>
    Math.min(a.domain.days_left ?? 1e9, a.hosting.days_left ?? 1e9) - Math.min(b.domain.days_left ?? 1e9, b.hosting.days_left ?? 1e9));

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Domain &amp; Hosting</h1>
      <p className="text-sm text-slate-600">
        Domain expiry dates are looked up automatically from the registry (RDAP, or WHOIS where RDAP is not available).
        Hosting renewal dates are usually not public: they are shown as <strong>Manual entry</strong> unless verified
        through a provider API, and as <strong>Unknown</strong> when nobody has recorded one.
      </p>
      <ErrorNote message={error} />
      {rows && rows.length === 0 && (
        <p className="text-sm text-slate-500">
          {isClient ? "Expiry information has not been shared with your organization." : "No websites."}
        </p>
      )}
      {sorted.length > 0 && (
        <Card title="Soonest first">
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Domain</th><th className="pr-4">Domain expiry</th>
                  <th className="pr-4">Hosting renewal</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sorted.map((r) => (
                  <tr key={r.website_id} className="align-top">
                    <td className="py-2 pr-4">
                      <Link href={`/websites/${r.website_id}#expiry`} className="font-medium hover:underline">{r.website_name}</Link>
                      {!isClient && <div className="text-xs text-slate-500">{r.client_name}</div>}
                    </td>
                    <td className="py-2 pr-4">
                      {r.domain.domain ? (
                        <>
                          <div>{r.domain.domain}</div>
                          <div className="text-xs text-slate-500">{r.domain.registrar ?? "Registrar unknown"}</div>
                        </>
                      ) : <div className="text-slate-500">No domain</div>}
                    </td>
                    <td className="py-2 pr-4"><ExpiryCell e={r.domain} /></td>
                    <td className="py-2 pr-4">
                      <div className="mb-1 text-xs text-slate-500">{r.hosting.provider ?? "Provider not recorded"}</div>
                      <ExpiryCell e={r.hosting} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
