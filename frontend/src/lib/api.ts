// Typed fetch wrapper. All requests go to same-origin /api (proxied to FastAPI).
// Authorization is enforced by the backend; the UI only adapts what it shows.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") return undefined;
  return document.cookie
    .split("; ")
    .find((c) => c.startsWith(name + "="))
    ?.split("=")[1];
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const csrf = readCookie("sw_csrf");
    if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);
  }
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: unknown }).detail;
    const msg =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg).join("; ")
          : `Request failed (${res.status})`;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

export type Me = {
  id: string;
  email: string;
  full_name: string;
  roles: string[];
  permissions: string[];
  client_id: string | null;
  csrf_token: string;
};

export type UserRow = {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  client_id: string | null;
  roles: string[];
  last_login_at: string | null;
  created_at: string;
};

export type Health = "healthy" | "warning" | "critical" | "unknown";

export type ClientOrg = {
  id: string;
  name: string;
  slug: string;
  contact_email: string | null;
  is_active: boolean;
  show_expiry_to_client: boolean;
  website_count: number;
  created_at: string;
};

export type CrawlConfig = {
  max_depth: number;
  max_pages: number;
  timeout_s: number;
  rate_rps: number;
  excluded_patterns: string[];
  check_external_links: boolean;
  render_js: boolean;
  slow_page_ms: number;
};

/** Fields every viewer receives. */
export type WebsiteBase = {
  id: string;
  name: string;
  base_url: string;
  client_id: string;
  client_name: string;
  status: "active" | "paused" | "archived";
  current_state: "up" | "down" | "degraded" | "unknown";
  health: Health;
  health_reason: string;
  last_checked_at: string | null;
  last_audit_at: string | null;
};

/** Technical fields: only present for staff users (the API omits them for clients). */
export type WebsiteStaff = WebsiteBase & {
  hostname: string;
  check_interval_s: number;
  retry_attempts: number;
  recovery_threshold: number;
  timeout_s: number;
  degraded_threshold_ms: number;
  expected_status_codes: number[];
  next_check_at: string | null;
  audit_day_of_month: number;
  crawl_config: CrawlConfig;
  ssl_monitoring: boolean;
  notification_recipients: string[];
  assigned_users: { id: string; email: string; full_name: string; role: string }[];
  archived_at: string | null;
  created_at: string;
};

export type Website = WebsiteBase | WebsiteStaff;

export function isStaffView(w: Website): w is WebsiteStaff {
  return "check_interval_s" in w;
}

export type DashboardSummary = {
  total_websites: number;
  online: number;
  offline: number;
  degraded: number;
  unknown: number;
  paused: number;
  avg_response_ms_24h: number | null;
  checks_24h: number;
  monthly_uptime_pct: number | null;
  monthly_uptime_note: string;
  active_incidents: number;
  websites: Website[];
};

export function fmtDate(v: string | null | undefined, empty = "Never"): string {
  return v ? new Date(v).toLocaleString() : empty;
}

export type UptimePeriod = {
  period_start: string;
  period_end: string;
  uptime_pct: number | null;
  monitored_s: number;
  downtime_s: number;
  unknown_s: number;
  coverage_pct: number;
  checks: number;
  incidents: number;
};

export type UptimeSummary = {
  website_id: string;
  day: UptimePeriod;
  week: UptimePeriod;
  month: UptimePeriod;
  calendar_month: UptimePeriod;
  method: string;
};

export type UptimeOverviewRow = {
  website_id: string;
  day: number | null;
  week: number | null;
  month: number | null;
  month_coverage_pct: number;
  avg_response_ms_24h: number | null;
};

export type ResponseBucket = {
  bucket: string;
  checks: number;
  successes: number;
  avg_ms: number | null;
  p95_ms: number | null;
  max_ms: number | null;
};

export type UptimeCheck = {
  id: number;
  checked_at: string;
  ok: boolean;
  degraded: boolean;
  http_status: number | null;
  response_ms: number | null;
  error_type: string | null;
  error_detail: string | null;
  final_url: string | null;
  attempt: number;
  is_retry: boolean;
  is_manual: boolean;
};

export type Incident = {
  id: string;
  website_id: string;
  website_name: string;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  status: "open" | "acknowledged" | "resolved";
  // staff only
  cause_summary?: string;
  acknowledged_by_email?: string | null;
  acknowledged_at?: string | null;
};

export type NotificationRule = {
  id: string;
  event_type: string;
  website_id: string | null;
  client_id: string | null;
  enabled: boolean;
  recipients: string[];
  thresholds: Record<string, unknown>;
  min_interval_s: number;
  created_at: string;
};

export type NotificationLogRow = {
  id: number;
  website_id: string | null;
  event_type: string;
  channel: string;
  recipients: string[];
  subject: string | null;
  status: "pending" | "sent" | "failed" | "suppressed";
  error: string | null;
  created_at: string;
  sent_at: string | null;
};

