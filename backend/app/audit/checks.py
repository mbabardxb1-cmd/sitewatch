"""Turn crawl data into findings + an honest coverage record.

Coverage statuses: performed | partial | not_checked | not_applicable.
Certainty: confirmed (we observed the problem) | warning (likely, but may be transient or
bot-blocking) | could_not_check (we tried and could not determine the answer).
"""
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.audit.crawler import CrawlResult, FetchOutcome, site_key
from app.netsafe.tls import CertInfo

CHECKS: dict[str, str] = {
    "broken_internal_link": "Broken internal links",
    "broken_external_link": "Broken external links",
    "redirect_chain": "Redirect chains",
    "redirect_loop": "Redirect loops",
    "page_access_restricted": "Pages blocked by access restrictions",
    "robots_blocked_pages": "Pages excluded by robots.txt",
    "missing_title": "Missing page titles",
    "duplicate_title": "Duplicate page titles",
    "missing_meta_description": "Missing meta descriptions",
    "duplicate_meta_description": "Duplicate meta descriptions",
    "broken_image": "Broken images",
    "missing_alt": "Images without alternative text",
    "slow_page": "Slow page responses",
    "robots_txt": "robots.txt availability and directives",
    "indexing": "Indexing configuration (noindex)",
    "sitemap": "XML sitemap validity and URLs",
    "ssl_certificate": "SSL certificate validity and expiry",
    "important_page_unavailable": "Important pages availability",
    "javascript_rendering": "JavaScript-rendered content",
}

MAX_FINDINGS_PER_CHECK = 1000
MAX_SOURCES = 20


@dataclass
class Finding:
    check_key: str
    severity: str
    certainty: str
    title: str
    url: str | None = None
    source_url: str | None = None
    detail: dict = field(default_factory=dict)
    subject: str | None = None  # identity across runs; defaults to url

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.check_key}|{self.subject or self.url or 'site'}".encode()).hexdigest()


@dataclass
class AuditOutcome:
    findings: list[Finding]
    coverage: dict[str, dict]
    stats: dict
    # URLs actually verified this run, per check (used to decide which old issues are resolved)
    verified_subjects: dict[str, set[str]]


def _path(url: str) -> str:
    p = urlsplit(url)
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def _status_text(f: FetchOutcome) -> str:
    if f.status is not None:
        return f"HTTP {f.status}"
    return {"timeout": "timed out", "dns": "DNS lookup failed", "connect": "connection failed", "ssl": "SSL error",
            "blocked": "blocked by security policy", "redirect_loop": "redirect loop",
            "too_many_redirects": "too many redirects"}.get(f.error_type or "", f.error_type or "error")


def _sources(cr: CrawlResult, url: str) -> list[str]:
    return sorted(cr.link_sources.get(url, set()) | cr.image_sources.get(url, set()))[:MAX_SOURCES]


