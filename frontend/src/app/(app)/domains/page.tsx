"use client";

import { Network, Pencil } from "lucide-react";
import Link from "next/link";
import { Fragment, useCallback, useEffect, useState } from "react";
import { ExpiryEditor } from "@/components/ExpiryCard";
import { ExpiryCell } from "@/components/ExpiryBits";
import { Button, Card, ErrorNote, PageHeader } from "@/components/ui";
import { api, type ExpiryOverviewRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function DomainsPage() {
  const { me, can } = useAuth();
  const [editing, setEditing] = useState<string | null>(null);
  const isClient = me?.roles.includes("client");
  const [rows, setRows] = useState<ExpiryOverviewRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ websites: ExpiryOverviewRow[] }>("/expiry/overview").then((r) => setRows(r.websites)).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  const write = can("expiry.write");

  const sorted = [...(rows ?? [])].sort((a, b) =>
    Math.min(a.domain.days_left ?? 1e9, a.hosting.days_left ?? 1e9) - Math.min(b.domain.days_left ?? 1e9, b.hosting.days_left ?? 1e9));

  return (
    <div className="space-y-6">
      <PageHeader title="Domain & Hosting" icon={<Network size={20} />} description={<>
        Domain expiry dates are looked up automatically from the registry (RDAP, or WHOIS where RDAP is not available).
        Hosting renewal dates are usually not public: use <strong>Edit</strong> to enter them (shown as <strong>Manual entry</strong>),
        and enter a domain date manually when the registry does not publish one.
      </>} />
      <ErrorNote message={error} />
      {rows && rows.length === 0 && (
        <p className="text-sm text-slate-500">
          {isClient ? "Expiry information has not been shared with your organization." : "No websites."}
        </p>
      )}
      {sorted.length > 0 && (
        <Card title="Soonest first" flush>
          <div className="overflow-x-auto">
            <table className="sw-table">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Website</th><th className="pr-4">Domain</th><th className="pr-4">Domain expiry</th>
                  <th className="pr-4">Hosting renewal</th>{write && <th><span className="sr-only">Actions</span></th>}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sorted.map((r) => (
                  <Fragment key={r.website_id}>
                  <tr className="align-top">
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
                    {write && (
                      <td className="text-right">
                        <Button size="sm" variant="secondary" onClick={() => setEditing(editing === r.website_id ? null : r.website_id)}>
                          <Pencil size={14} />Edit
                        </Button>
                      </td>
                    )}
                  </tr>
                  {editing === r.website_id && (
                    <tr className="bg-brand-50/30 hover:bg-brand-50/30">
                      <td colSpan={5}><ExpiryEditor websiteId={r.website_id} onSaved={load} onClose={() => setEditing(null)} /></td>
                    </tr>
                  )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