export function fmtPct(v: number | null | undefined): string | null {
  return v === null || v === undefined ? null : `${v.toFixed(v === 100 ? 0 : 2)} %`;
}

export function fmtDuration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/** Human wording for how much of a period was actually monitored. */
export function fmtCoverage(pct: number): string {
  if (pct >= 99.95) return "the whole period";
  if (pct < 0.1) return "less than 0.1% of the period";
  return `${pct.toFixed(pct < 10 ? 1 : 0)}% of the period`;
}

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export type AuditRun = {
  id: string;
  website_id: string;
  website_name: string;
  trigger: "scheduled" | "manual";
  period: string | null;
  status: "queued" | "running" | "completed" | "partial" | "failed";
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  pages_crawled: number;
  error: string | null;
  summary: {
    by_severity?: Record<Severity, number>;
    by_certainty?: Record<string, number>;
    counts?: { new: number; recurring: number; resolved: number; persisting: number };
    broken_internal_links?: number;
    broken_external_links?: number;
    external_links_checked?: number;
    images_checked?: number;
    requests?: number;
    duration_s?: number;
    robots_blocked?: number;
    excluded?: number;
    stopped_reason?: string | null;
  };
};

export type CoverageRow = { check_key: string; label: string; status: "performed" | "partial" | "not_checked" | "not_applicable"; reason?: string; [k: string]: unknown };

export type AuditDetail = AuditRun & { coverage: CoverageRow[]; config: Partial<CrawlConfig> };

export type AuditFinding = {
  id: string;
  check_key: string;
  check_label: string;
  severity: Severity;
  certainty: "confirmed" | "warning" | "could_not_check";
  title: string;
  url: string | null;
  source_url: string | null;
  detail: Record<string, unknown>;
};

export type CompareIssue = { id: string; title: string; severity: Severity; check_key: string; status: string; resolution_note: string | null };

export type AuditCompare = {
  previous_run_id: string | null;
  previous_finished_at: string | null;
  pages_crawled: { current: number; previous: number | null };
  by_severity: Record<Severity, { current: number; previous: number | null }>;
  new: CompareIssue[];
  recurring: CompareIssue[];
  resolved: CompareIssue[];
  persisting_count: number;
};

export type IssueRow = {
  id: string;
  website_id: string;
  website_name: string;
  category: string;
  title: string;
  severity: Severity;
  status: string;
  first_seen: string;
  resolved_at: string | null;
  // staff only
  check_key?: string;
  certainty?: string;
  url?: string | null;
  detail?: Record<string, unknown>;
  occurrences?: number;
  reopened_count?: number;
  resolution_note?: string | null;
  assignee_email?: string | null;
  comment_count?: number;
};

export type IssueComment = { id: string; author_email: string | null; body: string; created_at: string };

export type ExpiryLevel = "healthy" | "warning" | "critical" | "unknown";
export type EffectiveDate = {
  date: string | null;
  days_left: number | null;
  source: string;
  as_of: string | null;
  note: string | null;
  level: ExpiryLevel;
};

export type ExpiryOverviewRow = {
  website_id: string;
  website_name: string;
  base_url: string;
  client_name: string;
  domain: { domain: string | null; registrar: string | null; lookup_status?: string | null } & EffectiveDate;
  hosting: { provider: string | null } & EffectiveDate;
  ssl: { applicable: boolean; issuer: string | null; valid: boolean | null } & EffectiveDate;
};

export type DomainView = {
  domain: string | null;
  registrar: string | null;
  effective: EffectiveDate;
  domain_overridden?: boolean;
  registrar_url?: string | null;
  nameservers?: string[];
  registered_at?: string | null;
  lookup?: { status: string; source: string | null; expires_at: string | null; verified_at: string | null; last_checked_at: string | null; last_error: string | null } | null;
  manual?: { expires_at: string | null; registrar: string | null; entered_by: string | null; entered_at: string | null } | null;
};

export type HostingView = {
  provider: string | null;
  effective: EffectiveDate;
  plan?: string | null;
  account_reference?: string | null;
  renewal_date?: string | null;
  source?: string | null;
  verified_at?: string | null;
  entered_by?: string | null;
  entered_at?: string | null;
  integration?: { id: string; name: string; provider_key: string } | null;
  internal_notes?: string | null;
};

export type SslView = {
  applicable: boolean;
  reason?: string;
  effective: EffectiveDate;
  valid?: boolean | null;
  issuer?: string | null;
  subject?: string | null;
  san?: string[];
  not_after?: string | null;
  validation_error?: string | null;
  last_attempt?: { checked_at: string; check_error: string | null } | null;
  history?: { checked_at: string; valid: boolean | null; not_after: string | null; issuer: string | null; check_error: string | null }[];
};

export type WebsiteExpiry = { domain: DomainView; hosting: HostingView; ssl: SslView };

export function fmtDay(v: string | null | undefined): string {
  return v ? new Date(v + (v.length === 10 ? "T00:00:00" : "")).toLocaleDateString() : "—";
}
