"use client";

import { Bell } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, Field, inputClass, PageHeader } from "@/components/ui";
import { api, fmtDate, type ClientOrg, type NotificationLogRow, type NotificationRule, type Website } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type EventDef = { key: string; label: string };

const STATUS_STYLE: Record<string, string> = {
  sent: "text-emerald-700", failed: "text-red-700 font-medium", suppressed: "text-slate-500", pending: "text-amber-700",
};

export default function NotificationsPage() {
  const { can } = useAuth();
  const manage = can("notification.manage");
  const [events, setEvents] = useState<EventDef[]>([]);
  const [rules, setRules] = useState<NotificationRule[]>([]);
  const [logs, setLogs] = useState<NotificationLogRow[]>([]);
  const [sites, setSites] = useState<Website[]>([]);
  const [clients, setClients] = useState<ClientOrg[]>([]);
  const [form, setForm] = useState({ event_type: "website_down", scope: "global", target: "", recipients: "", min_interval_s: 0 });
  const [testTo, setTestTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api<EventDef[]>("/notification-events").then(setEvents).catch((e) => setError(e.message));
    api<NotificationLogRow[]>("/notification-logs?limit=200").then(setLogs).catch((e) => setError(e.message));
    api<Website[]>("/websites").then(setSites).catch(() => undefined);
    if (manage) {
      api<NotificationRule[]>("/notification-rules").then(setRules).catch((e) => setError(e.message));
      api<ClientOrg[]>("/clients").then(setClients).catch(() => undefined);
    }
  }, [manage]);
  useEffect(load, [load]);

  const label = (k: string) => events.find((e) => e.key === k)?.label ?? k;
  const siteName = (id: string | null) => sites.find((s) => s.id === id)?.name;
  const scopeLabel = (r: NotificationRule) =>
    r.website_id ? `Website: ${siteName(r.website_id) ?? "…"}` : r.client_id ? `Client: ${clients.find((c) => c.id === r.client_id)?.name ?? "…"}` : "All websites";

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setError(null);
    setNotice(null);
    try {
      await fn();
      if (ok) setNotice(ok);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  function createRule(e: React.FormEvent) {
    e.preventDefault();
    run(() => api("/notification-rules", {
      method: "POST",
      body: {
        event_type: form.event_type,
        website_id: form.scope === "website" ? form.target : null,
        client_id: form.scope === "client" ? form.target : null,
        recipients: form.recipients.split(/[\s,]+/).filter(Boolean),
        min_interval_s: Number(form.min_interval_s),
      },
    }), "Rule created.");
  }

  async function sendTest(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    try {
      const r = await api<{ ok: boolean; error: string | null }>("/notifications/test", { method: "POST", body: { recipient: testTo } });
      if (r.ok) setNotice(`Test email sent to ${testTo}.`); else setError(`Test email failed: ${r.error}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Notifications" icon={<Bell size={20} />} />
      <ErrorNote message={error} />
      {notice && <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-900">{notice}</p>}
      <p className="text-sm text-slate-600">
        Each website&apos;s own alert recipients (set on the website page) receive its alerts. Rules below add recipients
        for all websites, a client, or one website. A disabled website rule mutes that event for the website.
        Each outage produces one “down” and one “recovered” email — never repeats.
      </p>

      {manage && (
        <Card title="Add rule">
          <form onSubmit={createRule} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="Event">
              <select className={inputClass} value={form.event_type} onChange={(e) => setForm({ ...form, event_type: e.target.value })}>
                {events.map((ev) => <option key={ev.key} value={ev.key}>{ev.label}</option>)}
              </select>
            </Field>
            <Field label="Applies to">
              <select className={inputClass} value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value, target: "" })}>
                <option value="global">All websites</option><option value="client">One client</option><option value="website">One website</option>
              </select>
            </Field>
            {form.scope !== "global" ? (
              <Field label={form.scope === "client" ? "Client" : "Website"}>
                <select className={inputClass} required value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })}>
                  <option value="">Select…</option>
                  {(form.scope === "client" ? clients : sites).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </select>
              </Field>
            ) : <div className="hidden lg:block" />}
            <Field label="Recipients" hint="Comma-separated emails">
              <input className={inputClass} value={form.recipients} onChange={(e) => setForm({ ...form, recipients: e.target.value })} />
            </Field>
            <Field label="Min. interval (s)" hint="0 = no extra limit">
              <input className={inputClass} type="number" min={0} value={form.min_interval_s}
                     onChange={(e) => setForm({ ...form, min_interval_s: Number(e.target.value) })} />
            </Field>
            <div className="sm:col-span-2 lg:col-span-5"><Button type="submit">Add rule</Button></div>
          </form>
        </Card>
      )}

      {manage && (
        <Card title={`Rules (${rules.length})`}>
          {rules.length === 0 ? <p className="text-sm text-slate-500">No rules. Alerts go to each website&apos;s own recipients.</p> : (
            <div className="overflow-x-auto">
              <table className="sw-table">
                <thead className="text-xs uppercase text-slate-500">
                  <tr><th className="py-2 pr-4">Event</th><th className="pr-4">Scope</th><th className="pr-4">Recipients</th>
                    <th className="pr-4">Enabled</th><th /></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {rules.map((r) => (
                    <tr key={r.id}>
                      <td className="py-2 pr-4">{label(r.event_type)}</td>
                      <td className="pr-4">{scopeLabel(r)}</td>
                      <td className="pr-4">{r.recipients.join(", ") || "—"}</td>
                      <td className="pr-4">
                        <input type="checkbox" checked={r.enabled} aria-label="Enabled"
                               onChange={(e) => run(() => api(`/notification-rules/${r.id}`, { method: "PATCH", body: { enabled: e.target.checked } }))} />
                      </td>
                      <td className="text-right">
                        <Button variant="danger" onClick={() => run(() => api(`/notification-rules/${r.id}`, { method: "DELETE" }))}>Delete</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      {manage && (
        <Card title="Test email delivery">
          <form onSubmit={sendTest} className="flex flex-wrap items-end gap-3">
            <Field label="Send a test message to">
              <input className={inputClass} type="email" required value={testTo} onChange={(e) => setTestTo(e.target.value)} />
            </Field>
            <Button type="submit">Send test</Button>
          </form>
        </Card>
      )}

      <Card title="Delivery log">
        {logs.length === 0 ? <p className="text-sm text-slate-500">No notifications yet.</p> : (
          <div className="overflow-x-auto">
            <table className="sw-table">
              <thead className="text-xs uppercase text-slate-500">
                <tr><th className="py-2 pr-4">Time</th><th className="pr-4">Event</th><th className="pr-4">Subject</th>
                  <th className="hidden pr-4 md:table-cell">Recipients</th><th className="pr-4">Status</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {logs.map((l) => (
                  <tr key={l.id} className="align-top">
                    <td className="py-2 pr-4 whitespace-nowrap">{fmtDate(l.created_at)}</td>
                    <td className="pr-4">{label(l.event_type)}</td>
                    <td className="pr-4">{l.subject}</td>
                    <td className="hidden pr-4 md:table-cell">{l.recipients.join(", ") || "—"}</td>
                    <td className={`pr-4 capitalize ${STATUS_STYLE[l.status]}`}>
                      {l.status}{l.error && <div className="text-xs normal-case text-slate-500">{l.error}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
