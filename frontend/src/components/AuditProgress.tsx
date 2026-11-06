"use client";

import { Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { CATEGORY_LABEL } from "@/components/AuditLauncher";
import { fmtEta, type AuditRun } from "@/lib/api";

/** Circular progress indicator. `percent` null = indeterminate (spinning arc). */
export function ProgressRing({ percent, size = 96, stroke = 8, children }: {
  percent: number | null; size?: number; stroke?: number; children?: React.ReactNode;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const p = percent === null ? 25 : Math.max(0, Math.min(100, percent));
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className={`-rotate-90 ${percent === null ? "animate-spin" : ""}`} aria-hidden>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeWidth={stroke} className="text-slate-100" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="url(#sw-ring)" strokeWidth={stroke} strokeLinecap="round"
                strokeDasharray={c} strokeDashoffset={c * (1 - p / 100)}
                style={{ transition: "stroke-dashoffset 1s linear" }} />
        <defs>
          <linearGradient id="sw-ring" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="rgb(129 140 248)" /><stop offset="100%" stopColor="rgb(79 70 229)" />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">{children}</div>
    </div>
  );
}

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

/**
 * Progress shown to the user. The server reports progress every few seconds; between updates
 * the ring keeps moving smoothly along the time estimate, but never past what is plausible
 * (it stops at 98 % until the audit has actually finished).
 */
export function estimate(run: AuditRun, now: number) {
  if (run.status === "queued") return { percent: null as number | null, remaining: null as number | null, label: "Waiting for a worker…" };
  const p = run.progress?.percent ?? 0;
  const started = run.started_at ? new Date(run.started_at).getTime() : now;
  const elapsed = Math.max(0, (now - started) / 1000);
  const expected = run.progress?.expected_s ?? null;
  let total: number | null = null;
  if (p >= 8 && elapsed > 5) {
    const fromRate = elapsed / (p / 100);
    total = expected ? (p / 100) * fromRate + (1 - p / 100) * expected : fromRate;
  } else if (expected) {
    total = expected;
  }
  const remaining = total === null ? null : Math.max(0, total - elapsed);
  const timeBased = total ? (elapsed / total) * 100 : 0;
  const shown = Math.min(98, Math.max(p, Math.min(timeBased, p + 15)));
  return { percent: shown, remaining, label: run.progress?.phase || "Starting" };
}

export function AuditProgressCard({ run }: { run: AuditRun }) {
  const now = useNow();
  const { percent, remaining, label } = estimate(run, now);
  return (
    <div className="flex items-center gap-5 rounded-xl border border-brand-100 bg-gradient-to-br from-white to-brand-50/60 p-4 shadow-card">
      <ProgressRing percent={percent}>
        {percent === null
          ? <Loader2 className="animate-spin text-brand-500" size={20} />
          : <span className="text-lg font-semibold tabular-nums text-slate-900">{Math.floor(percent)}%</span>}
      </ProgressRing>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-x-2">
          <Link href={`/audits/${run.id}`} className="truncate font-semibold text-slate-900 hover:underline">{run.website_name}</Link>
          <span className="inline-flex items-center gap-1 rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand-500" />{run.status === "queued" ? "Queued" : "Running"}
          </span>
        </div>
        <p className="text-sm text-slate-600">{label}</p>
        <p className="text-sm text-slate-500">
          {run.status === "queued" ? "Starts in a moment" :
            remaining === null ? "Estimating time remaining…" :
            remaining < 10 ? "Almost done…" : `About ${fmtEta(remaining)} remaining`}
          {(run.progress?.pages_crawled ?? 0) > 0 && <> · {run.progress.pages_crawled} pages checked</>}
        </p>
        {run.categories && <p className="text-xs text-slate-400">Checking: {run.categories.map((c) => CATEGORY_LABEL[c] ?? c).join(", ")}</p>}
      </div>
    </div>
  );
}
