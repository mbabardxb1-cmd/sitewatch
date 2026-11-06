"""Polite, bounded website crawler.

* Starts at the homepage, follows internal links breadth-first within max_depth/max_pages.
* Obeys robots.txt (agent "SiteWatchMonitor", falling back to "*") including Crawl-delay.
* Rate-limited per host (config rate_rps for the audited site, 1 req/s per external host).
* Every request goes through the SSRF-safe client (connect-time IP checks, re-validated redirects).
* Records what was NOT done (robots-blocked, excluded, over the page limit, external links skipped)
  so the audit can state its coverage honestly.
"""
import fnmatch
import re
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from app.audit.htmlparse import PageFacts, parse_html
from app.audit.sitefiles import (
    MAX_SITEMAP_BYTES,
    MAX_SITEMAP_FILES,
    RobotsInfo,
    SitemapResult,
    parse_robots,
    parse_sitemap,
)
from app.netsafe.http import (
    BlockedTargetError,
    DnsResolutionError,
    TooManyRedirects,
    UnsafeUrlError,
    safe_client,
    safe_fetch,
)

MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_EXTERNAL_CHECKS = 300
MAX_IMAGE_CHECKS = 500
MAX_SITEMAP_URL_CHECKS = 100
MAX_FORM_ACTION_CHECKS = 50
KEPT_HEADERS = ("x-robots-tag", "strict-transport-security", "x-content-type-options", "x-frame-options",
                "content-security-policy", "referrer-policy")

# Weight (percent of total) of each phase, used for the progress indicator.
PHASES = [("robots", "Reading robots.txt and sitemaps", 5), ("pages", "Crawling pages", 60),
          ("unvisited", "Checking deeper links", 5), ("external", "Checking external links", 12),
          ("images", "Checking images", 10), ("sitemap", "Checking sitemap URLs", 4),
          ("forms", "Checking forms", 2), ("important", "Checking important pages", 2)]
EXTERNAL_HOST_INTERVAL_S = 1.0


@dataclass
class FetchOutcome:
    url: str
    status: int | None = None
    final_url: str | None = None
    elapsed_ms: int | None = None
    error_type: str | None = None  # timeout|dns|connect|ssl|blocked|redirect_loop|too_many_redirects|other
    error_detail: str | None = None
    hops: list[tuple[str, int]] = field(default_factory=list)
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def ok(self) -> bool:
        return self.status is not None and self.status < 400

    @property
    def redirects(self) -> int:
        return max(0, len(self.hops) - 1)


@dataclass
class Page:
    url: str
    depth: int
    fetch: FetchOutcome
    facts: PageFacts | None = None  # only for successfully fetched HTML


@dataclass
class CrawlResult:
    base_url: str
    pages: dict[str, Page] = field(default_factory=dict)
    link_sources: dict[str, set[str]] = field(default_factory=dict)  # target -> pages linking to it
    external: dict[str, FetchOutcome] = field(default_factory=dict)
    external_skipped: int = 0
    images: dict[str, FetchOutcome] = field(default_factory=dict)
    image_sources: dict[str, set[str]] = field(default_factory=dict)
    images_skipped: int = 0
    robots: RobotsInfo | None = None
    robots_blocked: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    not_crawled_limit: set[str] = field(default_factory=set)
    stopped_reason: str | None = None
    sitemaps: list[SitemapResult] = field(default_factory=list)
    sitemap_urls: list[str] = field(default_factory=list)
    sitemap_checks: dict[str, FetchOutcome] = field(default_factory=dict)
    important: dict[str, FetchOutcome] = field(default_factory=dict)
    form_actions: dict[str, FetchOutcome] = field(default_factory=dict)
    requests_made: int = 0
    duration_s: float = 0.0


def site_key(url: str) -> tuple[str, int | None]:
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = p.port if p.port not in (80, 443) else None
    return host, port


