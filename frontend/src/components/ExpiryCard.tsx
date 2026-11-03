"use client";

import { useCallback, useEffect, useState } from "react";
import { ExpiryCell } from "@/components/ExpiryBits";
import { Button, Card, ErrorNote, Field, inputClass } from "@/components/ui";
import { ApiError, api, fmtDate, fmtDay, type DomainView, type HostingView, type WebsiteExpiry } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export function ExpiryCard({ websiteId }: { websiteId: string }) {
  const { can, me } = useAuth();
  const isClient = me?.roles.includes("client");
  const write = can("expiry.write");
  const [data, setData] = useState<WebsiteExpiry | null>(null);
  const [hidden, setHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    api<WebsiteExpiry>(`/websites/${websiteId}/expiry`).then(setData).catch((e) => {
      if (e instanceof ApiError && e.status === 403) setHidden(true); else setError(e.message);
    });
  }, [websiteId]);
  useEffect(load, [load]);

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

  if (hidden || !can("expiry.read")) return null;
  if (!data) return <Card title="Domain, hosting & SSL"><p className="text-sm text-slate-500">{error ?? "Loading…"}</p></Card>;
  const { domain, hosting, ssl } = data;

  return (
    <div id="expiry">
    <Card title="Domain, hosting & SSL">
      <ErrorNote message={error} />
      {notice && <p className="mb-3 rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-900">{notice}</p>}
      <div className="grid gap-6 lg:grid-cols-3">
        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Domain{domain.domain ? ` · ${domain.domain}` : ""}</h3>
          <ExpiryCell e={domain.effective} />
          {domain.domain && <p className="text-xs text-slate-600">Registrar: {domain.registrar ?? "unknown"}</p>}
          {!isClient && <DomainDetails d={domain} />}
          {write && (
            <>
              <Button variant="secondary" onClick={() => run(() => api(`/websites/${websiteId}/domain/refresh`, { method: "POST" }), "Lookup finished.")}>
                Look up now
              </Button>
              <DomainForm d={domain} onSave={(body) => run(() => api(`/websites/${websiteId}/domain`, { method: "PUT", body }), "Saved.")} />
            </>
          )}
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Hosting{hosting.provider ? ` · ${hosting.provider}` : ""}</h3>
          <ExpiryCell e={hosting.effective} />
          {!isClient && hosting.plan && <p className="text-xs text-slate-600">Plan: {hosting.plan}</p>}
          {!isClient && hosting.entered_by && <p className="text-xs text-slate-500">Entered by {hosting.entered_by}</p>}
          {!isClient && hosting.integration && (
            <p className="text-xs text-slate-600">Provider API: {hosting.integration.name}
              {write && <> · <button className="underline" onClick={() => run(() => api(`/websites/${websiteId}/hosting/refresh`, { method: "POST" }), "Verified with provider.")}>verify now</button></>}
            </p>
          )}
          {write && <HostingForm h={hosting} onSave={(body) => run(() => api(`/websites/${websiteId}/hosting`, { method: "PUT", body }), "Saved.")} />}
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">SSL certificate</h3>
          {!ssl.applicable ? <p className="text-sm text-slate-500">{ssl.reason}</p> : (
            <>
              <ExpiryCell e={ssl.effective} invalid={ssl.valid === false} />
              {ssl.issuer && <p className="text-xs text-slate-600">Issuer: {ssl.issuer}</p>}
              {ssl.last_attempt?.check_error && (
                <p className="text-xs text-amber-800">Latest check could not connect ({fmtDate(ssl.last_attempt.checked_at)}): {ssl.last_attempt.check_error}</p>
              )}
              {!isClient && ssl.san && ssl.san.length > 0 && <p className="break-all text-xs text-slate-500">Covers: {ssl.san.slice(0, 6).join(", ")}{ssl.san.length > 6 ? "…" : ""}</p>}
              {write && <Button variant="secondary" onClick={() => run(() => api(`/websites/${websiteId}/ssl/refresh`, { method: "POST" }), "Certificate checked.")}>Check now</Button>}
              {!isClient && ssl.history && ssl.history.length > 1 && (
                <details className="text-xs text-slate-600">
                  <summary className="cursor-pointer">Check history</summary>
                  <ul className="mt-1 space-y-0.5">
                    {ssl.history.map((h) => (
                      <li key={h.checked_at}>{fmtDate(h.checked_at)} — {h.check_error ? `could not check (${h.check_error})` : `${h.valid ? "valid" : "NOT valid"}, expires ${fmtDay(h.not_after)}`}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
        </section>
      </div>
    </Card>
    </div>
  );
}

function DomainDetails({ d }: { d: DomainView }) {
  const l = d.lookup;
  return (
    <div className="space-y-0.5 text-xs text-slate-500">
      {d.nameservers && d.nameservers.length > 0 && <p>Nameservers: {d.nameservers.join(", ")}</p>}
      {l?.last_checked_at && <p>Last lookup {fmtDate(l.last_checked_at)}: {l.status === "ok" ? "succeeded" : l.last_error}</p>}
      {!l && <p>Not looked up yet (runs automatically within the hour).</p>}
      {d.manual && <p>Manual entry: {fmtDay(d.manual.expires_at)} by {d.manual.entered_by ?? "unknown"} on {fmtDate(d.manual.entered_at)}</p>}
      {d.domain_overridden && <p>Domain set manually (not derived from the website URL).</p>}
    </div>
  );
}

function DomainForm({ d, onSave }: { d: DomainView; onSave: (body: object) => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ domain: d.domain_overridden ? (d.domain ?? "") : "", date: d.manual?.expires_at?.slice(0, 10) ?? "", registrar: d.manual?.registrar ?? "" });
  if (!open) return <button className="block text-xs text-slate-600 underline" onClick={() => setOpen(true)}>Enter details manually</button>;
  return (
    <form className="space-y-2 rounded-md bg-slate-50 p-3" onSubmit={(e) => {
      e.preventDefault();
      onSave({ domain: f.domain, manual_expires_at: f.date || null, manual_registrar: f.registrar });
    }}>
      <Field label="Domain to look up" hint="Leave empty to derive it from the website URL.">
        <input className={inputClass} placeholder={d.domain ?? "example.com"} value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })} />
      </Field>
      <Field label="Manual expiry date" hint="Used only while no registry lookup has succeeded; always labelled Manual.">
        <input className={inputClass} type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} />
      </Field>
      <Field label="Registrar">
        <input className={inputClass} value={f.registrar} onChange={(e) => setF({ ...f, registrar: e.target.value })} />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit">Save</Button>
        {d.manual && <Button type="button" variant="danger" onClick={() => onSave({ clear_manual: true })}>Remove manual entry</Button>}
        <Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    </form>
  );
}

function HostingForm({ h, onSave }: { h: HostingView; onSave: (body: object) => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({
    provider: h.provider ?? "", plan: h.plan ?? "", account_reference: h.account_reference ?? "",
    renewal_date: h.renewal_date ?? "", internal_notes: h.internal_notes ?? "",
  });
  if (!open) return <button className="block text-xs text-slate-600 underline" onClick={() => setOpen(true)}>Edit hosting details</button>;
  return (
    <form className="space-y-2 rounded-md bg-slate-50 p-3" onSubmit={(e) => {
      e.preventDefault();
      const changedDate = f.renewal_date !== (h.renewal_date ?? "");
      onSave({
        provider: f.provider, plan: f.plan, account_reference: f.account_reference, internal_notes: f.internal_notes,
        ...(changedDate ? (f.renewal_date ? { renewal_date: f.renewal_date } : { clear_renewal_date: true }) : {}),
      });
      setOpen(false);
    }}>
      <Field label="Provider"><input className={inputClass} value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} /></Field>
      <Field label="Plan"><input className={inputClass} value={f.plan} onChange={(e) => setF({ ...f, plan: e.target.value })} /></Field>
      <Field label="Account reference" hint="Never shown to client users."><input className={inputClass} value={f.account_reference} onChange={(e) => setF({ ...f, account_reference: e.target.value })} /></Field>
      <Field label="Renewal date" hint="Saved as a manual entry (unverified)."><input className={inputClass} type="date" value={f.renewal_date} onChange={(e) => setF({ ...f, renewal_date: e.target.value })} /></Field>
      <Field label="Internal notes" hint="Staff only. Do not store passwords here.">
        <textarea className={inputClass} rows={2} value={f.internal_notes} onChange={(e) => setF({ ...f, internal_notes: e.target.value })} />
      </Field>
      <div className="flex gap-2"><Button type="submit">Save</Button><Button type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Button></div>
    </form>
  );
}
