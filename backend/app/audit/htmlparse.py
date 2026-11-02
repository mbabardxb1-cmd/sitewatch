"""Extract the SEO/link facts we audit from an HTML document (stdlib parser, tolerant of bad markup)."""
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "sms:", "ftp:", "file:", "#")


@dataclass
class PageFacts:
    title: str | None = None
    title_count: int = 0
    description: str | None = None
    robots_meta: str | None = None
    canonical: str | None = None
    links: list[str] = field(default_factory=list)
    images: list[tuple[str, bool]] = field(default_factory=list)  # (absolute src, has alt attribute)


class _Parser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.facts = PageFacts()
        self._in_title = False
        self._title_buf: list[str] = []

    def _abs(self, href: str | None) -> str | None:
        if not href:
            return None
        href = href.strip()
        if not href or href.lower().startswith(SKIP_SCHEMES):
            return None
        try:
            return urldefrag(urljoin(self.base, href))[0]
        except ValueError:
            return None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        elif tag == "title":
            self.facts.title_count += 1
            if self.facts.title_count == 1:
                self._in_title = True
        elif tag == "meta":
            name = a.get("name", "").lower()
            if name == "description" and self.facts.description is None:
                self.facts.description = a.get("content", "").strip()
            elif name in ("robots", "sitewatchmonitor") and a.get("content"):
                self.facts.robots_meta = ((self.facts.robots_meta or "") + "," + a["content"].lower()).strip(",")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.facts.canonical = self._abs(a.get("href"))
        elif tag == "a":
            u = self._abs(a.get("href"))
            if u:
                self.facts.links.append(u)
        elif tag == "img":
            u = self._abs(a.get("src"))
            if u:
                self.facts.images.append((u, "alt" in a))

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self.facts.title = " ".join("".join(self._title_buf).split())

    def handle_data(self, data):
        if self._in_title:
            self._title_buf.append(data)


def parse_html(html: str, base_url: str) -> PageFacts:
    p = _Parser(base_url)
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup: keep what was parsed
        pass
    if p._in_title:  # unterminated <title>
        p.facts.title = " ".join("".join(p._title_buf).split())
    # de-duplicate links preserving order
    seen: set[str] = set()
    p.facts.links = [x for x in p.facts.links if not (x in seen or seen.add(x))]
    return p.facts
