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
