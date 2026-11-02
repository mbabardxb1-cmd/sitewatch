import type { ReactNode } from "react";

export type Status = "healthy" | "warning" | "critical" | "unknown";
export type Severity = "critical" | "high" | "medium" | "low" | "info";

const STATUS_STYLE: Record<Status, string> = {
  healthy: "bg-emerald-50 text-emerald-800 ring-emerald-600/20",
  warning: "bg-amber-50 text-amber-800 ring-amber-600/20",
  critical: "bg-red-50 text-red-800 ring-red-600/20",
  unknown: "bg-slate-100 text-slate-700 ring-slate-500/20",
};
const STATUS_LABEL: Record<Status, string> = {
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  unknown: "Unknown",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_STYLE[status]}`}>
      {STATUS_LABEL[status]}
    </span>
  );
}

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-amber-200 text-amber-900",
  low: "bg-sky-100 text-sky-900",
  info: "bg-slate-100 text-slate-700",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const label = severity === "info" ? "Informational" : severity[0].toUpperCase() + severity.slice(1);
  return <span className={`rounded px-2 py-0.5 text-xs font-medium ${SEVERITY_STYLE[severity]}`}>{label}</span>;
}

export function Card({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const { variant = "primary", className = "", ...rest } = props;
  const style = {
    primary: "bg-slate-900 text-white hover:bg-slate-700",
    secondary: "bg-white text-slate-900 ring-1 ring-inset ring-slate-300 hover:bg-slate-50",
    danger: "bg-white text-red-700 ring-1 ring-inset ring-red-300 hover:bg-red-50",
  }[variant];
  return (
    <button
      {...rest}
      className={`inline-flex items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50 ${style} ${className}`}
    />
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">{message}</p>;
}

export const inputClass =
  "block w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500";

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="font-medium text-slate-800">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function HealthCell({ status, reason }: { status: Status; reason: string }) {
  return (
    <div className="flex flex-col items-start gap-0.5">
      <StatusBadge status={status} />
      <span className="text-xs text-slate-500">{reason}</span>
    </div>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
      {note && <div className="mt-1 text-xs text-slate-500">{note}</div>}
    </div>
  );
}

export function NoData({ children = "No data" }: { children?: ReactNode }) {
  return <span className="text-base font-normal text-slate-400">{children}</span>;
}
