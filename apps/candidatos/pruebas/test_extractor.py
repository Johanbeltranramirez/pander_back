"""
Pruebas del extractor.
 * Unitarias: disponibilidad, firma, singleton (no requieren PyTorch).
 * Integración: modelo BiLSTM-CRF real sobre todos los formatos (se omiten si no hay PyTorch).
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from apps.candidatos.ia import extractor as ext
from apps.candidatos.ia.extractor import ExtractorCV, ModeloNoDisponible

from . import utils

HAY_PDFPLUMBER = importlib.util.find_spec("pdfplumber") is not None


class DisponibilidadTests(SimpleTestCase):
    def test_sin_checkpoint_no_esta_disponible(self):
        e = ExtractorCV(ruta_modelo="/no/existe/modelo.pt")
        self.assertFalse(e.disponible())
        self.assertIn("/no/existe/modelo.pt", e.motivo_no_disponible())
        self.assertFalse(e.estado()["disponible"])

    def test_extraer_sin_modelo_lanza_excepcion_clara(self):
        with self.assertRaises(ModeloNoDisponible):
            ExtractorCV(ruta_modelo="/no/existe/modelo.pt").extraer_texto("hola")

    def test_sin_torch_lo_indica(self):
        with tempfile.NamedTemporaryFile(suffix=".pt") as f:
            real = importlib.util.find_spec
            with mock.patch("importlib.util.find_spec",
                            side_effect=lambda n, *a: None if n == "torch" else real(n, *a)):
                motivo = ExtractorCV(ruta_modelo=f.name).motivo_no_disponible()
        self.assertIn("torch", motivo)

    def test_sin_pytorch_crf_lo_indica(self):
        with tempfile.NamedTemporaryFile(suffix=".pt") as f:
            real = importlib.util.find_spec
            with mock.patch("importlib.util.find_spec",
                            side_effect=lambda n, *a: None if n == "torchcrf" else real(n, *a)):
                motivo = ExtractorCV(ruta_modelo=f.name).motivo_no_disponible()
        self.assertIn("pytorch-crf", motivo)

    def test_firma_cambia_si_cambia_el_modelo(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.pt"
            p.write_bytes(b"a")
            f1 = ExtractorCV(ruta_modelo=p).firma()
            p.write_bytes(b"abc")
            self.assertNotEqual(f1, ExtractorCV(ruta_modelo=p).firma())

    def test_singleton(self):
        ext.reiniciar_extractor()
        a, b = ext.get_extractor(), ext.get_extractor()
        self.assertIs(a, b)
        ext.reiniciar_extractor()
        self.assertIsNot(a, ext.get_extractor())
        ext.reiniciar_extractor()


@unittest.skipUnless(utils.hay_modelo_real(), "PyTorch / pytorch-crf / checkpoint no disponibles")
class ModeloRealTests(SimpleTestCase):
    """Integración con el modelo entrenado de verdad (Modelo_RNN)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ex = ExtractorCV()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _extraer(self, nombre, contenido):
        p = Path(self._tmp.name) / nombre
        p.write_bytes(contenido)
        return self.ex.extraer_archivo(p)

    def test_texto_libre(self):
        r = self.ex.extraer_texto(utils.CV_JUAN)
        self.assertEqual((r["primer_nombre"], r["segundo_nombre"]), ("Juan", "Carlos"))
        self.assertEqual((r["primer_apellido"], r["segundo_apellido"]), ("Pérez", "Gómez"))
        self.assertEqual(r["email"], "juan.perez@example.com")
        self.assertEqual(r["num_doc"], "1020304050")
        self.assertEqual(r["telefono"], "3124567890")

    def test_texto_con_etiquetas(self):
        r = self.ex.extraer_texto(utils.CV_MARIA)
        self.assertEqual(r["primer_nombre"], "María")
        self.assertEqual(r["primer_apellido"], "Rodríguez")
        self.assertEqual(r["email"], "maria.rodriguez@example.org")

    def test_sin_correo_no_inventa_uno(self):
        self.assertIsNone(self.ex.extraer_texto(utils.CV_SIN_EMAIL)["email"])

    def test_texto_sin_datos(self):
        r = self.ex.extraer_texto(utils.CV_SIN_NADA)
        self.assertIsNone(r["email"])
        self.assertIsNone(r["primer_nombre"])

    def test_todos_los_formatos_de_texto_dan_el_mismo_resultado(self):
        formatos = {
            "cv.txt": utils.crear_txt(utils.CV_JUAN),
            "cv.md": utils.crear_md(utils.CV_JUAN),
            "cv.html": utils.crear_html(utils.CV_JUAN),
            "cv.docx": utils.crear_docx(utils.CV_JUAN),
        }
        if HAY_PDFPLUMBER:
            formatos["cv.pdf"] = utils.crear_pdf(utils.CV_JUAN)
        for nombre, contenido in formatos.items():
            with self.subTest(formato=nombre):
                r = self._extraer(nombre, contenido)
                self.assertEqual(r["email"], "juan.perez@example.com")
                self.assertEqual(r["primer_nombre"], "Juan")
                self.assertEqual(r["primer_apellido"], "Pérez")
                self.assertEqual(r["num_doc"], "1020304050")

    @unittest.skipUnless(utils.hay_ocr(), "OCR (easyocr) no instalado")
    def test_imagenes_con_ocr(self):
        for formato, nombre in (("PNG", "cv.png"), ("JPEG", "cv.jpg")):
            with self.subTest(formato=formato):
                r = self._extraer(nombre, utils.crear_imagen(utils.CV_JUAN, formato))
                self.assertIn("example.com", (r["email"] or "").lower())
