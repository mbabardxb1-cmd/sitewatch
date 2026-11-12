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
from app.audit.malware import MALWARE_CHECKS, evaluate_malware
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
    "heading_structure": "Main heading (H1) structure",
    "large_page": "Oversized HTML pages",
    "mixed_content": "Insecure (mixed) content on HTTPS pages",
    "security_headers": "Security headers",
    "form_security": "Forms submitting insecurely",
    "form_action": "Form targets that do not exist",
    "form_spam_protection": "Form spam protection",
    "form_labels": "Form fields without labels",
    "missing_viewport": "Mobile viewport configuration",
    "missing_lang": "Page language declaration",
    **MALWARE_CHECKS,
}

# Audit categories a person can choose from when starting an audit. Every check belongs to one.
CATEGORIES: dict[str, dict] = {
    "links": {"label": "Broken links & redirects",
              "description": "Internal and external links, redirect chains and loops, important pages.",
              "checks": ["broken_internal_link", "broken_external_link", "redirect_chain", "redirect_loop",
                         "page_access_restricted", "important_page_unavailable"]},
    "seo": {"label": "SEO",
            "description": "Titles, meta descriptions, headings, robots.txt, sitemap and indexing.",
            "checks": ["missing_title", "duplicate_title", "missing_meta_description", "duplicate_meta_description",
                       "heading_structure", "robots_txt", "robots_blocked_pages", "indexing", "sitemap",
                       "javascript_rendering"]},
    "images": {"label": "Images", "description": "Broken images and images without alternative text.",
               "checks": ["broken_image", "missing_alt"]},
    "performance": {"label": "Performance", "description": "Slow server responses and oversized pages.",
                    "checks": ["slow_page", "large_page"]},
    "security": {"label": "Security & SSL",
                 "description": "Certificate validity, mixed content and security headers.",
                 "checks": ["ssl_certificate", "mixed_content", "security_headers"]},
    "forms": {"label": "Forms",
              "description": "Form targets, secure submission and spam protection. Forms are never submitted.",
              "checks": ["form_security", "form_action", "form_spam_protection"]},
    "malware": {"label": "Malware & blacklist",
                "description": "Injected code, crypto-miners, card skimmers, spam, hidden iframes, cloaking and blacklists.",
                "checks": list(MALWARE_CHECKS)},
    "mobile": {"label": "Mobile & accessibility",
               "description": "Viewport for mobile devices, page language and labelled form fields.",
               "checks": ["missing_viewport", "missing_lang", "form_labels"]},
}
CATEGORY_OF = {c: key for key, cat in CATEGORIES.items() for c in cat["checks"]}
LARGE_PAGE_BYTES = 1024 * 1024

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
             is_https: bool, now: datetime | None = None, categories: set[str] | None = None,
             blacklist: dict | None = None, previous_script_hosts: list[str] | None = None) -> AuditOutcome:
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

    _extra_checks(cr, html_pages, homepage, is_https, F, covered, verified)
    malware_stats = {}
    if crawled and (categories is None or "malware" in categories):
        malware_stats = evaluate_malware(cr, html_pages, cr.base_url, Finding, F, covered, verified,
                                         blacklist, previous_script_hosts)
    elif not crawled:
        for key in MALWARE_CHECKS:
            covered(key, "not_checked", "The site could not be crawled")

    covered("javascript_rendering", "not_checked",
            "JavaScript rendering is not available in this version; content added by scripts was not audited"
            if config.get("render_js") else "Pages were audited from their HTML only (JavaScript rendering disabled)")

    # Categories that were not selected: drop their findings and say so in the coverage.
    if categories is not None:
        skipped = {k for k in CHECKS if CATEGORY_OF.get(k) not in categories}
        F = [f for f in F if f.check_key not in skipped]
        for k in skipped:
            cov[k] = {"status": "not_checked", "reason": "Not selected for this audit"}
            verified.pop(k, None)

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
        **({"malware": malware_stats} if malware_stats else {}),
    }
    return AuditOutcome(findings=capped, coverage=cov, stats=stats, verified_subjects=dict(verified))


