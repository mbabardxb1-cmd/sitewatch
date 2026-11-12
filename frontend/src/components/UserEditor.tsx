"use client";

import { Globe, KeyRound, ShieldCheck, UserRound, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button, ErrorNote, Field, Notice, inputClass } from "@/components/ui";
import { api, type ClientOrg, type PermissionCatalogue, type UserRow, type UserWebsites } from "@/lib/api";

const ROLE_INFO: Record<string, { label: string; text: string }> = {
  super_admin: { label: "Super Administrator", text: "Full access to everything, including users and settings." },
  developer: { label: "Developer", text: "Works on assigned websites: monitoring, audits, issues, maintenance and reports. Permissions can be adjusted per person." },
  client: { label: "Client (read-only)", text: "Sees only their own organization's assigned websites: status, issues summary and approved reports." },
};

type Tab = "profile" | "role" | "permissions" | "websites";

export function UserEditor({ user, clients, isSelf, onClose, onSaved }: {
  user: UserRow; clients: ClientOrg[]; isSelf: boolean; onClose: () => void; onSaved: () => void;
}) {
  const [tab, setTab] = useState<Tab>("profile");
  const [u, setU] = useState(user);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const role = u.roles[0] ?? "developer";

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function run<T>(fn: () => Promise<T>, ok: string): Promise<T | undefined> {
    setError(null);
    setNotice(null);
    try {
      const r = await fn();
      setNotice(ok);
      onSaved();
      return r;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "profile", label: "Profile", icon: <UserRound size={16} /> },
    { key: "role", label: "Role", icon: <KeyRound size={16} /> },
    { key: "permissions", label: "Permissions", icon: <ShieldCheck size={16} /> },
    { key: "websites", label: "Website access", icon: <Globe size={16} /> },
  ];

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`Edit ${u.email}`}>
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div className="relative flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl">
        <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-6 py-5">
          <div className="min-w-0">
            <h2 className="truncate text-lg font-semibold text-slate-900">{u.full_name || u.email}</h2>
            <p className="truncate text-sm text-slate-500">{u.email} · {ROLE_INFO[role]?.label ?? role}</p>
          </div>
          <button onClick={onClose} className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close">
            <X size={20} />
          </button>
        </div>
        <nav className="flex gap-1 overflow-x-auto border-b border-slate-200 px-4">
          {tabs.map((t) => (
            <button key={t.key} onClick={() => { setTab(t.key); setNotice(null); setError(null); }}
                    className={`-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium ${
                      tab === t.key ? "border-brand-600 text-brand-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>
              {t.icon}{t.label}
            </button>
          ))}
        </nav>
        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          <ErrorNote message={error} />
          {notice && <Notice tone="success">{notice}</Notice>}
          {tab === "profile" && <ProfileTab u={u} run={run} setU={setU} />}
          {tab === "role" && <RoleTab u={u} clients={clients} isSelf={isSelf} run={run} setU={setU} />}
          {tab === "permissions" && <PermissionsTab u={u} run={run} setU={setU} />}
          {tab === "websites" && <WebsitesTab u={u} run={run} />}
        </div>
      </div>
    </div>
  );
}

type Run = <T>(fn: () => Promise<T>, ok: string) => Promise<T | undefined>;

function ProfileTab({ u, run, setU }: { u: UserRow; run: Run; setU: (u: UserRow) => void }) {
  const [f, setF] = useState({ full_name: u.full_name, email: u.email, password: "" });
  return (
    <form className="space-y-4" onSubmit={async (e) => {
      e.preventDefault();
      const body: Record<string, string> = {};
      if (f.full_name !== u.full_name) body.full_name = f.full_name;
      if (f.email !== u.email) body.email = f.email;
      if (f.password) body.password = f.password;
      const r = await run(() => api<UserRow>(`/users/${u.id}`, { method: "PATCH", body }),
        f.password ? "Saved. The new password is active and the user was signed out everywhere." : "Profile saved.");
      if (r) { setU(r); setF({ ...f, password: "" }); }
    }}>
      <Field label="Full name"><input className={inputClass} maxLength={200} value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
      <Field label="Email" hint="Used to sign in.">
        <input className={inputClass} type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      </Field>
      <Field label="New password" hint="Leave empty to keep the current password. 12+ characters with upper- and lower-case letters and a digit.">
        <input className={inputClass} type="password" autoComplete="new-password" minLength={12} value={f.password}
               onChange={(e) => setF({ ...f, password: e.target.value })} />
      </Field>
      <Button type="submit">Save profile</Button>
    </form>
  );
}

function RoleTab({ u, clients, isSelf, run, setU }: { u: UserRow; clients: ClientOrg[]; isSelf: boolean; run: Run; setU: (u: UserRow) => void }) {
  const [role, setRole] = useState(u.roles[0] ?? "developer");
  const [clientId, setClientId] = useState(u.client_id ?? "");
  return (
    <form className="space-y-4" onSubmit={async (e) => {
      e.preventDefault();
      const r = await run(() => api<UserRow>(`/users/${u.id}/role`, { method: "PUT", body: { role, client_id: role === "client" ? clientId : null } }),
        "Role saved. The user was signed out so the new access applies at their next sign-in.");
      if (r) setU(r);
    }}>
      {isSelf && <Notice tone="warning">This is your own account. Another super administrator must exist before you can stop being one.</Notice>}
      <div className="space-y-2">
        {Object.entries(ROLE_INFO).map(([key, info]) => (
          <label key={key} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-4 ${role === key ? "border-brand-500 bg-brand-50/50 ring-1 ring-brand-500" : "border-slate-200 hover:bg-slate-50"}`}>
            <input type="radio" name="role" className="mt-1 text-brand-600" checked={role === key} onChange={() => setRole(key)}
                   disabled={key === "client" && clients.length === 0} />
            <span>
              <span className="block text-sm font-semibold text-slate-900">{info.label}</span>
              <span className="block text-sm text-slate-500">{info.text}</span>
            </span>
          </label>
        ))}
      </div>
      {role === "client" && (
        <Field label="Client organization">
          <select className={inputClass} required value={clientId} onChange={(e) => setClientId(e.target.value)}>
            <option value="">Select…</option>
            {clients.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
      )}
      {u.custom_permissions && role !== (u.roles[0] ?? "") && (
        <p className="text-sm text-amber-800">Changing the role removes this user&apos;s custom permissions.</p>
      )}
      <Button type="submit">Save role</Button>
    </form>
  );
}

