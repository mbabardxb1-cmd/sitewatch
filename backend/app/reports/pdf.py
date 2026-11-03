"""Render a report snapshot (see data.py) to PDF with ReportLab.

Layout: cover + overall status + executive summary, then
  1. Monitoring results (availability, response time, technical audit, issues, domain/hosting/SSL)
  2. Maintenance work performed (recorded by the team; separate from monitoring)
  3. Recommendations   4. Comparison with the previous month   5. Notes on method
All text derived from websites/users is escaped before it reaches ReportLab's markup parser.
"""
import io
import os
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"]
INK, MUTED, RULE, SURFACE = colors.HexColor("#0f172a"), colors.HexColor("#475569"), colors.HexColor("#e2e8f0"), colors.HexColor("#f8fafc")
SERIES = colors.HexColor("#2a78d6")
LEVEL = {  # status colours always paired with a text label
    "healthy": (colors.HexColor("#ecfdf5"), colors.HexColor("#065f46"), "HEALTHY"),
    "warning": (colors.HexColor("#fffbeb"), colors.HexColor("#92400e"), "WARNING"),
    "critical": (colors.HexColor("#fef2f2"), colors.HexColor("#991b1b"), "CRITICAL"),
    "unknown": (colors.HexColor("#f1f5f9"), colors.HexColor("#334155"), "UNKNOWN"),
}
SEV_COLORS = {"critical": "#b91c1c", "high": "#c2410c", "medium": "#a16207", "low": "#0369a1", "info": "#475569"}
SOURCE_LABEL = {"rdap": "Verified (RDAP)", "whois": "Verified (WHOIS)", "registrar_api": "Verified (registrar API)",
                "api": "Verified (provider API)", "manual": "Manual entry - not verified", "certificate": "Live certificate",
                "unknown": "Unknown"}

_FONTS: tuple[str, str] | None = None


def fonts() -> tuple[str, str]:
    global _FONTS
    if _FONTS:
        return _FONTS
    for d in FONT_DIRS:
        reg, bold = os.path.join(d, "DejaVuSans.ttf"), os.path.join(d, "DejaVuSans-Bold.ttf")
        if os.path.exists(reg) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("SW", reg))
            pdfmetrics.registerFont(TTFont("SW-Bold", bold))
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


class _Styles:
    def __init__(self, accent):
        reg, bold = fonts()
        self.body = ParagraphStyle("body", fontName=reg, fontSize=9.5, leading=13.5, textColor=INK)
        self.small = ParagraphStyle("small", parent=self.body, fontSize=8, leading=10.5, textColor=MUTED)
        self.cell = ParagraphStyle("cell", parent=self.body, fontSize=8.5, leading=11)
        self.cell_b = ParagraphStyle("cellb", parent=self.cell, fontName=bold)
        self.h1 = ParagraphStyle("h1", parent=self.body, fontName=bold, fontSize=20, leading=25, spaceAfter=4)
        self.h2 = ParagraphStyle("h2", parent=self.body, fontName=bold, fontSize=14, leading=18, spaceBefore=10,
                                 spaceAfter=6, textColor=accent)
        self.h3 = ParagraphStyle("h3", parent=self.body, fontName=bold, fontSize=11, leading=14, spaceBefore=8, spaceAfter=4)
        self.bullet = ParagraphStyle("bullet", parent=self.body, leftIndent=10, bulletIndent=0)
        self.bold = bold


def _table(rows, widths, s: _Styles, header=True, zebra=True):
    data = [[c if not isinstance(c, str) else Paragraph(c, s.cell_b if (header and i == 0) else s.cell)
             for c in row] for i, row in enumerate(rows)]
    tbl = Table(data, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
             ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), SURFACE), ("LINEBELOW", (0, 0), (-1, 0), 0.8, MUTED)]
    tbl.setStyle(TableStyle(style))
    return tbl


def _sev(sev: str) -> str:
    return f'<font color="{SEV_COLORS.get(sev, "#475569")}"><b>{t(sev.upper())}</b></font>'


