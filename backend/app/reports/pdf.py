"""Render a report snapshot (see data.py) to PDF with ReportLab, in the agency's slide-style format.

Layout (16:9 landscape pages):
  cover (black, white logo, client, report title) ->
  monthly summary -> maintenance work (task table) -> uptime & performance -> technical audit & issues
  -> domain, hosting & SSL -> recommendations & comparison -> notes on method.
Content pages are white with the logo bottom-left and the company website bottom-right.
Tables: black header row with white text, light grey grid, status in green.
All text derived from websites/users is escaped before it reaches ReportLab's markup parser.
"""
import io
import os
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.reports.logo import Logo

PAGE = (960, 540)  # 13.33 x 7.5 in, the 16:9 slide size
MARGIN_X = 60
CONTENT_W = PAGE[0] - 2 * MARGIN_X

BLACK = colors.black
INK, TEXT_GREY = colors.HexColor("#1f1f1f"), colors.HexColor("#404040")
MUTED, GRID = colors.HexColor("#6b6b6b"), colors.HexColor("#d4d4d4")
GREEN = colors.HexColor("#2e7d32")
LEVEL = {  # status colours always paired with a text label
    "healthy": (colors.HexColor("#2e7d32"), "HEALTHY"),
    "warning": (colors.HexColor("#b26a00"), "WARNING"),
    "critical": (colors.HexColor("#c62828"), "CRITICAL"),
    "unknown": (colors.HexColor("#595959"), "UNKNOWN"),
}
SEV_COLORS = {"critical": "#c62828", "high": "#d84315", "medium": "#b26a00", "low": "#1565c0", "info": "#595959"}
SOURCE_LABEL = {"rdap": "Verified (RDAP)", "whois": "Verified (WHOIS)", "registrar_api": "Verified (registrar API)",
                "api": "Verified (provider API)", "manual": "Manual entry - not verified", "certificate": "Live certificate",
                "unknown": "Unknown"}
DEFAULT_TITLE = "Website Maintenance Report"
DEFAULT_INTRO = "Our maintenance contract keeps the website updated, secure and backed up every month."

# Carlito is metrically identical to Calibri; DejaVu is the wide-coverage fallback.
FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/crosextra", "Carlito-Regular.ttf", "Carlito-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
]

_FONTS: tuple[str, str] | None = None


def fonts() -> tuple[str, str]:
    global _FONTS
    if _FONTS:
        return _FONTS
    for d, regular, bold in FONT_CANDIDATES:
        reg, bld = os.path.join(d, regular), os.path.join(d, bold)
        if os.path.exists(reg) and os.path.exists(bld):
            pdfmetrics.registerFont(TTFont("SW", reg))
            pdfmetrics.registerFont(TTFont("SW-Bold", bld))
            pdfmetrics.registerFontFamily("SW", normal="SW", bold="SW-Bold", italic="SW", boldItalic="SW-Bold")
            _FONTS = ("SW", "SW-Bold")
            return _FONTS
    _FONTS = ("Helvetica", "Helvetica-Bold")
    return _FONTS


def t(value) -> str:
    """Escape untrusted text for Paragraph markup; keep it within the font's coverage."""
    s = escape("" if value is None else str(value))
    if fonts()[0] == "Helvetica":
        s = s.encode("cp1252", "replace").decode("cp1252")
    return s


def _plain(value: str) -> str:
    """Text drawn directly on the canvas (cover, footer): no markup, but same font coverage rule."""
    s = value or ""
    if fonts()[0] == "Helvetica":
        s = s.encode("cp1252", "replace").decode("cp1252")
    return s


def _d(v) -> str:
    if not v:
        return "-"
    try:
        return datetime.fromisoformat(v).strftime("%d %b %Y")
    except ValueError:
        return str(v)


def _dt(v) -> str:
    if not v:
        return "-"
    return datetime.fromisoformat(v).strftime("%d %b %Y %H:%M UTC")


