"use client";

import { Pencil, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { UserEditor } from "@/components/UserEditor";
import { Button, Card, ErrorNote, inputClass, PageHeader } from "@/components/ui";
import { api, type ClientOrg, type UserRow } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const ROLE_LABEL: Record<string, string> = {
  super_admin: "Super Administrator",
  developer: "Developer",
  client: "Client (read-only)",
};

export default function UsersPage() {
  const { me, can } = useAuth();
  const [users, setUsers] = useState<UserRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const empty = { email: "", full_name: "", password: "", role: "developer", client_id: "" };
  const [form, setForm] = useState(empty);
  const [clients, setClients] = useState<ClientOrg[]>([]);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<UserRow | null>(null);

  const load = useCallback(() => {
    api<UserRow[]>("/users").then(setUsers).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    api<ClientOrg[]>("/clients").then((cs) => setClients(cs.filter((c) => c.is_active))).catch(() => undefined);
  }, []);
  const clientName = (id: string | null) => clients.find((c) => c.id === id)?.name;

  if (!can("user.manage")) return <ErrorNote message="You do not have access to user management." />;

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/users", {
        method: "POST",
        body: { ...form, client_id: form.role === "client" ? form.client_id : null },
      });
      setForm(empty);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(u: UserRow) {
    setError(null);
    try {
      await api(`/users/${u.id}/${u.is_active ? "deactivate" : "activate"}`, { method: "POST" });
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Users & Permissions" icon={<Users size={20} />} description="Invite team members and clients, and control what each role can see." />
      <ErrorNote message={error} />
      <Card title="Add user">
        <form onSubmit={create} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <input className={inputClass} placeholder="Email" type="email" required value={form.email}
                 onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input className={inputClass} placeholder="Full name" value={form.full_name}
                 onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          <input className={inputClass} placeholder="Initial password" type="password" required minLength={12}
                 autoComplete="new-password" value={form.password}
                 onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <select className={inputClass} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="developer">Developer</option>
            <option value="super_admin">Super Administrator</option>
            <option value="client" disabled={clients.length === 0}>Client (read-only)</option>
          </select>
          {form.role === "client" && (
            <select className={inputClass} required value={form.client_id}
                    onChange={(e) => setForm({ ...form, client_id: e.target.value })}>
              <option value="">Client organization…</option>
              {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          )}
          <Button type="submit" disabled={busy}>Create</Button>
        </form>
        <p className="mt-2 text-xs text-slate-500">
          Passwords need 12+ characters with upper- and lower-case letters and a digit. Client users only see
          websites they are assigned to on the website&apos;s page, and only within their own organization.
        </p>
      </Card>
      {editing && (
        <UserEditor user={editing} clients={clients} isSelf={editing.id === me?.id}
                    onClose={() => setEditing(null)} onSaved={load} />
      )}
      <Card title={`${users.length} users`} flush>
        <div className="overflow-x-auto">
          <table className="sw-table">
            <thead>
              <tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th>Last login</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="py-2 pr-4">{u.email}</td>
                  <td className="pr-4">{u.full_name}</td>
                  <td className="pr-4">
                    {u.roles.map((r) => ROLE_LABEL[r] ?? r).join(", ")}
                    {u.client_id && <div className="text-xs text-slate-500">{clientName(u.client_id) ?? "Client org"}</div>}
                    {u.custom_permissions && <div className="text-xs font-medium text-amber-700">Custom permissions</div>}
                  </td>
                  <td className="pr-4">
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${u.is_active
                      ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20" : "bg-slate-100 text-slate-600 ring-slate-500/15"}`}>
                      {u.is_active ? "Active" : "Deactivated"}
                    </span>
                  </td>
                  <td className="pr-4">{u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "Never"}</td>
                  <td>
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="secondary" onClick={() => setEditing(u)}><Pencil size={14} />Edit</Button>
                      {u.id !== me?.id && (
                        <Button size="sm" variant={u.is_active ? "danger" : "secondary"} onClick={() => toggle(u)}>
                          {u.is_active ? "Deactivate" : "Activate"}
                        </Button>
                      )}
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
