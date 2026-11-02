"""robots.txt and XML sitemap handling."""
import gzip
from dataclasses import dataclass, field
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

MAX_SITEMAP_BYTES = 10 * 1024 * 1024
MAX_SITEMAP_FILES = 10
MAX_SITEMAP_URLS = 50000
ROBOTS_AGENT = "SiteWatchMonitor"


@dataclass
class RobotsInfo:
    url: str
    status: int | None = None  # HTTP status of robots.txt, None if it could not be fetched
    error: str | None = None
    parser: RobotFileParser | None = None
    disallow_all_for_everyone: bool = False
    sitemaps: list[str] = field(default_factory=list)
    crawl_delay: float | None = None

    @property
    def available(self) -> bool:
        return self.status == 200

    def can_fetch(self, url: str) -> bool:
        return True if self.parser is None else self.parser.can_fetch(ROBOTS_AGENT, url)


def parse_robots(url: str, status: int | None, text: str | None, error: str | None = None) -> RobotsInfo:
    info = RobotsInfo(url=url, status=status, error=error)
    rp = RobotFileParser(url)
    if status is None:
        info.parser = None  # unknown: we did not get an answer; caller decides
        return info
    if status in (401, 403):
        rp.disallow_all = True
    elif status >= 500:
        rp.disallow_all = True  # per RFC 9309, a server error means "assume complete disallow"
    elif status >= 400:
        rp.allow_all = True
    else:
        lines = (text or "").splitlines()
        rp.parse(lines)
        info.sitemaps = list(rp.site_maps() or [])
        delay = rp.crawl_delay(ROBOTS_AGENT)
        info.crawl_delay = float(delay) if delay else None
        star = RobotFileParser(url)
        star.parse(lines)
        info.disallow_all_for_everyone = not star.can_fetch("*", urljoin(url, "/"))
    info.parser = rp
    return info


@dataclass
class SitemapResult:
    url: str
    status: int | None
    error: str | None = None  # fetch error or XML error
    kind: str | None = None  # urlset | sitemapindex
    urls: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(url: str, status: int | None, body: bytes, error: str | None = None) -> SitemapResult:
    res = SitemapResult(url=url, status=status, error=error)
    if status != 200 or error:
        res.error = error or f"HTTP {status}"
        return res
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)[:MAX_SITEMAP_BYTES]
        except OSError as e:
            res.error = f"Invalid gzip: {e}"
            return res
    try:
        root = SafeET.fromstring(body)
    except (SafeET.ParseError, DefusedXmlException, ValueError) as e:
        res.error = f"Invalid XML: {e}"
        return res
    res.kind = _local(root.tag)
    if res.kind not in ("urlset", "sitemapindex"):
        res.error = f"Unexpected root element <{res.kind}>"
        return res
    for el in root:
        loc = next((c.text.strip() for c in el if _local(c.tag) == "loc" and c.text), None)
        if not loc:
            continue
        (res.urls if res.kind == "urlset" else res.children).append(loc)
        if len(res.urls) >= MAX_SITEMAP_URLS:
            break
    return res