def compile_exclusions(patterns: list[str]):
    compiled = []
    for p in patterns:
        if p.startswith("re:"):
            compiled.append(re.compile(p[3:]))
        else:
            compiled.append(p)
    def excluded(url: str) -> bool:
        parts = urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        for c in compiled:
            if isinstance(c, re.Pattern):
                if c.search(target) or c.search(url):
                    return True
            elif fnmatch.fnmatchcase(target, c) or fnmatch.fnmatchcase(url, c):
                return True
        return False
    return excluded


class Crawler:
    def __init__(self, base_url: str, config: dict, important_urls: list[str] | None = None,
                 sleep=time.sleep, monotonic=time.monotonic, categories: set[str] | None = None, progress=None):
        self.base_url = base_url
        self.cfg = config
        self.important_urls = important_urls or []
        self.key = site_key(base_url)
        self.excluded = compile_exclusions(config.get("excluded_patterns", []))
        self.timeout = float(config.get("timeout_s", 15))
        self.min_interval = 1.0 / max(0.1, float(config.get("rate_rps", 2.0)))
        self._sleep, self._now = sleep, monotonic
        self._last_request: dict[tuple, float] = {}
        self.result = CrawlResult(base_url=base_url)
        self.client: httpx.Client | None = None
        # None = every category. Categories decide which (slow) verification phases run.
        self.categories = categories
        self._progress_cb = progress
        self._phase = "robots"

    def wants(self, category: str) -> bool:
        return self.categories is None or category in self.categories

    def progress(self, phase: str | None = None, fraction: float = 0.0) -> None:
        """Report overall completion (0-100) to the optional callback; never lets it break a crawl."""
        if phase:
            self._phase = phase
        if self._progress_cb is None:
            return
        done, label = 0.0, ""
        for key, lab, weight in PHASES:
            if key == self._phase:
                done += weight * max(0.0, min(1.0, fraction))
                label = lab
                break
            done += weight
        try:
            self._progress_cb({"percent": round(min(99.0, done), 1), "phase": label,
                               "pages_crawled": len(self.result.pages), "requests": self.result.requests_made})
        except Exception:  # noqa: BLE001
            pass

    # ---- helpers ------------------------------------------------------------------------
    def is_internal(self, url: str) -> bool:
        return urlsplit(url).scheme in ("http", "https") and site_key(url) == self.key

    def _throttle(self, url: str) -> None:
        k = site_key(url)
        interval = self.min_interval if k == self.key else EXTERNAL_HOST_INTERVAL_S
        last = self._last_request.get(k)
        if last is not None:
            wait = interval - (self._now() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[k] = self._now()

    def fetch(self, url: str, read_body: int = 0, method: str = "GET") -> FetchOutcome:
        self._throttle(url)
        self.result.requests_made += 1
        out = FetchOutcome(url=url)
        try:
            r = safe_fetch(self.client, url, method=method, read_body_bytes=read_body)
        except (BlockedTargetError, UnsafeUrlError) as e:
            out.error_type, out.error_detail = "blocked", str(e)[:500]
        except DnsResolutionError as e:
            out.error_type, out.error_detail = "dns", str(e)[:500]
        except TooManyRedirects as e:
            out.error_type = "redirect_loop" if e.loop else "too_many_redirects"
            out.error_detail = str(e)
            out.hops = [(h.url, h.status) for h in e.hops]
        except httpx.TimeoutException:
            out.error_type, out.error_detail = "timeout", f"No response within {self.timeout:g}s"
        except httpx.HTTPError as e:
            msg = str(e) or type(e).__name__
            out.error_type = "ssl" if ("SSL" in msg or "CERTIFICATE" in msg) else "connect"
            out.error_detail = msg[:500]
        else:
            out.status, out.final_url, out.elapsed_ms = r.status, r.final_url, r.elapsed_ms
            out.hops = [(h.url, h.status) for h in r.hops]
            out.content_type = r.headers.get("content-type", "")
            out.headers = {k.lower(): v for k, v in r.headers.items() if k.lower() in KEPT_HEADERS}
            out.body = r.body
        return out

    def check_link(self, url: str) -> FetchOutcome:
        """HEAD first (cheap); fall back to GET when HEAD is not supported or rejected."""
        out = self.fetch(url, method="HEAD")
        if out.status in (403, 404, 405, 501) or out.error_type in ("connect",):
            get = self.fetch(url, read_body=1)
            if get.status is not None or out.status is None:
                return get
        return out

    # ---- phases -------------------------------------------------------------------------
    def load_robots(self) -> None:
        robots_url = urljoin(self.base_url, "/robots.txt")
        f = self.fetch(robots_url, read_body=512 * 1024)
        if f.status is None:
            self.result.robots = parse_robots(robots_url, None, None, f.error_detail or f.error_type)
        else:
            self.result.robots = parse_robots(robots_url, f.status, f.body.decode("utf-8", "replace"))
        delay = self.result.robots.crawl_delay
        if delay:
            self.min_interval = max(self.min_interval, min(float(delay), 30.0))

    def load_sitemaps(self) -> None:
        robots = self.result.robots
        declared = set(robots.sitemaps if robots else [])
        queue = list(declared or [urljoin(self.base_url, "/sitemap.xml")])
        seen: set[str] = set()
        while queue and len(seen) < MAX_SITEMAP_FILES:
            sm = queue.pop(0)
            if sm in seen:
                continue
            # A sitemap the owner declared in robots.txt is an invitation; anything else
            # (the /sitemap.xml guess, nested sitemaps) must be allowed by robots.txt.
            if sm not in declared and robots and not robots.can_fetch(sm):
                continue
            seen.add(sm)
            f = self.fetch(sm, read_body=MAX_SITEMAP_BYTES)
            res = parse_sitemap(sm, f.status, f.body, f.error_detail if f.status is None else None)
            self.result.sitemaps.append(res)
            queue.extend(res.children)
            self.result.sitemap_urls.extend(u for u in res.urls if u not in self.result.sitemap_urls)

    def crawl_pages(self) -> None:
        max_depth = int(self.cfg.get("max_depth", 3))
        max_pages = int(self.cfg.get("max_pages", 500))
        robots = self.result.robots
        q: deque[tuple[str, int]] = deque([(self.base_url, 0)])
        queued = {self.base_url}
        while q:
            url, depth = q.popleft()
            self.progress(fraction=len(self.result.pages) / max(1, min(max_pages, len(self.result.pages) + len(q) + 1)))
            if len(self.result.pages) >= max_pages:
                self.result.stopped_reason = "max_pages"
                self.result.not_crawled_limit.add(url)
                self.result.not_crawled_limit.update(u for u, _ in q)
                break
            if url != self.base_url and self.excluded(url):
                self.result.excluded.add(url)
                continue
            if robots and not robots.can_fetch(url):
                self.result.robots_blocked.add(url)
                continue
            f = self.fetch(url, read_body=MAX_HTML_BYTES)
            page = Page(url=url, depth=depth, fetch=f)
            self.result.pages[url] = page
            if not f.ok or "html" not in (f.content_type or "").lower():
                continue
            final = f.final_url or url
            if not self.is_internal(final):  # redirected off-site: don't parse as ours
                continue
            if final != url:
                # Analyse the destination once, under its own URL (avoids double-counting it).
                if final not in queued:
                    queued.add(final)
                    q.appendleft((final, depth))
                continue
            page.facts = parse_html(f.body.decode("utf-8", "replace"), final)
            for link in page.facts.links:
                self.result.link_sources.setdefault(link, set()).add(url)
                if self.is_internal(link) and link not in queued and depth + 1 <= max_depth:
                    queued.add(link)
                    q.append((link, depth + 1))
                elif self.is_internal(link) and link not in queued:
                    self.result.not_crawled_limit.add(link)
            for src, _ in page.facts.images:
                self.result.image_sources.setdefault(src, set()).add(url)

    def check_internal_unvisited(self) -> None:
        """Internal links found but not crawled because of depth: still verify they resolve."""
        budget = 200
        for url in sorted(self.result.not_crawled_limit):
            if url in self.result.pages or self.excluded(url) or self.result.stopped_reason == "max_pages":
                continue
            if self.result.robots and not self.result.robots.can_fetch(url):
                continue
            if budget <= 0:
                break
            budget -= 1
            self.progress(fraction=(200 - budget) / 200)
            self.result.pages[url] = Page(url=url, depth=-1, fetch=self.check_link(url))

    def check_external(self) -> None:
        if not self.cfg.get("check_external_links", True):
            return
        targets = sorted(u for u in self.result.link_sources
                         if not self.is_internal(u) and urlsplit(u).scheme in ("http", "https"))
        for i, url in enumerate(targets):
            if i >= MAX_EXTERNAL_CHECKS:
                self.result.external_skipped = len(targets) - MAX_EXTERNAL_CHECKS
                break
            self.progress(fraction=i / max(1, min(len(targets), MAX_EXTERNAL_CHECKS)))
            self.result.external[url] = self.check_link(url)

    def check_images(self) -> None:
        total = min(len(self.result.image_sources), MAX_IMAGE_CHECKS)
        for i, url in enumerate(sorted(self.result.image_sources)):
            self.progress(fraction=i / max(1, total))
            if i >= MAX_IMAGE_CHECKS:
                self.result.images_skipped = len(self.result.image_sources) - MAX_IMAGE_CHECKS
                break
            if url in self.result.pages:
                self.result.images[url] = self.result.pages[url].fetch
            elif self.is_internal(url) and self.result.robots and not self.result.robots.can_fetch(url):
                continue
            else:
                self.result.images[url] = self.check_link(url)

    def check_sitemap_urls(self) -> None:
        n = 0
        for url in self.result.sitemap_urls:
            if url in self.result.pages or not self.is_internal(url):
                continue
            if self.result.robots and not self.result.robots.can_fetch(url):
                continue
            if n >= MAX_SITEMAP_URL_CHECKS:
                break
            n += 1
            self.result.sitemap_checks[url] = self.check_link(url)

    def check_form_actions(self) -> None:
        """Verify that internal form targets exist. Forms are never submitted: only the target
        URL is requested (HEAD, falling back to GET without any form data)."""
        targets: set[str] = set()
        for page in self.result.pages.values():
            if page.facts is None:
                continue
            for f in page.facts.forms:
                if f.action and f.action != page.url and self.is_internal(f.action):
                    targets.add(f.action.split("?")[0])
        for i, url in enumerate(sorted(targets)):
            if i >= MAX_FORM_ACTION_CHECKS:
                break
            if self.result.robots and not self.result.robots.can_fetch(url):
                continue
            page = self.result.pages.get(url)
            self.result.form_actions[url] = page.fetch if page else self.check_link(url)

    def check_important(self) -> None:
        for url in self.important_urls:
            page = self.result.pages.get(url)
            self.result.important[url] = page.fetch if page else self.check_link(url)

    def run(self) -> CrawlResult:
        start = self._now()
        with safe_client(self.timeout) as client:
            self.client = client
            self.progress("robots")
            self.load_robots()
            if self.result.robots and self.result.robots.parser is not None:
                if self.wants("seo"):
                    self.load_sitemaps()
                self.progress("pages")
                self.crawl_pages()
                if self.wants("links"):
                    self.progress("unvisited")
                    self.check_internal_unvisited()
                    self.progress("external")
                    self.check_external()
                if self.wants("images"):
                    self.progress("images")
                    self.check_images()
                if self.wants("seo"):
                    self.progress("sitemap")
                    self.check_sitemap_urls()
                if self.wants("forms"):
                    self.progress("forms")
                    self.check_form_actions()
                if self.wants("links"):
                    self.progress("important")
                    self.check_important()
        self.result.duration_s = round(self._now() - start, 1)
        return self.result