function PermissionsTab({ u, run, setU }: { u: UserRow; run: Run; setU: (u: UserRow) => void }) {
  const [cat, setCat] = useState<PermissionCatalogue | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set(u.permissions));
  const role = u.roles[0] ?? "";
  useEffect(() => { api<PermissionCatalogue>("/users/permission-catalogue").then(setCat).catch(() => undefined); }, []);
  if (!cat) return <p className="text-sm text-slate-500">Loading…</p>;
  const editable = cat.customizable_roles.includes(role);
  const defaults = new Set(cat.groups.flatMap((g) => g.permissions.filter((p) => p.roles.includes(role)).map((p) => p.key)));
  const changed = [...new Set([...sel, ...defaults])].filter((k) => sel.has(k) !== defaults.has(k)).length;

  const toggle = (k: string) => {
    const n = new Set(sel);
    if (n.has(k)) n.delete(k); else n.add(k);
    setSel(n);
  };

  return (
    <div className="space-y-5">
      {!editable ? (
        <Notice>
          {role === "super_admin"
            ? "Super administrators always have every permission. Change the role to Developer to limit what this person can do."
            : "Client users have a fixed read-only set of permissions, so they can never see other organizations' data. Control which websites they see under Website access."}
        </Notice>
      ) : (
        <p className="text-sm text-slate-500">
          Start from the Developer defaults and switch individual permissions on or off for this person.
          Changes apply immediately. {changed > 0 && <strong className="text-slate-800">{changed} change{changed === 1 ? "" : "s"} from the defaults.</strong>}
        </p>
      )}
      {cat.groups.map((g) => (
        <fieldset key={g.name} className="rounded-xl border border-slate-200">
          <legend className="ml-3 px-1 text-xs font-semibold uppercase tracking-wider text-slate-500">{g.name}</legend>
          <ul className="divide-y divide-slate-100">
            {g.permissions.map((p) => {
              const on = editable ? sel.has(p.key) : u.permissions.includes(p.key);
              const differs = editable && on !== defaults.has(p.key);
              return (
                <li key={p.key}>
                  <label className={`flex items-center justify-between gap-4 px-4 py-2.5 ${editable ? "cursor-pointer hover:bg-slate-50" : ""}`}>
                    <span className="min-w-0">
                      <span className="block text-sm text-slate-800">{p.description}</span>
                      <span className="block font-mono text-[11px] text-slate-400">
                        {p.key}{differs && <span className="ml-2 font-sans font-medium text-amber-700">{on ? "added" : "removed"}</span>}
                      </span>
                    </span>
                    <input type="checkbox" className="h-4 w-4 shrink-0 rounded border-slate-300 text-brand-600 disabled:opacity-60"
                           checked={on} disabled={!editable} onChange={() => toggle(p.key)} />
                  </label>
                </li>
              );
            })}
          </ul>
        </fieldset>
      ))}
      {editable && (
        <div className="sticky bottom-0 flex flex-wrap gap-2 border-t border-slate-200 bg-white py-3">
          <Button onClick={async () => {
            const r = await run(() => api<UserRow>(`/users/${u.id}/permissions`, { method: "PUT", body: { permissions: [...sel] } }), "Permissions saved.");
            if (r) { setU(r); setSel(new Set(r.permissions)); }
          }}>Save permissions</Button>
          <Button variant="secondary" onClick={() => setSel(new Set(defaults))}>Reset to Developer defaults</Button>
        </div>
      )}
    </div>
  );
}

