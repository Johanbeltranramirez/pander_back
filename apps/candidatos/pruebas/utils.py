"""
Utilidades de prueba: generan hojas de vida sintéticas en todos los formatos permitidos
sin depender de librerías externas (PDF y DOCX se arman a mano; PNG/JPG con Pillow).
"""
import importlib.util
import io
import zipfile
from xml.sax.saxutils import escape

# ── CVs de ejemplo (formato libre y con etiquetas) ──────────────────────────
CV_JUAN = """Juan Carlos Pérez Gómez
Ingeniero de Sistemas

Datos de contacto
Correo electrónico: juan.perez@example.com
Teléfono: 3124567890
Cédula de Ciudadanía: 1020304050

Perfil profesional
Desarrollador de software con 6 años de experiencia en Python y Django.
"""

CV_MARIA = """Nombre: María Fernanda Rodríguez Vargas
Tipo de documento: CC
Número de documento: 52987654
Email: maria.rodriguez@example.org
Celular: 3001234567

Experiencia laboral
Analista de datos en Acme S.A. (2019 - 2024)
"""

CV_SIN_EMAIL = """Pedro Antonio Ramírez Soto
Teléfono: 3157778899
Cédula de Ciudadanía: 79123456

Contador público con experiencia en auditoría.
"""

CV_SIN_NADA = """Lorem ipsum dolor sit amet, consectetur adipiscing elit.
Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.
"""


def _lineas(texto):
    return [ln for ln in texto.splitlines()]


# ── TXT / MD / HTML ─────────────────────────────────────────────────────────
def crear_txt(texto: str, codificacion="utf-8") -> bytes:
    return texto.encode(codificacion)


def crear_md(texto: str) -> bytes:
    lineas = _lineas(texto)
    md = [f"# {lineas[0]}", ""] + [f"- {ln}" if ln.strip() and i > 0 else ln
                                   for i, ln in enumerate(lineas[1:], start=1)]
    return "\n".join(md).encode("utf-8")


def crear_html(texto: str) -> bytes:
    cuerpo = "".join(f"<p>{escape(ln)}</p>\n" for ln in _lineas(texto) if ln.strip())
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>CV</title>"
            f"<style>p{{color:red}}</style></head><body>{cuerpo}</body></html>").encode("utf-8")


# ── DOCX ────────────────────────────────────────────────────────────────────
_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def crear_docx(texto: str, encabezado: str | None = None) -> bytes:
    def parrafos(t):
        return "".join(
            f'<w:p><w:r><w:t xml:space="preserve">{escape(ln)}</w:t></w:r></w:p>'
            for ln in _lineas(t) if ln.strip())

    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{_NS}" xmlns:r="http://schemas.openxmlformats.org/'
                f'officeDocument/2006/relationships"><w:body>{parrafos(texto)}'
                f'<w:sectPr/></w:body></w:document>')
    tipos = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/'
             'package/2006/content-types"><Default Extension="rels" ContentType="application/'
             'vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType='
             '"application/xml"/><Override PartName="/word/document.xml" ContentType="application/'
             'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        if encabezado:
            z.writestr("word/header1.xml",
                       f'<?xml version="1.0" encoding="UTF-8"?><w:hdr xmlns:w="{_NS}">'
                       f'{parrafos(encabezado)}</w:hdr>')
    return buf.getvalue()


# ── PDF (generado a mano, con texto seleccionable) ──────────────────────────
def crear_pdf(texto: str) -> bytes:
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    flujo = "BT /F1 11 Tf 50 780 Td 15 TL\n"
    for ln in _lineas(texto):
        flujo += f"({esc(ln)}) Tj T*\n"
    flujo += "ET"
    flujo_b = flujo.encode("cp1252", errors="replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(flujo_b) + flujo_b + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return bytes(out)


# ── Imágenes (requieren Pillow; el OCR requiere además easyocr) ─────────────
def hay_pillow() -> bool:
    return importlib.util.find_spec("PIL") is not None


def hay_ocr() -> bool:
    return importlib.util.find_spec("easyocr") is not None and hay_pillow()


def crear_imagen(texto: str, formato="PNG") -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    try:
        fuente = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        fuente = ImageFont.load_default(size=30)
    lineas = _lineas(texto)
    img = Image.new("RGB", (1400, 60 + 46 * len(lineas)), "white")
    d = ImageDraw.Draw(img)
    for i, ln in enumerate(lineas):
        d.text((40, 30 + 46 * i), ln, fill="black", font=fuente)
    buf = io.BytesIO()
    img.save(buf, formato)
    return buf.getvalue()


def hay_modelo_real() -> bool:
    """¿Se puede ejecutar el modelo entrenado en este entorno?"""
    from apps.candidatos.ia.extractor import ExtractorCV
    return ExtractorCV().disponible()
