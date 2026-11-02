"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, Field, inputClass } from "@/components/ui";
import { api, type ClientOrg } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 100);

export default function ClientsPage() {
  const { can } = useAuth();
  const [clients, setClients] = useState<ClientOrg[]>([]);
  const [form, setForm] = useState({ name: "", slug: "", contact_email: "", show_expiry_to_client: false });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<ClientOrg[]>("/clients").then(setClients).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  if (!can("client.manage")) return <ErrorNote message="You do not have access to client management." />;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/clients", { method: "POST", body: { ...form, contact_email: form.contact_email || null } });
      setForm({ name: "", slug: "", contact_email: "", show_expiry_to_client: false });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  async function patch(c: ClientOrg, body: Partial<ClientOrg>) {
    setError(null);
    try {
      await api(`/clients/${c.id}`, { method: "PATCH", body });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Clients</h1>
      <ErrorNote message={error} />
      <Card title="Add client">
        <form onSubmit={create} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Name">
            <input className={inputClass} required value={form.name}
                   onChange={(e) => setForm({ ...form, name: e.target.value, slug: slugify(e.target.value) })} />
          </Field>
          <Field label="Slug">
            <input className={inputClass} required pattern="[a-z0-9-]+" value={form.slug}
                   onChange={(e) => setForm({ ...form, slug: e.target.value })} />
          </Field>
          <Field label="Contact email">
            <input className={inputClass} type="email" value={form.contact_email}
                   onChange={(e) => setForm({ ...form, contact_email: e.target.value })} />
          </Field>
          <div className="flex flex-col justify-end gap-2">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.show_expiry_to_client}
                     onChange={(e) => setForm({ ...form, show_expiry_to_client: e.target.checked })} />
              Show domain/hosting/SSL expiry to client
            </label>
            <Button type="submit">Create client</Button>
          </div>
        </form>
      </Card>
      <Card title={`${clients.length} clients`}>
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr><th className="py-2 pr-4">Name</th><th className="pr-4">Contact</th><th className="pr-4">Websites</th>
                <th className="pr-4">Expiry visible to client</th><th className="pr-4">Status</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {clients.map((c) => (
                <tr key={c.id}>
                  <td className="py-2 pr-4 font-medium">{c.name}<div className="text-xs font-normal text-slate-500">{c.slug}</div></td>
                  <td className="pr-4">{c.contact_email ?? "—"}</td>
                  <td className="pr-4 tabular-nums">{c.website_count}</td>
                  <td className="pr-4">
                    <input type="checkbox" aria-label={`Expiry visible to ${c.name}`} checked={c.show_expiry_to_client}
                           onChange={(e) => patch(c, { show_expiry_to_client: e.target.checked })} />
                  </td>
                  <td className="pr-4">{c.is_active ? "Active" : "Inactive"}</td>
                  <td className="text-right">
                    <Button variant={c.is_active ? "danger" : "secondary"} onClick={() => patch(c, { is_active: !c.is_active })}>
                      {c.is_active ? "Deactivate" : "Activate"}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
