"use client";

import { Eye, Upload } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, Field, Notice, inputClass } from "@/components/ui";
import { api } from "@/lib/api";

type Branding = {
  company_name: string | null; accent_color: string | null; footer_text: string | null;
  report_title: string | null; maintenance_intro: string | null; has_logo: boolean; logo_type: "svg" | "image" | null;
};

export function BrandingCard() {
  const [b, setB] = useState<Branding | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const load = useCallback(() => { api<Branding>("/settings/branding").then(setB).catch((e) => setError(e.message)); }, []);
  useEffect(load, [load]);
  if (!b) return null;

  async function upload(file: File) {
    setError(null);
    setSaved(null);
    const fd = new FormData();
    fd.append("file", file);
    const csrf = decodeURIComponent(document.cookie.split("; ").find((c) => c.startsWith("sw_csrf="))?.split("=")[1] ?? "");
    const res = await fetch("/api/v1/settings/branding/logo", { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf } });
    if (!res.ok) setError((await res.json().catch(() => ({}))).detail ?? "Upload failed");
    else { setSaved("Logo uploaded. Use Preview to see it on a report."); load(); }
  }

  const set = (k: keyof Branding) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setB({ ...b, [k]: e.target.value });

  return (
    <Card title="Report branding" description="Applies to PDF reports generated from now on. Existing reports keep their look.">
      <div className="space-y-4">
        <ErrorNote message={error} />
        {saved && <Notice tone="success">{saved}</Notice>}
        <form className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          setSaved(null);
          try {
            await api("/settings/branding", { method: "PUT", body: {
              company_name: b.company_name, accent_color: b.accent_color || null, footer_text: b.footer_text,
              report_title: b.report_title, maintenance_intro: b.maintenance_intro,
            } });
            setSaved("Branding saved.");
          } catch (err) { setError(err instanceof Error ? err.message : "Failed"); }
        }}>
          <Field label="Company name" hint="Shown instead of a logo if none is uploaded.">
            <input className={inputClass} placeholder="Brande" value={b.company_name ?? ""} onChange={set("company_name")} />
          </Field>
          <Field label="Report title" hint="Large title on the cover page.">
            <input className={inputClass} placeholder="AMC Monthly Report" maxLength={40} value={b.report_title ?? ""} onChange={set("report_title")} />
          </Field>
          <Field label="Company website" hint="Shown on the cover and at the bottom-right of every page.">
            <input className={inputClass} placeholder="www.brande.ae" value={b.footer_text ?? ""} onChange={set("footer_text")} />
          </Field>
          <div className="sm:col-span-2">
            <Field label="Maintenance page introduction" hint="The sentence above the maintenance task table.">
              <input className={inputClass} maxLength={300} value={b.maintenance_intro ?? ""} onChange={set("maintenance_intro")}
                     placeholder="Our Annual Maintenance Contract keeps the website updated, clean and backed up every month." />
            </Field>
          </div>
          <Field label="Chart colour" hint="Hex, e.g. #000000 (default black).">
            <input className={inputClass} placeholder="#000000" value={b.accent_color ?? ""} onChange={set("accent_color")} />
          </Field>
          <div className="flex items-end sm:col-span-2 lg:col-span-3"><Button type="submit">Save branding</Button></div>
        </form>
        <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4 text-sm">
          <span className="text-slate-700">
            Logo: <strong>{b.has_logo ? (b.logo_type === "svg" ? "SVG uploaded" : "image uploaded") : "none"}</strong>
          </span>
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50">
            <Upload size={16} />Upload SVG, PNG or JPEG (max 1 MB)
            <input type="file" accept="image/svg+xml,.svg,image/png,image/jpeg" className="hidden"
                   onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </label>
          <a href="/api/v1/settings/branding/preview" target="_blank" rel="noopener"
             className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50">
            <Eye size={16} />Preview report
          </a>
          <span className="w-full text-xs text-slate-500">
            SVG is best: it stays sharp, and a one-colour logo is drawn white on the black cover and black on the white pages automatically.
          </span>
        </div>
      </div>
    </Card>
  );
}
