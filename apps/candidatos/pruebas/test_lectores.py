"""Pruebas unitarias de lectura de texto por formato (apps.candidatos.ia.lectores)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from django.test import SimpleTestCase

from apps.candidatos.ia import lectores
from apps.candidatos.ia.lectores import LectorError, leer_texto

from . import utils

HAY_PDFPLUMBER = importlib.util.find_spec("pdfplumber") is not None
HAY_MARKITDOWN = importlib.util.find_spec("markitdown") is not None


class LectoresBase(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def archivo(self, nombre: str, contenido: bytes) -> Path:
        p = Path(self._tmp.name) / nombre
        p.write_bytes(contenido)
        return p


class TextoPlanoTests(LectoresBase):
    def test_txt_utf8(self):
        txt = leer_texto(self.archivo("cv.txt", utils.crear_txt(utils.CV_JUAN)))
        self.assertIn("juan.perez@example.com", txt)
        self.assertIn("Pérez", txt)

    def test_txt_con_bom(self):
        txt = leer_texto(self.archivo("cv.txt", b"\xef\xbb\xbf" + utils.crear_txt(utils.CV_JUAN)))
        self.assertTrue(txt.startswith("Juan Carlos"))

    def test_txt_windows_1252_conserva_tildes(self):
        """Antes se ignoraban los bytes inválidos y se perdían las tildes."""
        txt = leer_texto(self.archivo("cv.txt", utils.crear_txt(utils.CV_JUAN, "cp1252")))
        self.assertIn("Pérez Gómez", txt)

    def test_md(self):
        txt = leer_texto(self.archivo("cv.md", utils.crear_md(utils.CV_MARIA)))
        self.assertIn("maria.rodriguez@example.org", txt)

    def test_extension_en_mayusculas(self):
        txt = leer_texto(self.archivo("CV.TXT", utils.crear_txt(utils.CV_JUAN)))
        self.assertIn("Juan", txt)

    def test_archivo_vacio_o_solo_espacios(self):
        with self.assertRaises(LectorError):
            leer_texto(self.archivo("vacio.txt", b"  \n \n"))

    def test_extension_no_soportada(self):
        with self.assertRaises(LectorError):
            leer_texto(self.archivo("cv.exe", b"MZ..."))


class HtmlTests(LectoresBase):
    def test_html_extrae_texto_visible_y_omite_scripts_y_estilos(self):
        html = (b"<html><head><style>p{color:red}</style><script>var x='NO';</script></head>"
                b"<body><h1>Ana Torres</h1><p>ana@example.com</p><script>alert('NO')</script></body></html>")
        txt = leer_texto(self.archivo("cv.html", html))
        self.assertIn("ana@example.com", txt)
        self.assertNotIn("alert", txt)
        self.assertNotIn("color:red", txt)

    def test_html_nativo_directo(self):
        ruta = self.archivo("cv.htm", utils.crear_html(utils.CV_JUAN))
        txt = lectores._html_nativo(ruta)
        self.assertIn("juan.perez@example.com", txt)
        self.assertNotIn("color:red", txt)

    def test_html_tablas_se_separan_por_barra(self):
        html = b"<table><tr><td>Email</td><td>x@y.com</td></tr></table>"
        txt = lectores._html_nativo(self.archivo("t.html", html))
        self.assertIn("Email | x@y.com", txt)

    def test_htm(self):
        txt = leer_texto(self.archivo("cv.htm", utils.crear_html(utils.CV_MARIA)))
        self.assertIn("maria.rodriguez@example.org", txt)


class DocxTests(LectoresBase):
    def test_docx_nativo(self):
        ruta = self.archivo("cv.docx", utils.crear_docx(utils.CV_JUAN))
        self.assertIn("juan.perez@example.com", lectores._docx_nativo(ruta))

    def test_docx_nativo_incluye_encabezado(self):
        """Muchas plantillas ponen el contacto en el encabezado."""
        ruta = self.archivo("cv.docx", utils.crear_docx("Perfil profesional", encabezado="Correo: h@e.com"))
        txt = lectores._docx_nativo(ruta)
        self.assertIn("h@e.com", txt)
        self.assertLess(txt.index("h@e.com"), txt.index("Perfil"))

    @unittest.skipUnless(HAY_MARKITDOWN, "markitdown no instalado")
    def test_docx_por_ruta_principal(self):
        txt = leer_texto(self.archivo("cv.docx", utils.crear_docx(utils.CV_MARIA)))
        self.assertIn("maria.rodriguez@example.org", txt)

    def test_docx_corrupto(self):
        with self.assertRaises(LectorError):
            leer_texto(self.archivo("roto.docx", b"esto no es un zip"))

    def test_zip_sin_document_xml(self):
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("otro.txt", "x")
        with self.assertRaises(LectorError):
            leer_texto(self.archivo("falso.docx", buf.getvalue()))


@unittest.skipUnless(HAY_PDFPLUMBER, "pdfplumber no instalado")
class PdfTests(LectoresBase):
    def test_pdf_con_texto(self):
        txt = leer_texto(self.archivo("cv.pdf", utils.crear_pdf(utils.CV_JUAN)))
        self.assertIn("juan.perez@example.com", txt)
        self.assertIn("Pérez", txt)

    def test_pdf_sin_texto_exige_ocr_o_falla_con_mensaje(self):
        """Un PDF sin texto seleccionable no debe romper el proceso."""
        with self.assertRaises(LectorError):
            leer_texto(self.archivo("escaneado.pdf", utils.crear_pdf("")))

    def test_pdf_dañado(self):
        with self.assertRaises(LectorError):
            leer_texto(self.archivo("roto.pdf", b"%PDF-1.4 basura"))


@unittest.skipUnless(utils.hay_ocr(), "OCR (easyocr) no instalado")
class ImagenTests(LectoresBase):
    def test_png_y_jpg_con_ocr(self):
        for formato, nombre in (("PNG", "cv.png"), ("JPEG", "cv.jpg")):
            with self.subTest(formato):
                txt = leer_texto(self.archivo(nombre, utils.crear_imagen(utils.CV_JUAN, formato)))
                self.assertIn("example.com", txt.lower())


class MarcasMarkdownTests(LectoresBase):
    def test_quita_encabezados_markdown(self):
        self.assertEqual(lectores._quitar_marcas_markdown("# Juan Pérez\n## Perfil\ntexto # no se toca"),
                         "Juan Pérez\nPerfil\ntexto # no se toca")

    def test_md_con_titulo_llega_sin_almohadilla(self):
        txt = leer_texto(self.archivo("cv.md", b"# Juan Carlos Perez Gomez\n\ncorreo: j@x.com\n"))
        self.assertTrue(txt.startswith("Juan Carlos"))

    def test_no_altera_listas_ni_negritas(self):
        self.assertEqual(lectores._quitar_marcas_markdown("- **Email**: a@b.com"), "- **Email**: a@b.com")


class CorreosEnMayusculasTests(LectoresBase):
    """Regresión: la normalización original partía 'JUAN.PEREZ@X.COM' en dos líneas."""

    def test_correo_en_mayusculas_llega_completo(self):
        for escrito in ("JUAN.PEREZ@EXAMPLE.COM", "Juan.Perez@Example.com", "juan.PEREZ@gmail.com"):
            with self.subTest(correo=escrito):
                txt = leer_texto(self.archivo("cv.txt", f"Juan Pérez\nCorreo: {escrito}\n".encode()))
                self.assertIn(escrito.lower(), txt)

    def test_no_toca_el_resto_del_texto(self):
        self.assertEqual(lectores._emails_en_minuscula("Hola MUNDO A@B.COM FIN"), "Hola MUNDO a@b.com FIN")

    def test_correo_pegado_a_un_telefono_sigue_separandose(self):
        txt = leer_texto(self.archivo("cv.txt", b"Tel 3108880321JUAN@EXAMPLE.COM\n"))
        self.assertIn("3108880321 juan@example.com", txt)
