"use client";

import { Globe } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Card, ErrorNote, Field, inputClass, PageHeader } from "@/components/ui";
import { DEFAULT_MONITORING, MonitoringFields } from "@/components/WebsiteForms";
import { api, type ClientOrg, type WebsiteStaff } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function NewWebsitePage() {
  const router = useRouter();
  const { can } = useAuth();
  const [clients, setClients] = useState<ClientOrg[]>([]);
  const [clientId, setClientId] = useState("");
  const [values, setValues] = useState(DEFAULT_MONITORING);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<ClientOrg[]>("/clients")
      .then((cs) => setClients(cs.filter((c) => c.is_active)))
      .catch((e) => setError(e.message));
  }, []);

  if (!can("website.write")) return <ErrorNote message="You do not have permission to add websites." />;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const site = await api<WebsiteStaff>("/websites", { method: "POST", body: { ...values, client_id: clientId } });
      router.push(`/websites/${site.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/websites" className="text-sm text-slate-500 hover:underline">← Websites</Link>
        <PageHeader title="Add website" icon={<Globe size={20} />} description="Register a website to start uptime monitoring. Audits run when you start one, or monthly if enabled." />
      </div>
      <form onSubmit={submit} className="space-y-4">
        <ErrorNote message={error} />
        <Card title="Client">
          {clients.length === 0 ? (
            <p className="text-sm text-slate-600">
              No active client organizations yet.{" "}
              {can("client.manage") ? <Link href="/clients" className="underline">Create one first.</Link>
                : "Ask an administrator to create one."}
            </p>
          ) : (
            <Field label="Client organization">
              <select className={inputClass} required value={clientId} onChange={(e) => setClientId(e.target.value)}>
                <option value="">Select…</option>
                {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </Field>
          )}
        </Card>
        <Card title="Monitoring">
          <MonitoringFields v={values} set={setValues} />
        </Card>
        <p className="text-xs text-slate-500">
          Crawl settings start with safe defaults (depth 3, 500 pages, 2 requests/second) and can be changed after saving.
        </p>
        <Button type="submit" disabled={busy || !clientId}>{busy ? "Saving…" : "Add website"}</Button>
      </form>
    </div>
  );
}