def _dur(s) -> str:
    if s is None:
        return "-"
    h, r = divmod(int(s), 3600)
    m, sec = divmod(r, 60)
    return f"{h} h {m} min" if h else (f"{m} min {sec} s" if m else f"{sec} s")


def _pct(v) -> str:
    return "No data" if v is None else f"{v:.2f}%"


def _hex(c) -> str:
    return "#" + c.hexval()[2:]


class _Styles:
    def __init__(self):
        reg, bold = fonts()
        self.body = ParagraphStyle("body", fontName=reg, fontSize=12.5, leading=17, textColor=INK)
        self.small = ParagraphStyle("small", parent=self.body, fontSize=10, leading=13.5, textColor=MUTED)
        self.intro = ParagraphStyle("intro", parent=self.body, fontSize=17, leading=23, textColor=TEXT_GREY, spaceAfter=18)
        self.cell = ParagraphStyle("cell", parent=self.body, fontSize=12, leading=16)
        self.cell_b = ParagraphStyle("cellb", parent=self.cell, fontName=bold)
        self.cell_h = ParagraphStyle("cellh", parent=self.cell, fontName=bold, textColor=colors.white)
        self.h1 = ParagraphStyle("h1", parent=self.body, fontName=bold, fontSize=26, leading=31, spaceAfter=6, textColor=BLACK)
        self.h3 = ParagraphStyle("h3", parent=self.body, fontName=bold, fontSize=15, leading=19, spaceBefore=12, spaceAfter=7,
                                 textColor=BLACK)
        self.kpi_value = ParagraphStyle("kpiv", parent=self.body, fontName=bold, fontSize=26, leading=30, textColor=BLACK)
        self.kpi_label = ParagraphStyle("kpil", parent=self.body, fontSize=11, leading=14, textColor=MUTED)
        self.bullet = ParagraphStyle("bullet", parent=self.body, leftIndent=14, bulletIndent=2, spaceAfter=4)
        self.bold = bold


def _table(rows, widths, s: _Styles, header=True, bold_col: int | None = None, align_right: tuple[int, ...] = ()):
    """Brand table: black header with white text, light grey grid, roomy rows."""
    data = []
    for i, row in enumerate(rows):
        out = []
        for j, c in enumerate(row):
            if not isinstance(c, str):
                out.append(c)
                continue
            style = s.cell_h if (header and i == 0) else (s.cell_b if j == bold_col else s.cell)
            if j in align_right:
                style = ParagraphStyle(f"r{style.name}", parent=style, alignment=2)
            out.append(Paragraph(c, style))
        data.append(out)
    tbl = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("GRID", (0, 0), (-1, -1), 0.75, GRID),
             ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
             ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), BLACK), ("LINEBELOW", (0, 0), (-1, 0), 0.75, BLACK)]
    tbl.setStyle(TableStyle(style))
    return tbl


def _sev(sev: str) -> str:
    return f'<font color="{SEV_COLORS.get(sev, "#595959")}"><b>{t(sev.capitalize())}</b></font>'


def _done() -> str:
    return f'<font color="{_hex(GREEN)}"><b>Completed</b></font>'


def _kpis(items: list[tuple[str, str, str | None]], s: _Styles):
    """Row of KPI tiles: (label, value, note)."""
    w = CONTENT_W / len(items)
    cells = []
    for label, value, note in items:
        parts = [Paragraph(t(label), s.kpi_label), Paragraph(value, s.kpi_value)]
        if note:
            parts.append(Paragraph(t(note), s.small))
        cells.append(parts)
    tbl = Table([cells], colWidths=[w] * len(items), hAlign="LEFT")
    tbl.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.75, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.75, GRID),
                             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 12),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 12), ("LEFTPADDING", (0, 0), (-1, -1), 14)]))
    return tbl