function WebsitesTab({ u, run }: { u: UserRow; run: Run }) {
  const [data, setData] = useState<UserWebsites | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const load = useCallback(() => {
    api<UserWebsites>(`/users/${u.id}/websites`).then((d) => {
      setData(d);
      setSel(new Set(d.websites.filter((w) => w.assigned).map((w) => w.id)));
    }).catch(() => undefined);
  }, [u.id]);
  useEffect(load, [load]);
  if (!data) return <p className="text-sm text-slate-500">Loading…</p>;
  const shown = data.websites.filter((w) => !q || `${w.name} ${w.base_url} ${w.client_name}`.toLowerCase().includes(q.toLowerCase()));
  const toggle = (id: string) => {
    const n = new Set(sel);
    if (n.has(id)) n.delete(id); else n.add(id);
    setSel(n);
  };

  return (
    <div className="space-y-4">
      {data.sees_all ? (
        <Notice>This user can see <strong>every website</strong> (permission “Access every website regardless of assignment”). Assignments below only matter if that permission is removed.</Notice>
      ) : (
        <p className="text-sm text-slate-500">
          This user only sees the websites ticked below{u.client_id ? " (only their organization's websites are listed)" : ""}.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <input className={`${inputClass} max-w-xs`} placeholder="Search websites" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="text-sm font-medium text-brand-600" onClick={() => setSel(new Set(data.websites.map((w) => w.id)))}>Select all</button>
        <button className="text-sm font-medium text-slate-500" onClick={() => setSel(new Set())}>Clear</button>
        <span className="text-sm text-slate-500">{sel.size} of {data.websites.length} selected</span>
      </div>
      {data.websites.length === 0 ? <p className="text-sm text-slate-500">No websites available.</p> : (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200">
          {shown.map((w) => (
            <li key={w.id}>
              <label className="flex cursor-pointer items-center gap-3 px-4 py-2.5 hover:bg-slate-50">
                <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-brand-600" checked={sel.has(w.id)} onChange={() => toggle(w.id)} />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-slate-900">{w.name}</span>
                  <span className="block truncate text-xs text-slate-500">{w.client_name} · {w.base_url}</span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      <div className="sticky bottom-0 border-t border-slate-200 bg-white py-3">
        <Button onClick={async () => {
          await run(() => api(`/users/${u.id}/websites`, { method: "PUT", body: { website_ids: [...sel] } }), "Website access saved.");
          load();
        }}>Save website access</Button>
      </div>
    </div>
  );
}
