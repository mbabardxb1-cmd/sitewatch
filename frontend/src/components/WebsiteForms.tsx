"use client";

import { useState } from "react";
import { Button, ErrorNote, Field, inputClass } from "@/components/ui";
import type { CrawlConfig } from "@/lib/api";

export type MonitoringValues = {
  name: string;
  base_url: string;
  check_interval_s: number;
  retry_attempts: number;
  recovery_threshold: number;
  timeout_s: number;
  degraded_threshold_ms: number;
  expected_status_codes: number[];
  audit_day_of_month: number;
  ssl_monitoring: boolean;
  notification_recipients: string[];
};

export const DEFAULT_MONITORING: MonitoringValues = {
  name: "",
  base_url: "",
  check_interval_s: 300,
  retry_attempts: 2,
  recovery_threshold: 1,
  timeout_s: 15,
  degraded_threshold_ms: 3000,
  expected_status_codes: [200],
  audit_day_of_month: 1,
  ssl_monitoring: true,
  notification_recipients: [],
};

const splitList = (s: string) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

export function MonitoringFields({ v, set, disabled }: {
  v: MonitoringValues; set: (v: MonitoringValues) => void; disabled?: boolean;
}) {
  const [codes, setCodes] = useState(v.expected_status_codes.join(", "));
  const [recips, setRecips] = useState(v.notification_recipients.join(", "));
  const num = (k: keyof MonitoringValues) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    set({ ...v, [k]: Number(e.target.value) });

  return (
    <fieldset disabled={disabled} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Field label="Website name">
        <input className={inputClass} required maxLength={200} value={v.name} onChange={(e) => set({ ...v, name: e.target.value })} />
      </Field>
      <Field label="URL" hint="http:// or https://. Internal and private addresses are refused.">
        <input className={inputClass} required placeholder="https://www.example.com" value={v.base_url}
               onChange={(e) => set({ ...v, base_url: e.target.value })} />
      </Field>
      <Field label="Check interval">
        <select className={inputClass} value={v.check_interval_s} onChange={num("check_interval_s")}>
          {[1, 2, 3, 4, 5].map((m) => <option key={m} value={m * 60}>Every {m} minute{m > 1 ? "s" : ""}</option>)}
        </select>
      </Field>
      <Field label="Retries before outage" hint="Failed checks are retried this many times before an outage is confirmed.">
        <input className={inputClass} type="number" min={0} max={10} value={v.retry_attempts} onChange={num("retry_attempts")} />
      </Field>
      <Field label="Successes to recover" hint="Consecutive successful checks needed to close an outage.">
        <input className={inputClass} type="number" min={1} max={10} value={v.recovery_threshold} onChange={num("recovery_threshold")} />
      </Field>
      <Field label="Request timeout (s)">
        <input className={inputClass} type="number" min={2} max={60} value={v.timeout_s} onChange={num("timeout_s")} />
      </Field>
      <Field label="Degraded above (ms)" hint="Responses slower than this are flagged as degraded.">
        <input className={inputClass} type="number" min={100} max={60000} step={100} value={v.degraded_threshold_ms}
               onChange={num("degraded_threshold_ms")} />
      </Field>
      <Field label="Expected status codes" hint="Comma-separated, e.g. 200, 301">
        <input className={inputClass} value={codes} onChange={(e) => {
          setCodes(e.target.value);
          set({ ...v, expected_status_codes: splitList(e.target.value).map(Number).filter((n) => !Number.isNaN(n)) });
        }} />
      </Field>
      <Field label="Monthly audit day" hint="Day 1–28 of each month">
        <input className={inputClass} type="number" min={1} max={28} value={v.audit_day_of_month} onChange={num("audit_day_of_month")} />
      </Field>
      <Field label="Alert recipients" hint="Comma-separated email addresses">
        <input className={inputClass} value={recips} onChange={(e) => {
          setRecips(e.target.value);
          set({ ...v, notification_recipients: splitList(e.target.value) });
        }} />
      </Field>
      <label className="flex items-center gap-2 self-end text-sm">
        <input type="checkbox" checked={v.ssl_monitoring} onChange={(e) => set({ ...v, ssl_monitoring: e.target.checked })} />
        Monitor SSL certificate
      </label>
    </fieldset>
  );
}

export function CrawlConfigForm({ initial, onSave, disabled }: {
  initial: CrawlConfig; onSave: (c: CrawlConfig) => Promise<void>; disabled?: boolean;
}) {
  const [c, setC] = useState(initial);
  const [patterns, setPatterns] = useState(initial.excluded_patterns.join("\n"));
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const num = (k: keyof CrawlConfig) => (e: React.ChangeEvent<HTMLInputElement>) => setC({ ...c, [k]: Number(e.target.value) });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      await onSave({ ...c, excluded_patterns: patterns.split("\n").map((p) => p.trim()).filter(Boolean) });
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <ErrorNote message={error} />
      <fieldset disabled={disabled} className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Field label="Max crawl depth" hint="Links followed from the homepage (0–10)">
          <input className={inputClass} type="number" min={0} max={10} value={c.max_depth} onChange={num("max_depth")} />
        </Field>
        <Field label="Max pages" hint="1–10,000">
          <input className={inputClass} type="number" min={1} max={10000} value={c.max_pages} onChange={num("max_pages")} />
        </Field>
        <Field label="Page timeout (s)">
          <input className={inputClass} type="number" min={1} max={60} value={c.timeout_s} onChange={num("timeout_s")} />
        </Field>
        <Field label="Max requests per second" hint="Keeps the crawler gentle on client servers (0.1–10)">
          <input className={inputClass} type="number" min={0.1} max={10} step={0.1} value={c.rate_rps} onChange={num("rate_rps")} />
        </Field>
        <Field label="Slow page threshold (ms)">
          <input className={inputClass} type="number" min={100} max={60000} step={100} value={c.slow_page_ms} onChange={num("slow_page_ms")} />
        </Field>
        <div className="space-y-2 self-end text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={c.check_external_links}
                   onChange={(e) => setC({ ...c, check_external_links: e.target.checked })} />
            Check external links
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={c.render_js} onChange={(e) => setC({ ...c, render_js: e.target.checked })} />
            Render JavaScript (slower)
          </label>
        </div>
        <div className="sm:col-span-2 lg:col-span-3">
          <Field label="Excluded URLs" hint="One per line. Glob such as /wp-admin/* or a regular expression prefixed with re:">
            <textarea className={`${inputClass} font-mono`} rows={4} value={patterns} onChange={(e) => setPatterns(e.target.value)} />
          </Field>
        </div>
      </fieldset>
      {!disabled && (
        <div className="flex items-center gap-3">
          <Button type="submit">Save crawl settings</Button>
          {saved && <span className="text-sm text-emerald-700">Saved</span>}
        </div>
      )}
    </form>
  );
}