def evaluate(cr: CrawlResult, config: dict, cert: CertInfo | None, cert_error: str | None,
             is_https: bool, now: datetime | None = None) -> AuditOutcome:
    now = now or datetime.now(UTC)
    F: list[Finding] = []
    cov: dict[str, dict] = {}
    verified: dict[str, set[str]] = defaultdict(set)
    homepage = cr.pages.get(cr.base_url)
    crawled = bool(cr.pages)
    html_pages = [p for p in cr.pages.values() if p.facts is not None and p.fetch.status == 200]

    def covered(key, status, reason=None, **extra):
        cov[key] = {"status": status, **({"reason": reason} if reason else {}), **extra}

    if not crawled:
        reason = "The site could not be crawled"
        if cr.robots and cr.robots.status is None:
            reason += f": robots.txt request failed ({cr.robots.error})"
        elif cr.base_url in cr.robots_blocked:
            reason += ": robots.txt disallows crawling the homepage"
        for key in CHECKS:
            if key not in ("ssl_certificate",):
                covered(key, "not_checked", reason)

    # ---- internal pages: broken links, redirects, access, speed ---------------------------
    if crawled:
        slow_ms = int(config.get("slow_page_ms", 3000))
        for url, page in cr.pages.items():
            f = page.fetch
            verified["broken_internal_link"].add(url)
            verified["redirect_chain"].add(url)
            verified["redirect_loop"].add(url)
            srcs = _sources(cr, url)
            if f.error_type in ("redirect_loop", "too_many_redirects"):
                F.append(Finding("redirect_loop", "high", "confirmed",
                                 f"{'Redirect loop' if f.error_type == 'redirect_loop' else 'Too many redirects'}: {_path(url)}",
                                 url, srcs[0] if srcs else None,
                                 {"hops": [{"url": u, "status": s} for u, s in f.hops], "linked_from": srcs}))
                continue
            if f.status in (401, 403):
                F.append(Finding("page_access_restricted", "info", "could_not_check",
                                 f"Access restricted ({f.status}): {_path(url)}", url, srcs[0] if srcs else None,
                                 {"status": f.status, "linked_from": srcs}))
                continue
            if f.status is not None and f.status >= 400:
                sev = "critical" if url == cr.base_url else ("high" if f.status in (404, 410) or f.status >= 500 else "medium")
                F.append(Finding("broken_internal_link", sev, "confirmed", f"{_status_text(f)}: {_path(url)}", url,
                                 srcs[0] if srcs else None, {"status": f.status, "linked_from": srcs}))
            elif f.status is None:
                cert_ = "could_not_check" if f.error_type == "blocked" else "warning"
                F.append(Finding("broken_internal_link", "critical" if url == cr.base_url else "medium", cert_,
                                 f"Unreachable ({_status_text(f)}): {_path(url)}", url, srcs[0] if srcs else None,
                                 {"error_type": f.error_type, "error": f.error_detail, "linked_from": srcs}))
            if f.redirects >= 2:
                F.append(Finding("redirect_chain", "low", "confirmed", f"Redirect chain ({f.redirects} hops): {_path(url)}",
                                 url, srcs[0] if srcs else None,
                                 {"hops": [{"url": u, "status": s} for u, s in f.hops], "linked_from": srcs}))
            if f.ok and page.depth >= 0 and f.elapsed_ms is not None and f.elapsed_ms > slow_ms:
                F.append(Finding("slow_page", "medium" if f.elapsed_ms > 3 * slow_ms else "low", "confirmed",
                                 f"Slow response ({f.elapsed_ms} ms): {_path(url)}", url, None,
                                 {"response_ms": f.elapsed_ms, "threshold_ms": slow_ms}))
            if f.ok and page.depth >= 0:
                verified["slow_page"].add(url)
            if f.status is not None:
                verified["page_access_restricted"].add(url)

        partial_reason = None
        if cr.stopped_reason == "max_pages":
            partial_reason = f"Crawl stopped at the page limit ({config.get('max_pages')}); {len(cr.not_crawled_limit)} discovered URLs were not checked"
        for key in ("broken_internal_link", "redirect_chain", "redirect_loop", "slow_page", "page_access_restricted"):
            covered(key, "partial" if partial_reason else "performed", partial_reason, urls_checked=len(cr.pages))

        # robots-blocked / excluded pages are reported, not treated as errors
        if cr.robots_blocked:
            F.append(Finding("robots_blocked_pages", "info", "could_not_check",
                             f"{len(cr.robots_blocked)} linked page(s) not crawled because robots.txt disallows them",
                             None, None, {"urls": sorted(cr.robots_blocked)[:200]}, subject="site"))
        covered("robots_blocked_pages", "performed", None, blocked=len(cr.robots_blocked), excluded_by_config=len(cr.excluded))
        verified["robots_blocked_pages"].add("site")

    # ---- titles & descriptions ------------------------------------------------------------
    if crawled:
        titles, descs = defaultdict(list), defaultdict(list)
        for p in html_pages:
            fx = p.facts
            for k in ("missing_title", "missing_meta_description", "duplicate_title", "duplicate_meta_description"):
                verified[k].add(p.url)
            if not fx.title:
                F.append(Finding("missing_title", "medium", "confirmed", f"Missing <title>: {_path(p.url)}", p.url))
            else:
                titles[fx.title.lower()].append(p.url)
            if not fx.description:
                F.append(Finding("missing_meta_description", "low", "confirmed", f"Missing meta description: {_path(p.url)}", p.url))
            else:
                descs[fx.description.lower()].append(p.url)
        for key, groups, label in (("duplicate_title", titles, "title"), ("duplicate_meta_description", descs, "meta description")):
            for text, urls in groups.items():
                if len(urls) > 1:
                    F.append(Finding(key, "low", "confirmed", f"Duplicate {label} on {len(urls)} pages: “{text[:80]}”",
                                     sorted(urls)[0], None, {"text": text[:300], "urls": sorted(urls)[:50]},
                                     subject=f"{label}:{text[:300]}"))
                    verified[key].add(f"{label}:{text[:300]}")
        for key in ("missing_title", "missing_meta_description", "duplicate_title", "duplicate_meta_description"):
            covered(key, "performed" if html_pages else "not_checked",
                    None if html_pages else "No HTML pages could be fetched", pages_checked=len(html_pages))

    # ---- external links -------------------------------------------------------------------
    if crawled:
        if not config.get("check_external_links", True):
            covered("broken_external_link", "not_checked", "External link checking is disabled for this website")
        else:
            for url, f in cr.external.items():
                verified["broken_external_link"].add(url)
                srcs = _sources(cr, url)
                if f.status in (404, 410):
                    F.append(Finding("broken_external_link", "medium", "confirmed", f"{_status_text(f)}: {url}", url,
                                     srcs[0] if srcs else None, {"status": f.status, "linked_from": srcs}))
                elif f.status in (401, 403, 429, 999):
                    F.append(Finding("broken_external_link", "info", "could_not_check",
                                     f"External site refused automated check ({f.status}): {url}", url,
                                     srcs[0] if srcs else None, {"status": f.status, "linked_from": srcs}))
                elif f.status is not None and f.status >= 400:
                    F.append(Finding("broken_external_link", "low" if f.status >= 500 else "medium",
                                     "warning" if f.status >= 500 else "confirmed", f"{_status_text(f)}: {url}", url,
                                     srcs[0] if srcs else None, {"status": f.status, "linked_from": srcs}))
                elif f.status is None:
                    F.append(Finding("broken_external_link", "low" if f.error_type != "blocked" else "info",
                                     "could_not_check" if f.error_type == "blocked" else "warning",
                                     f"External link unreachable ({_status_text(f)}): {url}", url,
                                     srcs[0] if srcs else None, {"error_type": f.error_type, "linked_from": srcs}))
            covered("broken_external_link", "partial" if cr.external_skipped else "performed",
                    f"{cr.external_skipped} external links over the per-audit limit were not checked" if cr.external_skipped else None,
                    urls_checked=len(cr.external))

    # ---- images -----------------------------------------------------------------------------
    if crawled:
        for url, f in cr.images.items():
            verified["broken_image"].add(url)
            srcs = sorted(cr.image_sources.get(url, set()))[:MAX_SOURCES]
            if f.status is not None and f.status >= 400 and f.status not in (401, 403, 429):
                F.append(Finding("broken_image", "medium", "confirmed", f"Broken image ({_status_text(f)}): {_path(url) if urlsplit(url).netloc else url}",
                                 url, srcs[0] if srcs else None, {"status": f.status, "used_on": srcs}))
            elif f.status is None and f.error_type != "blocked":
                F.append(Finding("broken_image", "low", "warning", f"Image unreachable ({_status_text(f)}): {url}", url,
                                 srcs[0] if srcs else None, {"error_type": f.error_type, "used_on": srcs}))
        covered("broken_image", "partial" if cr.images_skipped else "performed",
                f"{cr.images_skipped} images over the per-audit limit were not checked" if cr.images_skipped else None,
                urls_checked=len(cr.images))
        for p in html_pages:
            verified["missing_alt"].add(p.url)
            missing = [src for src, has_alt in p.facts.images if not has_alt]
            if missing:
                F.append(Finding("missing_alt", "low", "confirmed", f"{len(missing)} image(s) without alt text: {_path(p.url)}",
                                 p.url, None, {"images": missing[:50]}))
        covered("missing_alt", "performed" if html_pages else "not_checked",
                None if html_pages else "No HTML pages could be fetched", pages_checked=len(html_pages))

    # ---- robots.txt & indexing -------------------------------------------------------------
    r = cr.robots
    if r is not None:
        verified["robots_txt"].add("site")
        if r.status is None:
            F.append(Finding("robots_txt", "medium", "could_not_check", "robots.txt could not be retrieved",
                             r.url, None, {"error": r.error}, subject="robots-fetch"))
            covered("robots_txt", "not_checked", f"robots.txt request failed: {r.error}")
        else:
            if r.status >= 500:
                F.append(Finding("robots_txt", "high", "confirmed", f"robots.txt returns HTTP {r.status} (crawlers treat this as 'disallow all')",
                                 r.url, None, {"status": r.status}, subject="robots-error"))
            elif r.status in (401, 403):
                F.append(Finding("robots_txt", "high", "confirmed", f"robots.txt is access-restricted (HTTP {r.status})",
                                 r.url, None, {"status": r.status}, subject="robots-error"))
            elif r.status == 404:
                F.append(Finding("robots_txt", "info", "confirmed", "No robots.txt file (all crawling allowed)", r.url,
                                 None, {"status": 404}, subject="robots-missing"))
            if r.disallow_all_for_everyone:
                F.append(Finding("robots_txt", "high", "confirmed", "robots.txt disallows all crawlers from the whole site",
                                 r.url, None, {}, subject="robots-disallow-all"))
            covered("robots_txt", "performed", None, http_status=r.status)

    if crawled and homepage is not None:
        verified["indexing"].update({"homepage-noindex", "sitemap-noindex"})
        hp_meta = (homepage.facts.robots_meta if homepage.facts else "") or ""
        hp_header = homepage.fetch.headers.get("x-robots-tag", "").lower()
        if "noindex" in hp_meta or "noindex" in hp_header:
            F.append(Finding("indexing", "high", "confirmed", "Homepage is marked noindex (search engines will drop it)",
                             cr.base_url, None, {"meta_robots": hp_meta, "x_robots_tag": hp_header}, subject="homepage-noindex"))
        sm_set = set(cr.sitemap_urls)
        noindexed_in_sm = [p.url for p in html_pages if p.url in sm_set and "noindex" in (p.facts.robots_meta or "")]
        if noindexed_in_sm:
            F.append(Finding("indexing", "medium", "confirmed",
                             f"{len(noindexed_in_sm)} page(s) listed in the sitemap are marked noindex", None, None,
                             {"urls": noindexed_in_sm[:100]}, subject="sitemap-noindex"))
        covered("indexing", "performed", None)

    # ---- sitemap ----------------------------------------------------------------------------
    if cr.robots is not None and cr.robots.parser is not None:
        verified["sitemap"].add("site")
        if not cr.sitemaps or (all(s.status == 404 for s in cr.sitemaps) and not cr.robots.sitemaps):
            F.append(Finding("sitemap", "low", "confirmed", "No XML sitemap found (not in robots.txt or at /sitemap.xml)",
                             None, None, {"tried": [s.url for s in cr.sitemaps]}, subject="sitemap-missing"))
        else:
            for s in cr.sitemaps:
                verified["sitemap"].add(f"file:{s.url}")
                if s.error:
                    kind = "inaccessible" if (s.status or 0) != 200 else "invalid"
                    F.append(Finding("sitemap", "medium", "confirmed", f"Sitemap {kind}: {s.url} ({s.error})", s.url,
                                     None, {"status": s.status, "error": s.error}, subject=f"file:{s.url}"))
            foreign = [u for u in cr.sitemap_urls if site_key(u) != site_key(cr.base_url)]
            if foreign:
                F.append(Finding("sitemap", "low", "confirmed", f"Sitemap lists {len(foreign)} URL(s) on other hosts",
                                 None, None, {"urls": foreign[:50]}, subject="sitemap-foreign"))
            verified["sitemap"].add("sitemap-foreign")
            for url in cr.sitemap_urls:
                f = cr.pages[url].fetch if url in cr.pages else cr.sitemap_checks.get(url)
                if f is None:
                    if cr.robots and not cr.robots.can_fetch(url):
                        F.append(Finding("sitemap", "medium", "confirmed", f"Sitemap URL is disallowed by robots.txt: {_path(url)}",
                                         url, None, {}, subject=f"blocked:{url}"))
                        verified["sitemap"].add(f"blocked:{url}")
                    continue
                verified["sitemap"].add(f"url:{url}")
                verified["sitemap"].add(f"blocked:{url}")
                if f.status is None or f.status >= 400:
                    F.append(Finding("sitemap", "medium", "confirmed" if f.status else "warning",
                                     f"Sitemap URL returns {_status_text(f)}: {_path(url)}", url, None,
                                     {"status": f.status, "error_type": f.error_type}, subject=f"url:{url}"))
        unchecked = len([u for u in cr.sitemap_urls if u not in cr.pages and u not in cr.sitemap_checks
                         and site_key(u) == site_key(cr.base_url) and cr.robots.can_fetch(u)])
        covered("sitemap", "partial" if unchecked else "performed",
                f"{unchecked} sitemap URLs were not individually checked (per-audit limit)" if unchecked else None,
                sitemap_files=len(cr.sitemaps), sitemap_urls=len(cr.sitemap_urls))

    # ---- important pages ----------------------------------------------------------------------
    if cr.important:
        for url, f in cr.important.items():
            verified["important_page_unavailable"].add(url)
            if not f.ok:
                F.append(Finding("important_page_unavailable", "high", "confirmed" if f.status else "warning",
                                 f"Important page unavailable ({_status_text(f)}): {_path(url)}", url, None,
                                 {"status": f.status, "error_type": f.error_type}))
        covered("important_page_unavailable", "performed", None, urls_checked=len(cr.important))
    elif crawled:
        covered("important_page_unavailable", "not_applicable", "No important pages are configured for this website")

    # ---- SSL ------------------------------------------------------------------------------------
    if not is_https:
        covered("ssl_certificate", "not_applicable", "Website is configured with an http:// URL")
    elif cert is None:
        covered("ssl_certificate", "not_checked", f"TLS connection could not be made: {cert_error}")
    else:
        verified["ssl_certificate"].add("site")
        days = cert.days_remaining(now)
        info = {"issuer": cert.issuer, "subject": cert.subject,
                "not_after": cert.not_after.isoformat() if cert.not_after else None, "days_remaining": days}
        if not cert.valid:
            F.append(Finding("ssl_certificate", "critical", "confirmed", f"SSL certificate is not valid: {cert.error}",
                             None, None, {**info, "error": cert.error}, subject="ssl"))
        elif days is not None and days < 30:
            sev = "critical" if days < 7 else ("high" if days < 14 else "medium")
            F.append(Finding("ssl_certificate", sev, "confirmed", f"SSL certificate expires in {days} days", None, None,
                             info, subject="ssl"))
        covered("ssl_certificate", "performed", None, **info)

    covered("javascript_rendering", "not_checked",
            "JavaScript rendering is not available in this version; content added by scripts was not audited"
            if config.get("render_js") else "Pages were audited from their HTML only (JavaScript rendering disabled)")

    # cap findings per check
    per: dict[str, int] = defaultdict(int)
    capped: list[Finding] = []
    for f in F:
        per[f.check_key] += 1
        if per[f.check_key] <= MAX_FINDINGS_PER_CHECK:
            capped.append(f)
    for k, n in per.items():
        if n > MAX_FINDINGS_PER_CHECK and k in cov:
            cov[k]["truncated"] = n - MAX_FINDINGS_PER_CHECK

    stats = {
        "pages_crawled": len([p for p in cr.pages.values() if p.depth >= 0]),
        "html_pages": len(html_pages),
        "internal_links_checked": len(cr.pages),
        "external_links_checked": len(cr.external),
        "images_checked": len(cr.images),
        "requests": cr.requests_made,
        "duration_s": cr.duration_s,
        "robots_blocked": len(cr.robots_blocked),
        "excluded": len(cr.excluded),
        "stopped_reason": cr.stopped_reason,
    }
    return AuditOutcome(findings=capped, coverage=cov, stats=stats, verified_subjects=dict(verified))
