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
    h1_count: int = 0
    lang: str | None = None
    viewport: str | None = None
    # Sub-resources that load automatically (scripts, stylesheets, iframes, media), absolute URLs.
    resources: list[str] = field(default_factory=list)
    forms: list["FormFacts"] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)  # external <script src>, absolute
    iframes: list[tuple[str, bool]] = field(default_factory=list)  # (absolute src, hidden/zero-size)
    # Anti-spam markers seen anywhere on the page (CAPTCHA widgets/scripts, honeypot fields).
    spam_protection: bool = False


@dataclass
class FormFacts:
    action: str  # absolute URL the form submits to ("" = same page)
    method: str
    has_password: bool = False
    fields: int = 0  # user-fillable fields
    unlabeled: int = 0  # fields with no label, aria-label, aria-labelledby or title
    has_honeypot: bool = False
    _pending: list = field(default_factory=list, repr=False)  # (id, labelled) resolved at document end


CAPTCHA_MARKERS = ("g-recaptcha", "recaptcha/api", "h-captcha", "hcaptcha.com", "cf-turnstile",
                   "challenges.cloudflare.com/turnstile", "friendlycaptcha", "frc-captcha")
FILLABLE_SKIP = {"hidden", "submit", "button", "reset", "image"}


class _Parser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base = base_url
        self.facts = PageFacts()
        self._in_title = False
        self._title_buf: list[str] = []
        self._form: FormFacts | None = None
        self._label_depth = 0
        self._label_for: set[str] = set()

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
        cls = a.get("class", "")
        if any(m in cls for m in CAPTCHA_MARKERS) or any(m in a.get("src", "") for m in CAPTCHA_MARKERS):
            self.facts.spam_protection = True
        if tag == "html" and a.get("lang"):
            self.facts.lang = a["lang"].strip()
        elif tag == "h1":
            self.facts.h1_count += 1
        elif tag == "script" and a.get("src"):
            self._resource(a.get("src"))
            u = self._abs(a.get("src"))
            if u:
                self.facts.scripts.append(u)
        elif tag in ("iframe", "audio", "video", "source", "embed") and a.get("src"):
            self._resource(a.get("src"))
            if tag == "iframe":
                u = self._abs(a.get("src"))
                style = a.get("style", "").replace(" ", "").lower()
                tiny = a.get("width", "").strip().rstrip("px") in ("0", "1") or a.get("height", "").strip().rstrip("px") in ("0", "1")
                hidden = tiny or "display:none" in style or "visibility:hidden" in style or "hidden" in a
                if u:
                    self.facts.iframes.append((u, hidden))
        elif tag == "form":
            action = self._abs(a.get("action")) if a.get("action", "").strip() else ""
            self._form = FormFacts(action=action or "", method=(a.get("method") or "get").strip().lower() or "get")
            self.facts.forms.append(self._form)
        elif tag == "label":
            self._label_depth += 1
            if a.get("for"):
                self._label_for.add(a["for"])
        elif tag in ("input", "textarea", "select") and self._form is not None:
            typ = a.get("type", "text").lower() if tag == "input" else tag
            name = (a.get("name", "") + " " + a.get("id", "") + " " + cls).lower()
            hidden_style = "display:none" in a.get("style", "").replace(" ", "")
            if typ == "password":
                self._form.has_password = True
            if (typ == "hidden" or hidden_style) and any(w in name for w in ("honeypot", "hp_", "_hp", "bot", "trap")):
                self._form.has_honeypot = True
            if typ not in FILLABLE_SKIP and not hidden_style:
                self._form.fields += 1
                labelled = bool(self._label_depth or a.get("aria-label") or a.get("aria-labelledby") or a.get("title"))
                self._form._pending.append((a.get("id"), labelled))
        if tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        elif tag == "title":
            self.facts.title_count += 1
            if self.facts.title_count == 1:
                self._in_title = True
        elif tag == "meta":
            name = a.get("name", "").lower()
            if name == "viewport":
                self.facts.viewport = a.get("content", "").strip()
            if name == "description" and self.facts.description is None:
                self.facts.description = a.get("content", "").strip()
            elif name in ("robots", "sitewatchmonitor") and a.get("content"):
                self.facts.robots_meta = ((self.facts.robots_meta or "") + "," + a["content"].lower()).strip(",")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.facts.canonical = self._abs(a.get("href"))
        elif tag == "link" and "stylesheet" in a.get("rel", "").lower().split():
            self._resource(a.get("href"))
        elif tag == "a":
            u = self._abs(a.get("href"))
            if u:
                self.facts.links.append(u)
        elif tag == "img":
            u = self._abs(a.get("src"))
            if u:
                self.facts.images.append((u, "alt" in a))

    def _resource(self, href: str | None) -> None:
        u = self._abs(href)
        if u:
            self.facts.resources.append(u)

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "label" and self._label_depth:
            self._label_depth -= 1
        if tag == "title" and self._in_title:
            self._in_title = False
            self.facts.title = " ".join("".join(self._title_buf).split())

    def handle_data(self, data):
        if not self.facts.spam_protection and ("grecaptcha" in data or "turnstile.render" in data or "hcaptcha" in data):
            self.facts.spam_protection = True
        if self._in_title:
            self._title_buf.append(data)


def parse_html(html: str, base_url: str) -> PageFacts:
    p = _Parser(base_url)
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup: keep what was parsed
        pass
    for f in p.facts.forms:
        f.unlabeled = sum(1 for fid, labelled in f._pending if not labelled and not (fid and fid in p._label_for))
        f._pending = []
    p.facts.spam_protection = p.facts.spam_protection or any(f.has_honeypot for f in p.facts.forms)
    if p._in_title:  # unterminated <title>
        p.facts.title = " ".join("".join(p._title_buf).split())
    # de-duplicate links preserving order
    seen: set[str] = set()
    p.facts.links = [x for x in p.facts.links if not (x in seen or seen.add(x))]
    return p.facts
