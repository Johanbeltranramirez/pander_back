"""Base común de las pruebas de integración de la API de importaciones."""
import re
import tempfile
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.candidatos.ia.lectores import leer_texto
from apps.candidatos.models import TipoDocumento
from apps.empresa.models import Compania

TIPOS = ["Cédula de Ciudadanía", "Cédula de Extranjería", "Pasaporte", "NIT",
         "Permiso por Protección Temporal"]


class ModeloFalso:
    """
    Sustituye al modelo BiLSTM-CRF en las pruebas rápidas: lee el archivo con los lectores reales
    y 'extrae' con expresiones regulares. Cuenta las llamadas para verificar la caché.
    """
    def __init__(self, disponible=True):
        self.llamadas = 0
        self._ok = disponible

    def motivo_no_disponible(self):
        return None if self._ok else "Modelo de prueba apagado."

    def estado(self):
        return {"disponible": self._ok, "motivo": self.motivo_no_disponible(), "version_modelo": "falso"}

    def firma(self):
        return "falso-1"

    def extraer_archivo(self, path):
        self.llamadas += 1
        texto = leer_texto(path)
        primera = next((ln for ln in texto.splitlines() if ln.strip()), "")
        primera = re.sub(r"^\s*Nombre:\s*", "", primera)
        tokens = primera.split()
        nombres = [None] * 4
        if 2 <= len(tokens) <= 4 and all(t[:1].isupper() for t in tokens):
            if len(tokens) == 4:
                nombres = tokens
            elif len(tokens) == 3:
                nombres = [tokens[0], None, tokens[1], tokens[2]]
            else:
                nombres = [tokens[0], None, tokens[1], None]
        email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", texto)
        tel = re.search(r"\b3\d{9}\b", texto)
        tipo = next((t for t in ("Tarjeta de Identidad", "Cédula de Ciudadanía", "Pasaporte") if t in texto), None)
        m_tipo = re.search(r"Tipo de documento:\s*(\w+)", texto)
        tipo = tipo or (m_tipo.group(1) if m_tipo else None)
        m_doc = re.search(r"(?:Ciudadanía|Identidad|Número de documento|Pasaporte):\s*(\d{5,15})", texto)
        return {"primer_nombre": nombres[0], "segundo_nombre": nombres[1],
                "primer_apellido": nombres[2], "segundo_apellido": nombres[3],
                "tipo_doc": tipo, "num_doc": m_doc.group(1) if m_doc else None,
                "email": email.group(0) if email else None, "telefono": tel.group(0) if tel else None,
                "_personas_detectadas": 1, "_fuente_nombre": "modelo"}


class BaseAPITest(APITestCase):
    """Compañía, catálogo de tipos de documento, MEDIA_ROOT temporal y caché limpia."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self._media = tempfile.TemporaryDirectory()
        self.addCleanup(self._media.cleanup)
        self.enterContext(override_settings(MEDIA_ROOT=self._media.name))
        self.compania = Compania.objects.create(descripcion="Acme S.A.", nit="900123", usuario_creacion=1)
        for d in TIPOS:
            TipoDocumento.objects.create(descripcion=d, usuario_creacion=1)

    def usar_modelo(self, modelo=None):
        modelo = modelo or ModeloFalso()
        self.enterContext(mock.patch("apps.candidatos.ia.servicio.get_extractor", return_value=modelo))
        return modelo
