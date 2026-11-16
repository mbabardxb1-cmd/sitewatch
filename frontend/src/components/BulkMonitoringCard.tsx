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
