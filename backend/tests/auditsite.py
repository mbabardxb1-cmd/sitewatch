"""Configurable local websites for crawler/audit tests."""
import http.server
import threading
import time


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _serve(self, head: bool):
        routes = self.server.routes  # type: ignore[attr-defined]
        self.server.hits.append((self.command, self.path))  # type: ignore[attr-defined]
        r = routes.get(self.path) or routes.get(self.path.split("?")[0])
        if r is not None and "cloak" in r:  # serve something else to Google / visitors from Google
            ua, ref = self.headers.get("User-Agent", "").lower(), self.headers.get("Referer", "").lower()
            if "googlebot" in ua or "google." in ref:
                r = r["cloak"]
        if r is None:
            status, headers, body, delay = 404, {"Content-Type": "text/html"}, b"<title>Not found</title>", 0
        else:
            status, headers, body, delay = r.get("status", 200), dict(r.get("headers", {})), r.get("body", b""), r.get("delay", 0)
            if isinstance(body, str):
                body = body.encode()
            headers.setdefault("Content-Type", r.get("type", "text/html; charset=utf-8"))
        if delay:
            time.sleep(delay)
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._serve(False)

    def do_HEAD(self):  # noqa: N802
        self._serve(True)


class Site:
    def __init__(self, routes: dict | None = None):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.routes = routes or {}
        self.httpd.hits = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def routes(self) -> dict:
        return self.httpd.routes

    @property
    def hits(self) -> list:
        return self.httpd.hits

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


def page(title=None, desc=None, links=(), images=(), robots=None, extra=""):
    head = ""
    if title is not None:
        head += f"<title>{title}</title>"
    if desc is not None:
        head += f'<meta name="description" content="{desc}">'
    if robots:
        head += f'<meta name="robots" content="{robots}">'
    body = "".join(f'<a href="{h}">link</a>' for h in links)
    for src, alt in images:
        body += f'<img src="{src}"' + (f' alt="{alt}"' if alt is not None else "") + ">"
    return {"body": f"<!doctype html><html><head>{head}</head><body>{body}{extra}</body></html>"}


def redirect(to, status=301):
    return {"status": status, "headers": {"Location": to}, "body": b""}


def defect_site(ext_base: str) -> dict:
    """A site with one example of most defects the audit detects."""
    return {
        "/robots.txt": {"type": "text/plain", "body": "User-agent: *\nDisallow: /private/\nSitemap: {BASE}/sitemap.xml\n"},
        "/sitemap.xml": {"type": "application/xml", "body": (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>{BASE}/</loc></url><url><loc>{BASE}/about</loc></url>"
            "<url><loc>{BASE}/gone-in-sitemap</loc></url><url><loc>{BASE}/private/secret</loc></url></urlset>")},
        "/": page("Home", "Welcome", links=["/about", "/missing", "/r1", "/loop-a", "/private/x", "/slow", "/error500",
                                            "/members", f"{ext_base}/ok", f"{ext_base}/dead",
                                            "http://169.254.169.254/latest/meta-data/", "mailto:x@example.com", "#top"],
                  images=[("/logo.png", "Logo"), ("/broken.png", None)]),
        "/about": page("About", None, links=["/deep1", "/team", "/"]),
        "/team": page("About", "Team page", links=["/"]),
        "/deep1": page("Deep 1", "d1", links=["/deep2"]),
        "/deep2": page("Deep 2", "d2", links=["/deep3"]),
        "/deep3": page("Deep 3", "d3"),
        "/missing": {"status": 404, "body": "nope"},
        "/r1": redirect("/r2"),
        "/r2": redirect("/about"),
        "/loop-a": redirect("/loop-b", 302),
        "/loop-b": redirect("/loop-a", 302),
        "/slow": {**page("Slow", "slow page"), "delay": 0.35},
        "/error500": {"status": 500, "body": "boom"},
        "/members": {"status": 403, "body": "forbidden"},
        "/logo.png": {"type": "image/png", "body": b"\x89PNG"},
    }


def materialize(routes: dict, base: str) -> dict:
    out = {}
    for k, v in routes.items():
        v = dict(v)
        if isinstance(v.get("body"), str):
            v["body"] = v["body"].replace("{BASE}", base)
        out[k] = v
    return out
