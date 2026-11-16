"use client";

import { useState } from "react";
import { Button, Card, ErrorNote, Field, Notice, inputClass } from "@/components/ui";
import { IntervalSlider } from "@/components/WebsiteForms";
import { api, fmtInterval } from "@/lib/api";

/** Apply one uptime-check schedule to every website (e.g. 4 hours for shared hosting). */
export function BulkMonitoringCard() {
  const [interval, setInterval] = useState(14400);
  const [timeout, setTimeoutS] = useState(30);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function applyRecommended() {
    if (!confirm("Apply the recommended shared-hosting settings to every website?\n\nChecks are never made heavier: websites already set to something lighter keep their value.")) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const r = await api<{ changed: number }>("/websites/apply-recommended", { method: "POST" });
      setDone(r.changed ? `Recommended settings applied: ${r.changed} website${r.changed === 1 ? "" : "s"} updated.` : "All websites already use the recommended settings.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!confirm(`Check every website ${fmtInterval(interval)} with a ${timeout}-second timeout?\n\nThis replaces each website's own setting. You can still change individual websites afterwards.`)) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const r = await api<{ updated: number }>("/websites/bulk-monitoring", { method: "POST", body: { check_interval_s: interval, timeout_s: timeout } });
      setDone(`${r.updated} website${r.updated === 1 ? "" : "s"} will now be checked ${fmtInterval(interval)}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Uptime checks for all websites"
          description="Set one schedule for every website at once. Longer intervals put less load on clients' shared hosting.">
      <div className="space-y-4">
        <ErrorNote message={error} />
        {done && <Notice tone="success">{done}</Notice>}
        <div className="rounded-xl border border-brand-200 bg-brand-50/40 p-4">
          <p className="text-sm font-semibold text-slate-900">Recommended for shared hosting</p>
          <ul className="mt-2 grid gap-x-6 gap-y-1 text-sm text-slate-600 sm:grid-cols-2">
            <li>Uptime check every 4 hours (6 a day)</li>
            <li>30-second timeout; slow (degraded) above 5 seconds</li>
            <li>2 retries before an outage is confirmed</li>
            <li>Audits and scans: 1 request per second, up to 200 pages</li>
            <li>Daily automatic malware scan off</li>
            <li>Monthly audit and SSL checks unchanged</li>
          </ul>
          <p className="mt-2 text-xs text-slate-500">Never makes checks heavier: websites already set to something lighter keep their value. New websites start with these settings.</p>
          <Button className="mt-3" onClick={applyRecommended} disabled={busy}>Apply recommended settings to all websites</Button>
        </div>
        <p className="pt-2 text-sm font-medium text-slate-700">Or choose your own interval for every website</p>
        <div className="grid gap-4 lg:grid-cols-3">
          <IntervalSlider value={interval} onChange={setInterval} />
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div className="w-48">
            <Field label="Request timeout (seconds)" hint="Slow shared hosts need 20 to 30 s.">
              <input className={inputClass} type="number" min={2} max={60} value={timeout} onChange={(e) => setTimeoutS(Number(e.target.value))} />
            </Field>
          </div>
          <Button onClick={apply} disabled={busy}>{busy ? "Applying…" : "Apply to all websites"}</Button>
        </div>
        <p className="text-xs text-slate-500">New websites start at every 4 hours with a 30-second timeout. Archived websites are not changed.</p>
      </div>
    </Card>
  );
}