def _status_box(status: dict, s: _Styles, width):
    bg, fg, label = LEVEL.get(status["level"], LEVEL["unknown"])
    title = Paragraph(f'<font color="#{fg.hexval()[2:]}"><b>Overall status: {label}</b></font>', s.h3)
    reasons = [Paragraph(f"• {t(r)}", s.body) for r in status["reasons"]]
    tbl = Table([[title], *[[r] for r in reasons]], colWidths=[width])
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 0.8, fg),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 2),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    return tbl


def _response_chart(daily: list[dict], s: _Styles, width) -> Drawing | None:
    pts = [(date.fromisoformat(d["day"]).toordinal(), d["avg_ms"]) for d in daily if d["avg_ms"] is not None]
    if len(pts) < 2:
        return None
    reg, _ = fonts()
    h = 150
    dr = Drawing(width, h)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 45, 25, width - 60, h - 45
    lp.data = [pts]
    lp.lines[0].strokeColor = SERIES
    lp.lines[0].strokeWidth = 2
    ymax = max(v for _, v in pts)
    step = max(1, round(ymax / 4, -1 if ymax > 50 else 0))
    lp.yValueAxis.valueMin = 0
    lp.yValueAxis.valueMax = step * 5
    lp.yValueAxis.valueStep = step
    lp.yValueAxis.labels.fontName = reg
    lp.yValueAxis.labels.fontSize = 7
    lp.yValueAxis.labelTextFormat = "%d ms"
    lp.yValueAxis.visibleGrid = True
    lp.yValueAxis.gridStrokeColor = RULE
    lp.yValueAxis.strokeColor = RULE
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = pts[0][0], pts[-1][0]
    lp.xValueAxis.labels.fontName = reg
    lp.xValueAxis.labels.fontSize = 7
    lp.xValueAxis.labelTextFormat = lambda v: date.fromordinal(int(v)).strftime("%d %b")
    lp.xValueAxis.valueSteps = [p[0] for p in pts][:: max(1, len(pts) // 6)]
    lp.xValueAxis.strokeColor = RULE
    dr.add(lp)
    dr.add(String(45, h - 10, "Average response time per day (successful checks)", fontName=reg, fontSize=8,
                  fillColor=MUTED))
    return dr


class _NumberedCanvas(rl_canvas.Canvas):
    """Adds 'Page X of Y' after the total page count is known."""

    def __init__(self, *a, footer_left="", **kw):
        super().__init__(*a, **kw)
        self._saved = []
        self.footer_left = footer_left

    def showPage(self):  # noqa: N802
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            reg, _ = fonts()
            self.setFont(reg, 7.5)
            self.setFillColor(MUTED)
            self.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {self._pageNumber} of {total}")
            self.drawString(18 * mm, 10 * mm, self.footer_left[:120])
            super().showPage()
        super().save()


def render_pdf(snap: dict, logo_path: str | None = None) -> bytes:
    b = snap.get("branding") or {}
    accent = colors.HexColor(b.get("accent_color") or "#1e3a8a")
    s = _Styles(accent)
    buf = io.BytesIO()
    width = A4[0] - 36 * mm
    period_label = datetime.fromisoformat(snap["period_start"]).strftime("%B %Y")
    last_day = (datetime.fromisoformat(snap["period_end"]) - timedelta(days=1)).date()
    company = b.get("company_name") or "SiteWatch"
    footer = f"{company} · {snap['website']['name']} · {period_label}" + (f" · {b['footer_text']}" if b.get("footer_text") else "")

    def on_page(c, doc):
        reg, bold = fonts()
        c.saveState()
        c.setStrokeColor(accent)
        c.setLineWidth(2)
        c.line(18 * mm, A4[1] - 14 * mm, A4[0] - 18 * mm, A4[1] - 14 * mm)
        c.setFont(bold, 8)
        c.setFillColor(accent)
        c.drawString(18 * mm, A4[1] - 12 * mm, company[:80])
        c.setFont(reg, 8)
        c.setFillColor(MUTED)
        c.drawRightString(A4[0] - 18 * mm, A4[1] - 12 * mm,
                          f"{'Technical' if snap['kind'] == 'technical' else 'Client'} maintenance report · {period_label}")
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm,
                            bottomMargin=18 * mm, title=f"{snap['website']['name']} - {period_label}",
                            author=company, subject="Website maintenance report")
    story = []

    # ---- cover ----------------------------------------------------------------------------
    if logo_path and os.path.exists(logo_path):
        img = Image(logo_path)
        ratio = img.imageWidth / img.imageHeight
        img.drawHeight = 16 * mm
        img.drawWidth = min(60 * mm, 16 * mm * ratio)
        img.hAlign = "LEFT"
        story += [img, Spacer(1, 4 * mm)]
    story.append(Paragraph(f"Website maintenance report{' (technical)' if snap['kind'] == 'technical' else ''}", s.h1))
    meta = [
        ["Website", f"<b>{t(snap['website']['name'])}</b>"],
        ["Address", t(snap["website"]["url"])],
        ["Client", t(snap["website"]["client"])],
        ["Reporting period", f"{_d(snap['period_start'])} – {_d(last_day.isoformat())} (calendar month, UTC)"],
        ["Generated", _dt(snap["generated_at"])],
    ]
    if not snap["period_complete"]:
        meta.append(["Note", f"<b>The period is not finished: data until {_dt(snap['data_until'])}.</b>"])
    story += [_table(meta, [35 * mm, width - 35 * mm], s, header=False, zebra=False), Spacer(1, 5 * mm),
              _status_box(snap["status"], s, width), Spacer(1, 3 * mm),
              Paragraph("Executive summary", s.h2), *[Paragraph(t(x), s.body) for x in snap["summary"]],
              Spacer(1, 2 * mm),
              Paragraph("Section 1 contains automated <b>monitoring results</b>. Section 2 lists <b>maintenance work</b> "
                        "recorded by the team. An issue is only reported as fixed when a later audit confirmed it.", s.small)]

    # ---- 1. monitoring results ------------------------------------------------------------
    story += [PageBreak(), Paragraph("1. Monitoring results", s.h2), Paragraph("1.1 Availability", s.h3)]
    up = snap["uptime"]
    story.append(_table([
        ["Measure", "This month", "Previous month"],
        ["Uptime", f"<b>{_pct(up['uptime_pct'])}</b>", _pct(up["previous"]["uptime_pct"])],
        ["Confirmed outages", str(len(up["incidents"])), str(up["previous"]["incidents"])],
        ["Total downtime", _dur(up["downtime_s"]), _dur(up["previous"]["downtime_s"])],
        ["Monitoring coverage", f"{up['coverage_pct']:.1f}% ({up['checks']} checks)", f"{up['previous']['coverage_pct']:.1f}%"],
    ], [60 * mm, 55 * mm, width - 115 * mm], s))
    story.append(Paragraph("Uptime is time-weighted from recorded checks; time without checks is excluded, never counted "
                           "as available.", s.small))
    if up["incidents"]:
        rows = [["Started", "Ended", "Duration"] + (["Cause"] if snap["kind"] == "technical" else [])]
        for i in up["incidents"][:40]:
            rows.append([_dt(i["started_at"]), "Ongoing" if i["ongoing"] else _dt(i["ended_at"]), _dur(i["duration_s"])]
                        + ([t(i.get("cause"))] if snap["kind"] == "technical" else []))
        w = [40 * mm, 40 * mm, 28 * mm] + ([width - 108 * mm] if snap["kind"] == "technical" else [])
        story += [Spacer(1, 2 * mm), Paragraph("Outages", s.h3), _table(rows, w, s)]
    else:
        story.append(Paragraph("No confirmed outages in this period.", s.body))

    story.append(Paragraph("1.2 Response time", s.h3))
    r = snap["response"]
    if r["avg_ms"] is None:
        story.append(Paragraph("No successful checks were recorded, so response times cannot be reported.", s.body))
    else:
        story.append(Paragraph(f"Average {r['avg_ms']} ms" + (f", 95th percentile {r['p95_ms']} ms" if r["p95_ms"] else "")
                               + (f"; previous month {r['previous_avg_ms']} ms." if r["previous_avg_ms"] else "."), s.body))
        chart = _response_chart(r["daily"], s, width)
        if chart:
            story.append(chart)

    story.append(Paragraph("1.3 Technical audit", s.h3))
    a, ap = snap["audit"], snap.get("audit_previous")
    if a is None:
        story.append(Paragraph("No technical audit was completed during this period. Audit results are therefore not "
                               "included; this is not an indication that no problems exist.", s.body))
    else:
        story.append(Paragraph(f"Latest audit finished {_dt(a['last_finished_at'])} ({a['last_status']}); "
                               f"{a['runs']} audit(s) completed in the period.", s.body))
        rows = [["Measure", "This month", "Previous month"],
                ["Pages crawled", str(a["pages_crawled"]), str(ap["pages_crawled"]) if ap else "-"],
                ["Broken internal links", str(a["broken_internal_links"]), str(ap["broken_internal_links"]) if ap else "-"],
                ["Broken external links", f"{a['broken_external_links']} (of {a['external_links_checked']} checked)", "-"]]
        for k in ("critical", "high", "medium", "low"):
            rows.append([_sev(k) + " findings", str(a["by_severity"][k]), str(ap["by_severity"][k]) if ap else "-"])
        story.append(_table(rows, [60 * mm, 55 * mm, width - 115 * mm], s))
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
            story += [Spacer(1, 1 * mm), _table(rows, [width * 0.5, width * 0.5], s)]

    iss = snap["issues"]
    tech = snap["kind"] == "technical"

    def issue_table(items, extra_note=False):
        rows = [["Severity", "Issue", "Category"] + (["Note"] if extra_note else [])]
        for i in items:
            title = t(i["title"]) + (f"<br/><font size=7 color='#475569'>{t(i['url'])}</font>" if tech and i.get("url") else "")
            rows.append([_sev(i["severity"]), title, t(i["category"])] + ([t(i.get("resolution_note") or "")] if extra_note else []))
        w = [20 * mm, width - 62 * mm, 42 * mm] if not extra_note else [20 * mm, 70 * mm, 35 * mm, width - 125 * mm]
        return _table(rows, w, s)

    story += [Paragraph("1.4 Issues", s.h3),
              Paragraph(f"New this period: {iss['new_total']} · confirmed fixed by a follow-up audit: {iss['resolved_total']} "
                        f"· outstanding now: {iss['outstanding_total']}", s.body)]
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

    e = snap.get("expiry")
    if e:
        story.append(Paragraph("1.5 Domain, hosting and SSL", s.h3))

        def when(p):
            if not p["date"]:
                return "Unknown"
            dl = p["days_left"]
            return f"{_d(p['date'])} ({'expired' if dl < 0 else f'{dl} days left'})"

        rows = [["Item", "Expiry / renewal", "Source", "Details"],
                ["Domain" + (f"<br/>{t(e['domain']['name'])}" if e["domain"]["name"] else ""), when(e["domain"]),
                 SOURCE_LABEL.get(e["domain"]["source"], e["domain"]["source"]),
                 t(e["domain"]["registrar"] or e["domain"]["note"] or "")],
                ["Hosting", when(e["hosting"]), SOURCE_LABEL.get(e["hosting"]["source"], e["hosting"]["source"]),
                 t(e["hosting"]["provider"] or e["hosting"]["note"] or "")]]
        ssl = e["ssl"]
        if ssl["applicable"]:
            detail = ("NOT VALID: " + t(ssl["error"])) if ssl["valid"] is False else t(ssl["issuer"] or ssl["note"] or "")
            rows.append(["SSL certificate", when(ssl), SOURCE_LABEL["certificate"] if ssl["date"] else "Unknown", detail])
        else:
            rows.append(["SSL certificate", "-", "-", t(ssl["note"])])
        story.append(_table(rows, [30 * mm, 42 * mm, 42 * mm, width - 114 * mm], s))
        story.append(Paragraph("“Manual entry” dates were typed in by the team and have not been verified with the "
                               "registrar or hosting provider.", s.small))

    # ---- 2. maintenance -------------------------------------------------------------------------
    story += [Paragraph("2. Maintenance work performed", s.h2),
              Paragraph("Work recorded by the maintenance team during the period. These entries describe actions taken; "
                        "they are not automated measurements.", s.small)]
    if snap["maintenance"]:
        rows = [["Date", "Work", "Type"]] + [
            [_d(m["date"]), f"<b>{t(m['title'])}</b>" + (f"<br/>{t(m['description'])}" if m.get("description") else ""),
             t(m["category"])] for m in snap["maintenance"]]
        story.append(_table(rows, [25 * mm, width - 55 * mm, 30 * mm], s))
    else:
        story.append(Paragraph("No maintenance work was recorded for this period.", s.body))

    # ---- 3. recommendations -----------------------------------------------------------------------
    story.append(Paragraph("3. Recommendations", s.h2))
    if snap["recommendations"]:
        rows = [["Priority", "Recommended action", "Based on"]] + [
            [f"<b>{t(x['priority'].upper())}</b>", t(x["text"]), t(x["evidence"])] for x in snap["recommendations"]]
        story.append(_table(rows, [20 * mm, 75 * mm, width - 95 * mm], s))
    else:
        story.append(Paragraph("No corrective actions are recommended based on this period's data.", s.body))

    # ---- 4. comparison ------------------------------------------------------------------------------
    up_prev = up["previous"]
    rows = [["Measure", "This month", "Previous month", "Change"]]

    def change(cur, prev, unit="", better_low=False):
        if cur is None or prev is None:
            return "-"
        d = cur - prev
        if abs(d) < 1e-9:
            return "no change"
        good = (d < 0) if better_low else (d > 0)
        return f"{'+' if d > 0 else '−'}{abs(d):.2f}{unit} ({'better' if good else 'worse'})"

    rows.append(["Uptime", _pct(up["uptime_pct"]), _pct(up_prev["uptime_pct"]), change(up["uptime_pct"], up_prev["uptime_pct"], " pts")])
    rows.append(["Outages", str(len(up["incidents"])), str(up_prev["incidents"]), change(len(up["incidents"]), up_prev["incidents"], "", True)])
    rows.append(["Average response", f"{r['avg_ms']} ms" if r["avg_ms"] is not None else "No data",
                 f"{r['previous_avg_ms']} ms" if r["previous_avg_ms"] is not None else "No data",
                 change(r["avg_ms"], r["previous_avg_ms"], " ms", True)])
    if a and ap:
        hi, hp = a["by_severity"]["critical"] + a["by_severity"]["high"], ap["by_severity"]["critical"] + ap["by_severity"]["high"]
        rows.append(["Critical + high findings", str(hi), str(hp), change(hi, hp, "", True)])
    story += [KeepTogether([Paragraph("4. Comparison with the previous month", s.h2),
                            _table(rows, [50 * mm, 38 * mm, 38 * mm, width - 126 * mm], s)])]

    # ---- 5. method --------------------------------------------------------------------------------
    story += [Paragraph("5. Notes on method", s.h2)]
    for line in (
        "Availability is measured by automated HTTP(S) checks every 1–5 minutes from a single location. An outage is "
        "confirmed only after repeated failed checks; brief network problems between the monitor and the website can "
        "occasionally be recorded as downtime.",
        "Technical audits crawl the website within configured limits and respect robots.txt. Checks that were limited "
        "or could not run are listed in section 1.3; content generated by JavaScript is not audited.",
        "An issue is reported as fixed only when a subsequent audit re-checked it and no longer observed the problem.",
        "Domain expiry dates are retrieved from the registry (RDAP/WHOIS). Hosting renewal dates are generally not "
        "public and are shown as entered by the team unless verified with the provider.",
    ):
        story.append(Paragraph(f"• {t(line)}", s.small))

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page,
              canvasmaker=lambda *a, **kw: _NumberedCanvas(*a, footer_left=footer, **kw))
    return buf.getvalue()
