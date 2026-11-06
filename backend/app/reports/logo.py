"""Company logo for reports: validation of uploads and drawing on light or dark pages.

SVG logos are drawn as vectors (svglib). A single-colour logo is recoloured automatically so it
stays visible: a white logo is drawn black on white pages and a dark logo white on the black cover.
Multi-colour logos that already contrast with the background are drawn unchanged.
"""
import os
import tempfile

from reportlab.graphics import renderPDF
from reportlab.lib import colors

MAX_SVG_BYTES = 1024 * 1024
_FORBIDDEN_TAGS = {"script", "foreignobject", "image", "iframe", "object", "embed", "audio", "video", "feimage"}


class LogoError(ValueError):
    pass


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


def validate_svg(data: bytes):
    """Reject anything but a self-contained vector drawing (no DTD/entities, scripts, external
    references or embedded documents). Returns the parsed reportlab Drawing."""
    from defusedxml import ElementTree
    from defusedxml.common import DefusedXmlException

    if len(data) > MAX_SVG_BYTES:
        raise LogoError("Logo must be 1 MB or smaller.")
    if b"<!DOCTYPE" in data[:2048].upper() or b"<!ENTITY" in data.upper():
        raise LogoError("SVG files with a DOCTYPE or entities are not accepted.")
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ElementTree.ParseError, DefusedXmlException, ValueError):
        raise LogoError("File is not a valid SVG image.") from None
    if _local(root.tag) != "svg":
        raise LogoError("File is not an SVG image.")
    for el in root.iter():
        tag = _local(el.tag) if isinstance(el.tag, str) else ""
        if tag in _FORBIDDEN_TAGS:
            raise LogoError(f"SVG logos may not contain <{tag}> elements.")
        for k, v in el.attrib.items():
            if _local(k) == "href" and not v.strip().startswith("#"):
                raise LogoError("SVG logos may not reference external files.")
            if _local(k).startswith("on"):
                raise LogoError("SVG logos may not contain scripts.")
        if tag == "style" and el.text and ("@import" in el.text or "url(" in el.text.replace("url(#", "")):
            raise LogoError("SVG logos may not reference external files.")
    drawing = _svg_drawing(data)
    if drawing is None or not drawing.width or not drawing.height:
        raise LogoError("The SVG could not be read.")
    return drawing


def _svg_drawing(data: bytes):
    from svglib.svglib import svg2rlg

    fd, path = tempfile.mkstemp(suffix=".svg")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return svg2rlg(path)
    finally:
        os.unlink(path)


def _lum(c) -> float:
    return 0.2126 * c.red + 0.7152 * c.green + 0.0722 * c.blue


def _shapes(node):
    for c in getattr(node, "contents", []) or []:
        yield c
        yield from _shapes(c)


class Logo:
    """Loaded once per report; draw() on any canvas."""

    def __init__(self, path: str | None, fallback_text: str):
        self.path = path if path and os.path.exists(path) else None
        self.fallback = fallback_text
        self.kind = None
        if self.path:
            self.kind = "svg" if self.path.lower().endswith(".svg") else "raster"
            if self.kind == "svg":
                with open(self.path, "rb") as f:
                    self._svg = f.read()
            else:
                from PIL import Image

                with Image.open(self.path) as im:
                    self._img = im.convert("RGBA")

    # -- helpers ---------------------------------------------------------------------------
    @staticmethod
    def _target(lum: float, dark_background: bool):
        """Colour to recolour to, or None to keep the logo as designed."""
        if dark_background and lum < 0.5:
            return colors.white
        if not dark_background and lum > 0.6:
            return colors.black
        return None

    def _draw_svg(self, c, x, y, height, dark):
        d = _svg_drawing(self._svg)
        fills = [s.fillColor for s in _shapes(d) if getattr(s, "fillColor", None) is not None]
        strokes = [s.strokeColor for s in _shapes(d) if getattr(s, "strokeColor", None) is not None]
        used = fills + strokes
        lum = sum(_lum(col) for col in used) / len(used) if used else 0.0
        target = self._target(lum, dark)
        if target is not None:
            for s in _shapes(d):
                if getattr(s, "fillColor", None) is not None:
                    s.fillColor = target
                if getattr(s, "strokeColor", None) is not None:
                    s.strokeColor = target
        scale = height / d.height
        c.saveState()
        c.translate(x, y)
        c.scale(scale, scale)
        renderPDF.draw(d, c, 0, 0)
        c.restoreState()
        return d.width * scale

    def _draw_raster(self, c, x, y, height, dark):
        from PIL import Image
        from reportlab.lib.utils import ImageReader

        img = self._img
        alpha = img.getchannel("A")
        has_transparency = alpha.getextrema()[0] < 250
        if has_transparency:
            px = [p for p in img.resize((min(img.width, 200), max(1, min(img.height, 80)))).getdata() if p[3] > 128]
            lum = sum((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 for r, g, b, _ in px) / len(px) if px else 0
            target = self._target(lum, dark)
            if target is not None:
                v = 255 if target == colors.white else 0
                solid = Image.new("RGBA", img.size, (v, v, v, 255))
                solid.putalpha(alpha)
                img = solid
        w = height * img.width / img.height
        c.drawImage(ImageReader(img), x, y, width=w, height=height, mask="auto")
        return w

    def draw(self, c, x: float, y: float, height: float, dark_background: bool, max_width: float | None = None) -> float:
        """Draw with the bottom-left corner at (x, y). Returns the drawn width."""
        try:
            if self.kind == "svg":
                return self._draw_svg(c, x, y, height, dark_background)
            if self.kind == "raster":
                return self._draw_raster(c, x, y, height, dark_background)
        except Exception:  # noqa: BLE001 - a broken logo must never break a report
            pass
        # No logo: the company name as a serif wordmark.
        c.saveState()
        c.setFillColor(colors.white if dark_background else colors.black)
        size = height * 1.05
        c.setFont("Times-Bold", size)
        text = (self.fallback or "")[:40]
        c.drawString(x, y + height * 0.12, text)
        c.restoreState()
        return c.stringWidth(text, "Times-Bold", size)

