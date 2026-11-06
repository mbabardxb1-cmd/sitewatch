"use client";

import {
  Check, FileSearch, Gauge, Image as ImageIcon, Layers, Link2, Lock, Play, Search, SlidersHorizontal, Smartphone,
  SquarePen, Wrench, type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { Button, ErrorNote, inputClass } from "@/components/ui";
import { api, type AuditRun, type Website } from "@/lib/api";

export const AUDIT_CATEGORIES: { key: string; label: string; description: string; icon: LucideIcon }[] = [
  { key: "links", label: "Broken links & redirects", description: "Internal and external links, redirect chains and loops, important pages.", icon: Link2 },
  { key: "seo", label: "SEO", description: "Titles, meta descriptions, H1 headings, robots.txt, sitemap and indexing.", icon: FileSearch },
  { key: "images", label: "Images", description: "Broken images and images without alternative text.", icon: ImageIcon },
  { key: "performance", label: "Performance", description: "Slow server responses and oversized pages.", icon: Gauge },
  { key: "security", label: "Security & SSL", description: "Certificate validity, insecure (mixed) content and security headers.", icon: Lock },
  { key: "forms", label: "Forms", description: "Form targets exist, submit securely and have spam protection. Forms are never submitted.", icon: SquarePen },
  { key: "mobile", label: "Mobile & accessibility", description: "Mobile viewport, page language and labelled form fields.", icon: Smartphone },
];
export const CATEGORY_LABEL = Object.fromEntries(AUDIT_CATEGORIES.map((c) => [c.key, c.label]));
const ALL = AUDIT_CATEGORIES.map((c) => c.key);

/** Ready-made audit types. "Custom" is any other combination. */
export const AUDIT_PRESETS: { key: string; label: string; description: string; categories: string[]; icon: LucideIcon }[] = [
  { key: "full", label: "Full audit", description: "Everything: technical, SEO, security, forms and accessibility.", categories: ALL, icon: Layers },
  { key: "technical", label: "Technical audit", description: "Broken links, redirects, speed, SSL & security, forms.",
    categories: ["links", "performance", "security", "forms"], icon: Wrench },
  { key: "seo", label: "SEO audit", description: "Titles, descriptions, headings, sitemap, indexing, links, images, mobile.",
    categories: ["seo", "links", "images", "mobile"], icon: Search },
];

const same = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));

/** "SEO audit", "Technical audit", "Full audit" or "Custom audit" for a run's category list (null = all). */
export function auditKind(categories: string[] | null): string {
  const cats = categories ?? ALL;
  return AUDIT_PRESETS.find((p) => same(p.categories, cats))?.label ?? "Custom audit";
}

export function AuditLauncher({ sites, websiteId, onStarted, onCancel }: {
  sites?: Website[]; websiteId?: string; onStarted: (run: AuditRun) => void; onCancel?: () => void;
}) {
  const [siteId, setSiteId] = useState(websiteId ?? "");
  const [selected, setSelected] = useState<Set<string>>(new Set(AUDIT_CATEGORIES.map((c) => c.key)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const all = selected.size === AUDIT_CATEGORIES.length;
  const chosen = ALL.filter((k) => selected.has(k));
  const preset = AUDIT_PRESETS.find((p) => same(p.categories, chosen));

  function toggle(key: string) {
    const next = new Set(selected);
    if (next.has(key)) next.delete(key); else next.add(key);
    setSelected(next);
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const body = all ? undefined : { categories: AUDIT_CATEGORIES.map((c) => c.key).filter((k) => selected.has(k)) };
      onStarted(await api<AuditRun>(`/websites/${siteId}/audits`, { method: "POST", body }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <ErrorNote message={error} />
      {!websiteId && (
        <label className="block max-w-md space-y-1.5 text-sm">
          <span className="font-medium text-slate-700">Website</span>
          <select className={inputClass} value={siteId} onChange={(e) => setSiteId(e.target.value)}>
            <option value="">Select a website…</option>
            {(sites ?? []).map((s) => <option key={s.id} value={s.id}>{s.name} — {s.base_url}</option>)}
          </select>
        </label>
      )}
      <div>
        <span className="mb-2 block text-sm font-medium text-slate-700">Audit type</span>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {AUDIT_PRESETS.map(({ key, label, description, categories, icon: Icon }) => {
            const on = preset?.key === key;
            return (
              <button key={key} type="button" aria-pressed={on} onClick={() => setSelected(new Set(categories))}
                      className={`flex items-start gap-3 rounded-xl border p-3.5 text-left transition ${
                        on ? "border-brand-500 bg-brand-600 text-white shadow-sm" : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"}`}>
                <Icon size={20} className={`mt-0.5 shrink-0 ${on ? "text-white" : "text-brand-600"}`} />
                <span>
                  <span className="block text-sm font-semibold">{label}</span>
                  <span className={`mt-0.5 block text-xs leading-relaxed ${on ? "text-brand-100" : "text-slate-500"}`}>{description}</span>
                </span>
              </button>
            );
          })}
          <div className={`flex items-start gap-3 rounded-xl border p-3.5 ${
            !preset ? "border-brand-500 bg-brand-600 text-white shadow-sm" : "border-dashed border-slate-300 text-slate-500"}`}>
            <SlidersHorizontal size={20} className={`mt-0.5 shrink-0 ${!preset ? "text-white" : "text-slate-400"}`} />
            <span>
              <span className="block text-sm font-semibold">Custom audit</span>
              <span className={`mt-0.5 block text-xs leading-relaxed ${!preset ? "text-brand-100" : ""}`}>Pick the areas below.</span>
            </span>
          </div>
        </div>
      </div>
      <div>
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-slate-700">What will be checked</span>
          <button type="button" className="text-sm font-medium text-brand-600 hover:text-brand-500"
                  onClick={() => setSelected(all ? new Set() : new Set(AUDIT_CATEGORIES.map((c) => c.key)))}>
            {all ? "Clear all" : "Select all"}
          </button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {AUDIT_CATEGORIES.map(({ key, label, description, icon: Icon }) => {
            const on = selected.has(key);
            return (
              <button key={key} type="button" onClick={() => toggle(key)} aria-pressed={on}
                      className={`relative flex items-start gap-3 rounded-xl border p-3.5 text-left transition ${
                        on ? "border-brand-500 bg-brand-50/60 ring-1 ring-brand-500" : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"}`}>
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${on ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-500"}`}>
                  <Icon size={18} />
                </span>
                <span className="min-w-0 pr-5">
                  <span className="block text-sm font-semibold text-slate-900">{label}</span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">{description}</span>
                </span>
                <span className={`absolute right-3 top-3 flex h-5 w-5 items-center justify-center rounded-full border ${
                  on ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300 bg-white"}`}>
                  {on && <Check size={12} strokeWidth={3} />}
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={start} disabled={!siteId || selected.size === 0 || busy}>
          <Play size={16} />{busy ? "Starting…" : preset ? `Start ${preset.label}`
            : `Start custom audit (${selected.size} of ${AUDIT_CATEGORIES.length} areas)`}
        </Button>
        {onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}
        <span className="text-xs text-slate-500">The site is crawled politely in the background; you can leave this page.</span>
      </div>
    </div>
  );
}
