"""
Lectura de texto de una hoja de vida.

Formatos: PDF, DOCX, PNG, JPG/JPEG, HTML/HTM, MD y TXT.

Cada formato tiene una ruta principal (la misma que usa el script del modelo,
`ner_documentos.read_file`) y un respaldo con librerías estándar, de modo que
DOCX/HTML/TXT/MD sigan funcionando aunque `markitdown` no esté instalado.
Solo PNG/JPG y los PDF escaneados requieren OCR (`easyocr`).

El texto resultante pasa por `ner_documentos.normalizar_texto`, que es el mismo
paso previo con el que se entrenó el modelo.
"""
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from . import ner_documentos as ner

EXT_TEXTO = {".txt", ".md"}
EXT_HTML = {".html", ".htm"}
EXT_IMAGEN = {".png", ".jpg", ".jpeg"}
EXTENSIONES_PERMITIDAS = {".pdf", ".docx"} | EXT_IMAGEN | EXT_HTML | EXT_TEXTO

MIN_CARACTERES_PDF = 30          # menos que esto = PDF escaneado -> OCR
MAX_XML_DOCX = 20 * 1024 * 1024  # tope del document.xml descomprimido


class LectorError(Exception):
    """No se pudo extraer texto del archivo."""


class LectorNoDisponible(LectorError):
    """Falta una dependencia opcional (por ejemplo el OCR)."""


# ───────────────────────── utilidades ─────────────────────────
def _decodificar(datos: bytes) -> str:
    """UTF-8 (con o sin BOM); si falla, Windows-1252 y, en último caso, Latin-1."""
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            return datos.decode(codificacion)
        except UnicodeDecodeError:
            continue
    return datos.decode("latin-1")


def _leer_plano(path: Path) -> str:
    return _decodificar(path.read_bytes())


