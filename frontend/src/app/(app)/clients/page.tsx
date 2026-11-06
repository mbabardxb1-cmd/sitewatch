"use client";

import { Building2, Check, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, Field, Notice, PageHeader, inputClass } from "@/components/ui";
import { api, type ClientOrg } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 100);

export default function ClientsPage() {
  const { can } = useAuth();
  const [clients, setClients] = useState<ClientOrg[]>([]);
  const [form, setForm] = useState({ name: "", slug: "", contact_email: "", show_expiry_to_client: false });
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState({ name: "", contact_email: "" });

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

  async function patch(c: ClientOrg, body: Partial<ClientOrg>, ok?: string) {
    setError(null);
    setNotice(null);
    try {
      await api(`/clients/${c.id}`, { method: "PATCH", body });
      if (ok) setNotice(ok);
      setEditing(null);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  function edit(c: ClientOrg) {
    setEditing(c.id);
    setDraft({ name: c.name, contact_email: c.contact_email ?? "" });
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Clients" icon={<Building2 size={20} />}
                  description="Client organizations own websites. Client users only ever see their own organization's websites and reports." />
      <ErrorNote message={error} />
      {notice && <Notice tone="success">{notice}</Notice>}
      <Card title="Add client">
        <form onSubmit={create} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Name">
            <input className={inputClass} required value={form.name}
                   onChange={(e) => setForm({ ...form, name: e.target.value, slug: slugify(e.target.value) })} />
          </Field>
          <Field label="Slug" hint="Short ID used in URLs; cannot be changed later.">
            <input className={inputClass} required pattern="[a-z0-9\-]+" value={form.slug}
                   onChange={(e) => setForm({ ...form, slug: e.target.value })} />
          </Field>
          <Field label="Contact email">
            <input className={inputClass} type="email" value={form.contact_email}
                   onChange={(e) => setForm({ ...form, contact_email: e.target.value })} />
          </Field>
          <div className="flex flex-col justify-end gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-brand-600" checked={form.show_expiry_to_client}
                     onChange={(e) => setForm({ ...form, show_expiry_to_client: e.target.checked })} />
              Show domain/hosting/SSL expiry to client
            </label>
            <Button type="submit">Create client</Button>
          </div>
        </form>
      </Card>
      <Card title={`${clients.length} client${clients.length === 1 ? "" : "s"}`} flush>
        <div className="overflow-x-auto">
          <table className="sw-table">
            <thead>
              <tr><th>Name</th><th>Contact</th><th className="text-right">Websites</th>
                <th>Expiry visible to client</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {clients.map((c) => editing === c.id ? (
                <tr key={c.id} className="bg-brand-50/40 align-top">
                  <td colSpan={6}>
                    <form className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={(e) => {
                      e.preventDefault();
                      patch(c, { name: draft.name, contact_email: draft.contact_email || null }, `${draft.name} updated.`);
                    }}>
                      <Field label="Name">
                        <input className={inputClass} required maxLength={200} value={draft.name} autoFocus
                               onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                      </Field>
                      <Field label="Contact email">
                        <input className={inputClass} type="email" value={draft.contact_email} placeholder="Optional"
                               onChange={(e) => setDraft({ ...draft, contact_email: e.target.value })} />
                      </Field>
                      <div className="flex gap-2">
                        <Button type="submit"><Check size={16} />Save</Button>
                        <Button type="button" variant="secondary" onClick={() => setEditing(null)}><X size={16} />Cancel</Button>
                      </div>
                    </form>
                    <p className="mt-2 text-xs text-slate-500">Slug: {c.slug} (cannot be changed)</p>
                  </td>
                </tr>
              ) : (
                <tr key={c.id}>
                  <td className="font-medium text-slate-900">{c.name}<div className="text-xs font-normal text-slate-500">{c.slug}</div></td>
                  <td className="text-slate-600">{c.contact_email ?? "—"}</td>
                  <td className="text-right tabular-nums">{c.website_count}</td>
                  <td>
                    <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-brand-600" aria-label={`Expiry visible to ${c.name}`}
                           checked={c.show_expiry_to_client} onChange={(e) => patch(c, { show_expiry_to_client: e.target.checked })} />
                  </td>
                  <td>
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${c.is_active
                      ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20" : "bg-slate-100 text-slate-600 ring-slate-500/15"}`}>
                      {c.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td>
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="secondary" onClick={() => edit(c)}><Pencil size={14} />Edit</Button>
                      <Button size="sm" variant={c.is_active ? "danger" : "secondary"} onClick={() => patch(c, { is_active: !c.is_active })}>
                        {c.is_active ? "Deactivate" : "Activate"}
                      </Button>
                    </div>
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