def _extra_checks(cr: CrawlResult, html_pages, homepage, is_https: bool, F: list, covered, verified) -> None:
    """Headings, page weight, mixed content, security headers, forms, mobile/accessibility basics."""
    if not cr.pages:
        for key in ("heading_structure", "large_page", "mixed_content", "security_headers", "form_security",
                    "form_action", "form_spam_protection", "form_labels", "missing_viewport", "missing_lang"):
            covered(key, "not_checked", "The site could not be crawled")
        return
    none_reason = None if html_pages else "No HTML pages could be fetched"

    # ---- headings, size -------------------------------------------------------------------
    for p in html_pages:
        verified["heading_structure"].add(p.url)
        verified["large_page"].add(p.url)
        if p.facts.h1_count == 0:
            F.append(Finding("heading_structure", "low", "confirmed", f"No H1 heading: {_path(p.url)}", p.url))
        elif p.facts.h1_count > 1:
            F.append(Finding("heading_structure", "info", "confirmed",
                             f"{p.facts.h1_count} H1 headings: {_path(p.url)}", p.url, None, {"h1_count": p.facts.h1_count}))
        size = len(p.fetch.body or b"")
        if size > LARGE_PAGE_BYTES:
            F.append(Finding("large_page", "low", "confirmed", f"Large HTML ({size // 1024} KB): {_path(p.url)}", p.url,
                             None, {"bytes": size}))
    covered("heading_structure", "performed" if html_pages else "not_checked", none_reason, pages_checked=len(html_pages))
    covered("large_page", "performed" if html_pages else "not_checked", none_reason, pages_checked=len(html_pages))

    # ---- mixed content -----------------------------------------------------------------------
    if not is_https:
        covered("mixed_content", "not_applicable", "Website is configured with an http:// URL")
    else:
        https_pages = [p for p in html_pages if p.url.startswith("https://")]
        for p in https_pages:
            verified["mixed_content"].add(p.url)
            active = sorted({u for u in p.facts.resources if u.startswith("http://")})
            passive = sorted({src for src, _ in p.facts.images if src.startswith("http://")})
            if active:
                F.append(Finding("mixed_content", "high", "confirmed",
                                 f"Insecure scripts/styles loaded over http://: {_path(p.url)}", p.url, None,
                                 {"resources": active[:50], "note": "Browsers block these, which can break the page."}))
            elif passive:
                F.append(Finding("mixed_content", "medium", "confirmed",
                                 f"Insecure images loaded over http://: {_path(p.url)}", p.url, None, {"images": passive[:50]}))
        covered("mixed_content", "performed" if https_pages else "not_checked",
                None if https_pages else "No HTTPS pages could be fetched", pages_checked=len(https_pages))

    # ---- security headers (homepage) -------------------------------------------------------------
    if homepage is None or homepage.fetch.status is None or not homepage.fetch.ok:
        covered("security_headers", "not_checked", "The homepage could not be fetched")
    else:
        h = homepage.fetch.headers
        missing = []
        if is_https and "strict-transport-security" not in h:
            missing.append("Strict-Transport-Security (HSTS)")
        if h.get("x-content-type-options", "").lower() != "nosniff":
            missing.append("X-Content-Type-Options: nosniff")
        if "x-frame-options" not in h and "frame-ancestors" not in h.get("content-security-policy", ""):
            missing.append("X-Frame-Options or CSP frame-ancestors (clickjacking protection)")
        verified["security_headers"].add("headers")
        if missing:
            F.append(Finding("security_headers", "low", "confirmed", f"Homepage is missing {len(missing)} security header(s)",
                             cr.base_url, None, {"missing": missing}, subject="headers"))
        covered("security_headers", "performed", None)

    # ---- forms (never submitted) -------------------------------------------------------------
    forms: dict[str, dict] = {}
    for p in html_pages:
        for f in p.facts.forms:
            target = f.action or p.url
            key = f"{target.split('?')[0]}|{f.method}"
            entry = forms.setdefault(key, {"form": f, "target": target, "pages": [], "protected": False})
            entry["pages"].append(p.url)
            entry["protected"] = entry["protected"] or p.facts.spam_protection
    if not html_pages:
        for key in ("form_security", "form_action", "form_spam_protection", "form_labels"):
            covered(key, "not_checked", none_reason)
    elif not forms:
        for key in ("form_security", "form_action", "form_spam_protection", "form_labels"):
            covered(key, "not_applicable", "No forms were found on the crawled pages")
            verified[key].add("none")
    else:
        for key, e in forms.items():
            f, target, pages = e["form"], e["target"], sorted(e["pages"])
            where = {"form_target": target, "method": f.method.upper(), "found_on": pages[:MAX_SOURCES]}
            for k in ("form_security", "form_action", "form_spam_protection", "form_labels"):
                verified[k].add(key)
            on_http_page = any(u.startswith("http://") for u in pages)
            if target.startswith("http://") and (is_https or f.has_password):
                F.append(Finding("form_security", "high" if f.has_password else "medium", "confirmed",
                                 f"{'Login form' if f.has_password else 'Form'} submits over insecure http://: {_path(pages[0])}",
                                 target, pages[0], where, subject=key))
            elif f.has_password and on_http_page:
                F.append(Finding("form_security", "high", "confirmed", f"Password form on an http:// page: {_path(pages[0])}",
                                 target, pages[0], where, subject=key))
            res = cr.form_actions.get(target.split("?")[0])
            if res is not None and res.status is not None and (res.status in (404, 410) or res.status >= 500):
                F.append(Finding("form_action", "high", "confirmed",
                                 f"Form target returns {_status_text(res)}: {_path(target)}", target, pages[0],
                                 {**where, "status": res.status}, subject=key))
            if f.method == "post" and f.fields >= 2 and not f.has_password and not e["protected"]:
                F.append(Finding("form_spam_protection", "info", "warning",
                                 f"No CAPTCHA or honeypot detected on form: {_path(pages[0])}", target, pages[0],
                                 {**where, "note": "Server-side spam filtering cannot be seen from outside."}, subject=key))
            if f.unlabeled:
                F.append(Finding("form_labels", "low", "confirmed",
                                 f"{f.unlabeled} form field(s) without a label: {_path(pages[0])}", target, pages[0],
                                 {**where, "unlabeled_fields": f.unlabeled}, subject=key))
        unchecked_targets = sum(1 for e in forms.values() if e["target"].split("?")[0] not in cr.form_actions
                                and e["target"] not in e["pages"])
        covered("form_security", "performed", None, forms=len(forms))
        covered("form_action", "partial" if unchecked_targets else "performed",
                f"{unchecked_targets} form target(s) on other hosts or over the limit were not requested" if unchecked_targets else None,
                forms=len(forms))
        covered("form_spam_protection", "performed", None, forms=len(forms))
        covered("form_labels", "performed", None, forms=len(forms))

    # ---- mobile & language (site-level: usually one template) ---------------------------------
    no_vp = [p.url for p in html_pages if not p.facts.viewport or "width" not in p.facts.viewport]
    no_lang = [p.url for p in html_pages if not p.facts.lang]
    verified["missing_viewport"].add("site")
    verified["missing_lang"].add("site")
    if no_vp:
        F.append(Finding("missing_viewport", "medium", "confirmed",
                         f"{len(no_vp)} page(s) have no mobile viewport (they will render zoomed out on phones)",
                         no_vp[0], None, {"urls": sorted(no_vp)[:100]}, subject="site"))
    if no_lang:
        F.append(Finding("missing_lang", "low", "confirmed", f"{len(no_lang)} page(s) do not declare their language (<html lang>)",
                         no_lang[0], None, {"urls": sorted(no_lang)[:100]}, subject="site"))
    for key in ("missing_viewport", "missing_lang"):
        covered(key, "performed" if html_pages else "not_checked", none_reason, pages_checked=len(html_pages))