def _status_line(status: dict, s: _Styles):
    fg, label = LEVEL.get(status["level"], LEVEL["unknown"])
    rows = [[Paragraph(f'<font color="{_hex(fg)}"><b>Overall status: {label}</b></font>', s.h3)]]
    rows += [[Paragraph(t(r), s.bullet, bulletText="•")] for r in status["reasons"]]
    tbl = Table(rows, colWidths=[CONTENT_W], hAlign="LEFT")
    tbl.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, -1), 4, fg), ("LEFTPADDING", (0, 0), (-1, -1), 14),
                             ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return tbl


def _response_chart(daily: list[dict], width, line_color) -> Drawing | None:
    pts = [(date.fromisoformat(d["day"]).toordinal(), d["avg_ms"]) for d in daily if d["avg_ms"] is not None]
    if len(pts) < 2:
        return None
    reg, _ = fonts()
    h = 190
    dr = Drawing(width, h)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 55, 25, width - 70, h - 50
    lp.data = [pts]
    lp.lines[0].strokeColor = line_color
    lp.lines[0].strokeWidth = 2.2
    ymax = max(v for _, v in pts)
    step = max(1, round(ymax / 4, -1 if ymax > 50 else 0))
    lp.yValueAxis.valueMin = 0
    lp.yValueAxis.valueMax = step * 5
    lp.yValueAxis.valueStep = step
    lp.yValueAxis.labels.fontName = reg
    lp.yValueAxis.labels.fontSize = 9
    lp.yValueAxis.labelTextFormat = "%d ms"
    lp.yValueAxis.visibleGrid = True
    lp.yValueAxis.gridStrokeColor = GRID
    lp.yValueAxis.strokeColor = GRID
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = pts[0][0], pts[-1][0]
    lp.xValueAxis.labels.fontName = reg
    lp.xValueAxis.labels.fontSize = 9
    lp.xValueAxis.labelTextFormat = lambda v: date.fromordinal(int(v)).strftime("%d %b")
    lp.xValueAxis.valueSteps = [p[0] for p in pts][:: max(1, len(pts) // 8)]
    lp.xValueAxis.strokeColor = GRID
    dr.add(lp)
    dr.add(String(55, h - 12, "Average response time per day (successful checks)", fontName=reg, fontSize=10,
                  fillColor=MUTED))
    return dr


class _NumberedCanvas(rl_canvas.Canvas):
    """Adds the page number to content pages once the total is known (the cover has none)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._saved = []

    def showPage(self):  # noqa: N802
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            if self._pageNumber > 1:
                reg, _ = fonts()
                self.setFont(reg, 9)
                self.setFillColor(MUTED)
                self.drawCentredString(PAGE[0] / 2, 26, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def render_pdf(snap: dict, logo_path: str | None = None) -> bytes:
    b = snap.get("branding") or {}
    s = _Styles()
    reg, bold = fonts()
    line_color = colors.HexColor(b["accent_color"]) if b.get("accent_color") else BLACK
    buf = io.BytesIO()
    period_label = datetime.fromisoformat(snap["period_start"]).strftime("%B %Y")
    last_day = (datetime.fromisoformat(snap["period_end"]) - timedelta(days=1)).date()
    company = b.get("company_name") or "SiteWatch"
    site_text = b.get("footer_text") or ""
    title = b.get("report_title") or DEFAULT_TITLE
    tech = snap["kind"] == "technical"
    logo = Logo(logo_path, company)

    def cover(c, doc):
        c.saveState()
        c.setFillColor(BLACK)
        c.rect(0, 0, PAGE[0], PAGE[1], stroke=0, fill=1)
        logo.draw(c, MARGIN_X, PAGE[1] - 105, 46, dark_background=True)
        c.setFillColor(colors.white)
        c.setFont(reg, 19)
        c.drawString(MARGIN_X + 10, 318, _plain(snap["website"]["name"][:70]))
        c.setFont(bold, 50)
        c.drawString(MARGIN_X + 10, 250, _plain(title[:40]))
        c.setFont(reg, 17)
        c.setFillColor(colors.HexColor("#bfbfbf"))
        sub = period_label + (" · Technical edition" if tech else "")
        c.drawString(MARGIN_X + 10, 212, sub)
        c.setFont(reg, 13)
        c.setFillColor(colors.white)
        if site_text:
            c.drawString(MARGIN_X, 40, _plain(site_text[:80]))
        c.restoreState()

    def content(c, doc):
        c.saveState()
        logo.draw(c, MARGIN_X, 20, 20, dark_background=False)
        c.setFont(reg, 11)
        c.setFillColor(TEXT_GREY)
        if site_text:
            c.drawRightString(PAGE[0] - MARGIN_X, 25, _plain(site_text[:80]))
        c.restoreState()

    frame = Frame(MARGIN_X, 62, CONTENT_W, PAGE[1] - 62 - 45, id="body", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc = BaseDocTemplate(buf, pagesize=PAGE, leftMargin=MARGIN_X, rightMargin=MARGIN_X, topMargin=45, bottomMargin=62,
                          title=f"{snap['website']['name']} - {period_label}", author=company, subject=title)
    doc.addPageTemplates([PageTemplate(id="cover", frames=[frame], onPage=cover),
                          PageTemplate(id="content", frames=[frame], onPage=content)])

    def section(heading: str, intro: str | None = None) -> list:
        out = [PageBreak(), Paragraph(t(heading), s.h1)]
        if intro:
            out.append(Paragraph(t(intro), s.intro))
        else:
            out.append(Spacer(1, 10))
        return out

    # ---- cover ------------------------------------------------------------------------------------
    story: list = [Spacer(1, 1), NextPageTemplate("content")]

    # ---- summary ----------------------------------------------------------------------------------
    up, r, iss = snap["uptime"], snap["response"], snap["issues"]
    story += section(f"Monthly summary · {period_label}",
                     f"{snap['website']['name']} ({snap['website']['url']}) · {_d(snap['period_start'])} – "
                     f"{_d(last_day.isoformat())}")
    story.append(_kpis([
        ("Uptime", _pct(up["uptime_pct"]), f"{up['coverage_pct']:.1f}% of the month monitored"),
        ("Confirmed outages", str(len(up["incidents"])), f"Downtime {_dur(up['downtime_s'])}"),
        ("Average response", f"{r['avg_ms']} ms" if r["avg_ms"] is not None else "No data", None),
        ("Open issues", str(iss["outstanding_total"]), f"{iss['resolved_total']} fixed and verified"),
    ], s))
    story += [Spacer(1, 16), _status_line(snap["status"], s), Spacer(1, 8)]
    story += [Paragraph(t(x), s.bullet, bulletText="•") for x in snap["summary"]]
    if not snap["period_complete"]:
        story.append(Paragraph(f"<b>The period is not finished: data until {_dt(snap['data_until'])}.</b>", s.small))
    story.append(Paragraph(f"Generated {_dt(snap['generated_at'])} for {t(snap['website']['client'])}.", s.small))

    # ---- maintenance work (the agency's task table) ------------------------------------------------
    story += section("Maintenance work performed", b.get("maintenance_intro") or DEFAULT_INTRO)
    if snap["maintenance"]:
        rows = [["#", "Task", "Details", "Status"]]
        for n, m in enumerate(snap["maintenance"], 1):
            details = t(m.get("description") or m["category"])
            rows.append([str(n), f"<b>{t(m['title'])}</b><br/><font size=10 color='#6b6b6b'>{_d(m['date'])}</font>",
                         details, _done()])
        story.append(_table(rows, [50, 260, CONTENT_W - 50 - 260 - 130, 130], s))
    else:
        story.append(Paragraph("No maintenance work was recorded for this period.", s.body))

    # ---- uptime & performance ------------------------------------------------------------------------
    story += section("Uptime & performance", "Automated checks confirm the website is reachable and measure how "
                                             "quickly it responds.")
    story.append(_table([
        ["Measure", "This month", "Previous month"],
        ["Uptime", f"<b>{_pct(up['uptime_pct'])}</b>", _pct(up["previous"]["uptime_pct"])],
        ["Confirmed outages", str(len(up["incidents"])), str(up["previous"]["incidents"])],
        ["Total downtime", _dur(up["downtime_s"]), _dur(up["previous"]["downtime_s"])],
        ["Monitoring coverage", f"{up['coverage_pct']:.1f}% ({up['checks']} checks)", f"{up['previous']['coverage_pct']:.1f}%"],
        ["Average response", f"{r['avg_ms']} ms" if r["avg_ms"] is not None else "No data",
         f"{r['previous_avg_ms']} ms" if r["previous_avg_ms"] is not None else "No data"],
    ], [CONTENT_W * 0.4, CONTENT_W * 0.3, CONTENT_W * 0.3], s, bold_col=0))
    story.append(Paragraph("Uptime is time-weighted from recorded checks; time without checks is excluded, never counted "
                           "as available.", s.small))
    if r["avg_ms"] is None:
        story.append(Paragraph("No successful checks were recorded, so response times cannot be reported.", s.body))
    else:
        if r["p95_ms"]:
            story.append(Paragraph(f"95% of responses were faster than {r['p95_ms']} ms.", s.small))
        chart = _response_chart(r["daily"], CONTENT_W, line_color)
        if chart:
            story += [CondPageBreak(200), chart]
    if up["incidents"]:
        rows = [["Started", "Ended", "Duration"] + (["Cause"] if tech else [])]
        for i in up["incidents"][:40]:
            rows.append([_dt(i["started_at"]), "Ongoing" if i["ongoing"] else _dt(i["ended_at"]), _dur(i["duration_s"])]
                        + ([t(i.get("cause"))] if tech else []))
        w = [190, 190, 130] + ([CONTENT_W - 510] if tech else [])
        story += [CondPageBreak(120), Paragraph("Outages", s.h3), _table(rows, w, s)]
    else:
        story.append(Paragraph("No confirmed outages in this period.", s.body))

    # ---- technical audit & issues -------------------------------------------------------------------------
    story += section("Technical audit", "The website is crawled to find broken links, SEO, security, form and "
                                        "accessibility problems.")
    a, ap = snap["audit"], snap.get("audit_previous")
    if a is None:
        story.append(Paragraph("No technical audit was completed during this period. Audit results are therefore not "
                               "included; this is not an indication that no problems exist.", s.body))
    else:
        story.append(Paragraph(f"Latest audit finished {_dt(a['last_finished_at'])} ({a['last_status']}); "
                               f"{a['runs']} audit(s) completed in the period.", s.body))
        story.append(Spacer(1, 8))
        rows = [["Measure", "This month", "Previous month"],
                ["Pages crawled", str(a["pages_crawled"]), str(ap["pages_crawled"]) if ap else "-"],
                ["Broken internal links", str(a["broken_internal_links"]), str(ap["broken_internal_links"]) if ap else "-"],
                ["Broken external links", f"{a['broken_external_links']} (of {a['external_links_checked']} checked)", "-"]]
        for k in ("critical", "high", "medium", "low"):
            rows.append([_sev(k) + " findings", str(a["by_severity"][k]), str(ap["by_severity"][k]) if ap else "-"])
        story.append(_table(rows, [CONTENT_W * 0.4, CONTENT_W * 0.3, CONTENT_W * 0.3], s, bold_col=0))
        cov = a["coverage"]
        story.append(Paragraph(
            f"Checks performed: {len(cov.get('performed', []))} fully, {len(cov.get('partial', []))} partly; "
            f"{len(cov.get('not_checked', []))} not performed; {len(cov.get('not_applicable', []))} not applicable. "
            f"{a['could_not_check']} item(s) could not be checked automatically.", s.small))
        lim = cov.get("partial", []) + cov.get("not_checked", [])
        if lim:
            groups: dict[str, list[str]] = {}
            for c in lim:  # one row per distinct limitation, listing the affected checks
                groups.setdefault(c.get("reason") or "-", []).append(c["label"])
            rows = [["Limitation", "Checks affected"]] + [[t(reason), t(", ".join(labels))] for reason, labels in groups.items()]
            story += [CondPageBreak(100), Paragraph("What could not be fully checked", s.h3),
                      _table(rows, [CONTENT_W * 0.5, CONTENT_W * 0.5], s)]

    def issue_table(items, extra_note=False):
        rows = [["Severity", "Issue", "Category"] + (["Note"] if extra_note else [])]
        for i in items:
            ttl = t(i["title"]) + (f"<br/><font size=9 color='#6b6b6b'>{t(i['url'])}</font>" if tech and i.get("url") else "")
            rows.append([_sev(i["severity"]), ttl, t(i["category"])] + ([t(i.get("resolution_note") or "")] if extra_note else []))
        w = [100, CONTENT_W - 330, 230] if not extra_note else [100, 330, 180, CONTENT_W - 610]
        return _table(rows, w, s)

    story += [CondPageBreak(140), Paragraph("Issues", s.h3),
              Paragraph(f"New this period: {iss['new_total']} · confirmed fixed by a follow-up audit: {iss['resolved_total']} "
                        f"· outstanding now: {iss['outstanding_total']}", s.body), Spacer(1, 6)]
    if iss["resolved"]:
        story += [Paragraph("Fixed (verified by a later audit)", s.h3), issue_table(iss["resolved"], extra_note=tech)]
    if iss["recurring"]:
        story += [Paragraph("Recurring (previously fixed, found again)", s.h3), issue_table(iss["recurring"])]
    if iss["outstanding"]:
        story += [Paragraph("Outstanding issues requiring attention", s.h3), issue_table(iss["outstanding"])]
        if iss["outstanding_total"] > len(iss["outstanding"]):
            story.append(Paragraph(f"…and {iss['outstanding_total'] - len(iss['outstanding'])} more (lower severity).", s.small))
    elif snap["audit"] is not None and snap["audit"]["pages_crawled"] > 0:
        story.append(Paragraph("No outstanding issues.", s.body))
    else:
        story.append(Paragraph("No issues are listed because the website could not be audited this period; this does "
                               "not mean there are none.", s.body))

    # ---- domain, hosting, SSL ---------------------------------------------------------------------------
    e = snap.get("expiry")
    if e:
        story += section("Domain, hosting & SSL", "Renewal dates we track so the website never goes offline "
                                                  "because something expired.")

        def when(p):
            if not p["date"]:
                return "Unknown"
            dl = p["days_left"]
            return f"{_d(p['date'])}<br/><font size=10 color='#6b6b6b'>{'expired' if dl < 0 else f'{dl} days left'}</font>"

        rows = [["Item", "Expiry / renewal", "Source", "Details"],
                ["Domain" + (f"<br/><font size=10 color='#6b6b6b'>{t(e['domain']['name'])}</font>" if e["domain"]["name"] else ""),
                 when(e["domain"]), SOURCE_LABEL.get(e["domain"]["source"], e["domain"]["source"]),
                 t(e["domain"]["registrar"] or e["domain"]["note"] or "")],
                ["Hosting", when(e["hosting"]), SOURCE_LABEL.get(e["hosting"]["source"], e["hosting"]["source"]),
                 t(e["hosting"]["provider"] or e["hosting"]["note"] or "")]]
        ssl = e["ssl"]
        if ssl["applicable"]:
            detail = ("NOT VALID: " + t(ssl["error"])) if ssl["valid"] is False else t(ssl["issuer"] or ssl["note"] or "")
            rows.append(["SSL certificate", when(ssl), SOURCE_LABEL["certificate"] if ssl["date"] else "Unknown", detail])
        else:
            rows.append(["SSL certificate", "-", "-", t(ssl["note"])])
        story.append(_table(rows, [170, 190, 200, CONTENT_W - 560], s, bold_col=0))
        story.append(Paragraph("“Manual entry” dates were typed in by the team and have not been verified with the "
                               "registrar or hosting provider.", s.small))

    # ---- recommendations & comparison ---------------------------------------------------------------------
    story += section("Recommendations", "Suggested next steps, based on this month's monitoring and audit results.")
    if snap["recommendations"]:
        rows = [["Priority", "Recommended action", "Based on"]] + [
            [f"<b>{t(x['priority'].capitalize())}</b>", t(x["text"]), t(x["evidence"])] for x in snap["recommendations"]]
        story.append(_table(rows, [100, CONTENT_W * 0.5, CONTENT_W * 0.5 - 100], s))
    else:
        story.append(Paragraph("No corrective actions are recommended based on this period's data.", s.body))

    up_prev = up["previous"]
    rows = [["Measure", "This month", "Previous month", "Change"]]

    def change(cur, prev, unit="", better_low=False):
        if cur is None or prev is None:
            return "-"
        d = cur - prev
        if abs(d) < 1e-9:
            return "no change"
        good = (d < 0) if better_low else (d > 0)
        col = _hex(GREEN) if good else SEV_COLORS["critical"]
        return f"<font color='{col}'>{'+' if d > 0 else '−'}{abs(d):.2f}{unit} ({'better' if good else 'worse'})</font>"

    rows.append(["Uptime", _pct(up["uptime_pct"]), _pct(up_prev["uptime_pct"]), change(up["uptime_pct"], up_prev["uptime_pct"], " pts")])
    rows.append(["Outages", str(len(up["incidents"])), str(up_prev["incidents"]), change(len(up["incidents"]), up_prev["incidents"], "", True)])
    rows.append(["Average response", f"{r['avg_ms']} ms" if r["avg_ms"] is not None else "No data",
                 f"{r['previous_avg_ms']} ms" if r["previous_avg_ms"] is not None else "No data",
                 change(r["avg_ms"], r["previous_avg_ms"], " ms", True)])
    if a and ap:
        hi, hp = a["by_severity"]["critical"] + a["by_severity"]["high"], ap["by_severity"]["critical"] + ap["by_severity"]["high"]
        rows.append(["Critical + high findings", str(hi), str(hp), change(hi, hp, "", True)])
    story += [Spacer(1, 10), KeepTogether([Paragraph("Comparison with the previous month", s.h3),
                                           _table(rows, [CONTENT_W * 0.28, CONTENT_W * 0.22, CONTENT_W * 0.22,
                                                         CONTENT_W * 0.28], s, bold_col=0)])]

    # ---- method --------------------------------------------------------------------------------------------
    story += section("Notes on method")
    for line in (
        "Availability is measured by automated HTTP(S) checks every 1 minute to 24 hours, as configured, from a single "
        "location. An outage is confirmed only after repeated failed checks; brief network problems between the monitor "
        "and the website can occasionally be recorded as downtime.",
        "Technical audits crawl the website within configured limits and respect robots.txt. Checks that were limited "
        "or could not run are listed on the technical audit page; content generated by JavaScript is not audited. "
        "Forms are inspected but never submitted.",
        "An issue is reported as fixed only when a subsequent audit re-checked it and no longer observed the problem.",
        "Domain expiry dates are retrieved from the registry (RDAP/WHOIS). Hosting renewal dates are generally not "
        "public and are shown as entered by the team unless verified with the provider.",
        "Maintenance work is recorded by the team and describes actions taken; it is not an automated measurement.",
    ):
        story.append(Paragraph(t(line), s.bullet, bulletText="•"))

    doc.build(story, canvasmaker=_NumberedCanvas)
    return buf.getvalue()
