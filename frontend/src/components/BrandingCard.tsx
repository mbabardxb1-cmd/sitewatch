"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Card, ErrorNote, Field, inputClass } from "@/components/ui";
import { api } from "@/lib/api";

type Branding = { company_name: string | null; accent_color: string | null; footer_text: string | null; has_logo: boolean };

export function BrandingCard() {
  const [b, setB] = useState<Branding | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const load = useCallback(() => { api<Branding>("/settings/branding").then(setB).catch((e) => setError(e.message)); }, []);
  useEffect(load, [load]);
  if (!b) return null;

  async function upload(file: File) {
    setError(null);
    const fd = new FormData();
    fd.append("file", file);
    const csrf = decodeURIComponent(document.cookie.split("; ").find((c) => c.startsWith("sw_csrf="))?.split("=")[1] ?? "");
    const res = await fetch("/api/v1/settings/branding/logo", { method: "POST", body: fd, headers: { "X-CSRF-Token": csrf } });
    if (!res.ok) setError((await res.json().catch(() => ({}))).detail ?? "Upload failed");
    else { setSaved("Logo uploaded."); load(); }
  }

  return (
    <Card title="Report branding">
      <ErrorNote message={error} />
      <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try {
          await api("/settings/branding", { method: "PUT", body: { company_name: b.company_name, accent_color: b.accent_color || null, footer_text: b.footer_text } });
          setSaved("Branding saved. It applies to reports generated from now on.");
        } catch (err) { setError(err instanceof Error ? err.message : "Failed"); }
      }}>
        <Field label="Company name"><input className={inputClass} value={b.company_name ?? ""} onChange={(e) => setB({ ...b, company_name: e.target.value })} /></Field>
        <Field label="Accent colour" hint="Hex, e.g. #1e3a8a"><input className={inputClass} value={b.accent_color ?? ""} onChange={(e) => setB({ ...b, accent_color: e.target.value })} /></Field>
        <Field label="Footer text"><input className={inputClass} value={b.footer_text ?? ""} onChange={(e) => setB({ ...b, footer_text: e.target.value })} /></Field>
        <div className="flex items-end"><Button type="submit">Save branding</Button></div>
      </form>
      <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
        <span>Logo: {b.has_logo ? "uploaded" : "none"}</span>
        <label className="cursor-pointer rounded-md px-3 py-1.5 font-medium ring-1 ring-slate-300 hover:bg-slate-50">
          Upload PNG/JPEG (max 1 MB)
          <input type="file" accept="image/png,image/jpeg" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
        {saved && <span className="text-emerald-700">{saved}</span>}
      </div>
    </Card>
  );
}
