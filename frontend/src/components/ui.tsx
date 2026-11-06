import type { ReactNode } from "react";
import type { Severity } from "@/lib/api";

export type Status = "healthy" | "warning" | "critical" | "unknown";

const STATUS_STYLE: Record<Status, [string, string]> = {
  healthy: ["bg-emerald-50 text-emerald-700 ring-emerald-600/20", "bg-emerald-500"],
  warning: ["bg-amber-50 text-amber-800 ring-amber-600/25", "bg-amber-500"],
  critical: ["bg-red-50 text-red-700 ring-red-600/20", "bg-red-500"],
  unknown: ["bg-slate-100 text-slate-600 ring-slate-500/15", "bg-slate-400"],
};
const STATUS_LABEL: Record<Status, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  unknown: "Unknown",
};

export function StatusBadge({ status }: { status: Status }) {
  const [style, dot] = STATUS_STYLE[status];
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-red-600 text-white ring-red-700/20",
  high: "bg-orange-50 text-orange-700 ring-orange-600/25",
  medium: "bg-amber-50 text-amber-800 ring-amber-600/25",
  low: "bg-sky-50 text-sky-700 ring-sky-600/20",
  info: "bg-slate-100 text-slate-600 ring-slate-500/15",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const label = severity === "info" ? "Info" : severity[0].toUpperCase() + severity.slice(1);
  return (
    <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${SEVERITY_STYLE[severity]}`}>
      {label}
    </span>
  );
}

export function Card({ title, description, children, actions, className = "", flush }: {
  title?: ReactNode; description?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string;
  /** No body padding (for full-bleed tables). */
  flush?: boolean;
}) {
  return (
    <section className={`rounded-xl border border-slate-200/80 bg-white shadow-card ${className}`}>
      {(title || actions) && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={flush ? "" : "p-5"}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions, icon }: {
  title: ReactNode; description?: ReactNode; actions?: ReactNode; icon?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && (
          <span className="mt-0.5 hidden h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600 ring-1 ring-brand-100 sm:flex">
            {icon}
          </span>
        )}
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
          {description && <p className="mt-1 max-w-3xl text-sm text-slate-500">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
const BUTTON_STYLE: Record<Variant, string> = {
  primary: "bg-brand-600 text-white shadow-sm hover:bg-brand-500 focus-visible:outline-brand-600",
  secondary: "bg-white text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50",
  danger: "bg-white text-red-600 shadow-sm ring-1 ring-inset ring-red-200 hover:bg-red-50",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
};

export function buttonClass(variant: Variant = "primary", size: "sm" | "md" = "md") {
  const pad = size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm";
  return `inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${pad} ${BUTTON_STYLE[variant]}`;
}

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  const { variant = "primary", size = "md", className = "", ...rest } = props;
  return <button {...rest} className={`${buttonClass(variant, size)} ${className}`} />;
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-800">{message}</p>;
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "success" | "warning" }) {
  const style = {
    info: "border-brand-200 bg-brand-50 text-brand-900",
    success: "border-emerald-200 bg-emerald-50 text-emerald-900",
    warning: "border-amber-200 bg-amber-50 text-amber-900",
  }[tone];
  return <p role="status" className={`rounded-lg border px-4 py-2.5 text-sm ${style}`}>{children}</p>;
}

export const inputClass =
  "block w-full rounded-lg border-0 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-brand-600 disabled:bg-slate-50 disabled:text-slate-500";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5 text-sm">
      <span className="font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function HealthCell({ status, reason }: { status: Status; reason: string }) {
  return (
    <div className="flex flex-col items-start gap-1">
      <StatusBadge status={status} />
      <span className="text-xs text-slate-500">{reason}</span>
    </div>
  );
}

const TONE: Record<string, string> = {
  brand: "bg-brand-50 text-brand-600",
  green: "bg-emerald-50 text-emerald-600",
  red: "bg-red-50 text-red-600",
  amber: "bg-amber-50 text-amber-600",
  slate: "bg-slate-100 text-slate-500",
};

export function Stat({ label, value, note, icon, tone = "brand" }: {
  label: string; value: ReactNode; note?: string; icon?: ReactNode; tone?: keyof typeof TONE;
}) {
  return (
    <div className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-card">
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs font-medium text-slate-500">{label}</div>
        {icon && <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${TONE[tone]}`}>{icon}</span>}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">{value}</div>
      {note && <div className="mt-1 text-xs text-slate-500">{note}</div>}
    </div>
  );
}

export function NoData({ children = "No data" }: { children?: ReactNode }) {
  return <span className="text-base font-normal text-slate-400">{children}</span>;
}

export function EmptyState({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-4 py-10 text-center">
      {icon && <span className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-slate-400">{icon}</span>}
      <p className="text-sm font-medium text-slate-900">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-slate-500">{children}</div>}
    </div>
  );
}

const COVERAGE: Record<string, [string, string]> = {
  performed: ["Checked", "bg-emerald-50 text-emerald-700 ring-emerald-600/20"],
  partial: ["Partly checked", "bg-amber-50 text-amber-800 ring-amber-600/25"],
  not_checked: ["Not checked", "bg-slate-100 text-slate-600 ring-slate-500/15"],
  not_applicable: ["Not applicable", "bg-white text-slate-500 ring-slate-300"],
};

export function CoverageBadge({ status }: { status: string }) {
  const [label, style] = COVERAGE[status] ?? [status, COVERAGE.not_checked[1]];
  return <span className={`inline-flex whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}>{label}</span>;
}

const CERTAINTY: Record<string, string> = { confirmed: "Confirmed", warning: "Warning", could_not_check: "Could not check" };
export function CertaintyLabel({ c }: { c: string }) {
  return <span className={c === "confirmed" ? "text-slate-700" : c === "warning" ? "text-amber-800" : "text-slate-500 italic"}>{CERTAINTY[c] ?? c}</span>;
}