# ───────────────────────── HTML ─────────────────────────
class _HTMLTexto(HTMLParser):
    BLOQUE = {"p", "div", "br", "li", "tr", "ul", "ol", "table", "hr", "section", "article",
              "header", "footer", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"}
    IGNORAR = {"script", "style", "head", "noscript", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORAR:
            self._ignorar += 1
        elif tag in self.BLOQUE:
            self.partes.append("\n")
        elif tag in ("td", "th"):
            self.partes.append(" | ")

    def handle_endtag(self, tag):
        if tag in self.IGNORAR:
            self._ignorar = max(0, self._ignorar - 1)
        elif tag in self.BLOQUE:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self._ignorar:
            self.partes.append(data)


def _html_nativo(path: Path) -> str:
    parser = _HTMLTexto()
    parser.feed(_leer_plano(path))
    parser.close()
    texto = "".join(parser.partes)
    lineas = [re.sub(r"[ \t\u00a0]+", " ", ln).strip(" |").strip() for ln in texto.splitlines()]
    return "\n".join(ln for ln in lineas if ln)


# ───────────────────────── DOCX ─────────────────────────
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _texto_parrafo(p) -> str:
    partes = []
    for el in p.iter():
        if el.tag == _W + "t":
            partes.append(el.text or "")
        elif el.tag == _W + "tab":
            partes.append("\t")
        elif el.tag in (_W + "br", _W + "cr"):
            partes.append("\n")
    return "".join(partes)


def _lineas_xml_docx(xml: bytes) -> list[str]:
    raiz = ET.fromstring(xml)
    cuerpo = raiz if raiz.tag != _W + "document" else raiz.find(_W + "body")
    if cuerpo is None:
        return []
    lineas: list[str] = []
    for hijo in cuerpo:
        if hijo.tag == _W + "p":
            lineas.append(_texto_parrafo(hijo))
        elif hijo.tag == _W + "tbl":
            for fila in hijo.iter(_W + "tr"):
                celdas = []
                for celda in fila.findall(_W + "tc"):
                    celdas.append(" ".join(_texto_parrafo(p) for p in celda.iter(_W + "p")).strip())
                lineas.append(" | ".join(c for c in celdas if c))
    return lineas


def _docx_nativo(path: Path) -> str:
    """Encabezados (a menudo traen el contacto), cuerpo y pies del DOCX."""
    try:
        with zipfile.ZipFile(path) as z:
            nombres = z.namelist()
            if "word/document.xml" not in nombres:
                raise LectorError("El archivo no es un DOCX válido (falta word/document.xml).")
            orden = sorted(n for n in nombres if re.fullmatch(r"word/header\d*\.xml", n))
            orden += ["word/document.xml"]
            orden += sorted(n for n in nombres if re.fullmatch(r"word/footer\d*\.xml", n))
            lineas: list[str] = []
            for nombre in orden:
                info = z.getinfo(nombre)
                if info.file_size > MAX_XML_DOCX:
                    raise LectorError("El DOCX es demasiado grande para procesarlo.")
                lineas += _lineas_xml_docx(z.read(info))
    except zipfile.BadZipFile as e:
        raise LectorError("El archivo no es un DOCX válido.") from e
    except ET.ParseError as e:
        raise LectorError("El contenido XML del DOCX está dañado.") from e
    return "\n".join(ln for ln in lineas if ln.strip())


# ───────────────────────── markitdown (ruta del script del modelo) ─────────────────────────
def _markitdown(path: Path) -> str:
    return ner._markitdown(str(path))


def _con_respaldo(path: Path, principal, respaldo, nombre: str) -> str:
    """Intenta la ruta del script del modelo y, si falla o queda vacía, el respaldo nativo."""
    for fn in (principal, respaldo):
        try:
            texto = fn(path)
        except LectorError:
            raise
        except Exception:
            continue
        if texto and texto.strip():
            return texto
    raise LectorError(f"No se pudo leer el contenido del archivo {nombre}.")


# ───────────────────────── PDF ─────────────────────────
def _pdf_pdfplumber(path: Path, max_paginas: int) -> str:
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        return "\n\n".join((pg.extract_text() or "") for pg in pdf.pages[:max_paginas])


def _pdf_ocr(path: Path, max_paginas: int) -> str:
    try:
        import numpy as np
        import pdfplumber
        import easyocr  # noqa: F401  (comprueba que el OCR exista)
    except ImportError as e:
        raise LectorNoDisponible(
            "El PDF no tiene texto seleccionable (parece escaneado) y el OCR no está "
            "instalado. Instale easyocr.") from e
    with pdfplumber.open(str(path)) as pdf:
        paginas = [np.array(pg.to_image(resolution=200).original.convert("RGB"))
                   for pg in pdf.pages[:max_paginas]]
    return "\n\n".join(ner._ocr(arr) for arr in paginas)


def _leer_pdf(path: Path, max_paginas: int) -> str:
    texto = ""
    for fn in (lambda: _pdf_pdfplumber(path, max_paginas), lambda: _markitdown(path)):
        try:
            texto = fn()
        except Exception:
            texto = ""
        if len(texto.strip()) >= MIN_CARACTERES_PDF:
            return texto
    try:
        return _pdf_ocr(path, max_paginas)
    except LectorNoDisponible:
        raise
    except Exception as e:
        raise LectorError(
            "No se pudo leer el PDF (puede estar protegido con contraseña o dañado).") from e


# ───────────────────────── imágenes ─────────────────────────
def _leer_imagen(path: Path) -> str:
    try:
        import easyocr  # noqa: F401
    except ImportError as e:
        raise LectorNoDisponible(
            "Para leer imágenes (PNG/JPG) se requiere OCR. Instale easyocr.") from e
    try:
        return ner._ocr(ner._cargar_imagen(str(path)))
    except Exception as e:
        raise LectorError("No se pudo leer la imagen (archivo dañado o sin texto legible).") from e


# ───────────────────────── preparación del texto ─────────────────────────
_ENCABEZADO_MD = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+")


def _quitar_marcas_markdown(texto: str) -> str:
    """
    Quita los '#' de los encabezados Markdown ('# Juan Pérez' -> 'Juan Pérez').
    El modelo no reconoce el nombre cuando va precedido por '#', y markitdown genera
    esos encabezados a partir de los títulos de DOCX/HTML.
    """
    return _ENCABEZADO_MD.sub("", texto)


def _emails_en_minuscula(texto: str) -> str:
    """
    `ner_documentos.normalizar_texto` parte los correos escritos en MAYÚSCULAS
    ('JUAN.PEREZ@X.COM' -> 'JUAN.' + 'PEREZ@X.COM') porque los confunde con el final de una
    frase pegada al correo, y el modelo terminaría devolviendo 'perez@x.com'.
    Un correo no distingue mayúsculas, así que se pasa a minúsculas antes de normalizar.
    """
    return ner.EMAIL_RE.sub(lambda m: m.group(0).lower(), texto)


# ───────────────────────── validación estructural ─────────────────────────
def _validar_docx(path: Path) -> None:
    """markitdown trata como texto un DOCX corrupto; se valida antes de leerlo."""
    try:
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                raise LectorError("El archivo no es un DOCX válido (falta word/document.xml).")
    except zipfile.BadZipFile as e:
        raise LectorError("El archivo no es un DOCX válido.") from e


def _validar_pdf(path: Path) -> None:
    with path.open("rb") as f:
        if b"%PDF" not in f.read(1024):
            raise LectorError("El archivo no es un PDF válido.")


# ───────────────────────── API pública ─────────────────────────
def leer_texto(path, max_paginas: int = 10) -> str:
    """Devuelve el texto normalizado del archivo según su extensión."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in EXTENSIONES_PERMITIDAS:
        raise LectorError(f"Formato '{ext}' no soportado.")

    if ext in EXT_TEXTO:
        texto = _leer_plano(path)
    elif ext in EXT_HTML:
        texto = _con_respaldo(path, _markitdown, _html_nativo, "HTML")
    elif ext == ".docx":
        _validar_docx(path)
        texto = _con_respaldo(path, _markitdown, _docx_nativo, "DOCX")
    elif ext == ".pdf":
        _validar_pdf(path)
        texto = _leer_pdf(path, max_paginas)
    else:
        texto = _leer_imagen(path)

    texto = ner.normalizar_texto(_emails_en_minuscula(_quitar_marcas_markdown(texto)))
    if not texto.strip():
        raise LectorError("No se encontró texto legible en el archivo.")
    return texto
