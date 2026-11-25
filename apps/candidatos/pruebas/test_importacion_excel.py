"""
Regresión de la importación por matriz de Excel (debe seguir funcionando) y exigencia
del campo 'email' en la importación de candidatos.
"""
import io

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.candidatos.models import Candidato, DatosCandidato
from apps.empresa.models import UnidadOrg

from .base import BaseAPITest

CABECERA = ["primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
            "tipo_documento", "numero_documento", "email", "telefono"]


def excel(cabecera, filas, nombre="candidatos.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(cabecera)
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(nombre, buf.getvalue(),
                              content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


FILA_OK = ["Carlos", "Andrés", "Ramírez", "Torres", "Cédula de Ciudadanía", "1234567890",
           "Carlos@Email.com", "3209876543"]


class ExcelCandidatosTests(BaseAPITest):
    def url(self, compania=None):
        return reverse("imp-candidatos", kwargs={"compania": compania or self.compania.pk})

    def post(self, archivo, **datos):
        return self.client.post(self.url(), {"archivo": archivo, **datos}, format="multipart")

    def test_la_plantilla_sigue_descargandose_y_trae_email(self):
        r = self.client.get(reverse("plantilla-excel", kwargs={"entidad": "candidatos"}))
        self.assertEqual(r.status_code, 200)
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        self.assertIn("email", [c.value for c in ws[1]])

    def test_validacion_ok(self):
        r = self.post(excel(CABECERA, [FILA_OK]), dry_run="true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.data["modo"], r.data["total"], r.data["ok"], r.data["errores"]),
                         ("validacion", 1, 1, 0))

    def test_importa_y_normaliza_el_email(self):
        r = self.post(excel(CABECERA, [FILA_OK]), usuario_id="3")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["creados"], 1)
        datos = DatosCandidato.objects.get()
        self.assertEqual(datos.email, "carlos@email.com")
        self.assertEqual(datos.tipo_documento.descripcion, "Cédula de Ciudadanía")
        self.assertEqual(Candidato.objects.get().usuario_creacion, 3)

    def test_sin_la_columna_email_se_rechaza_el_archivo(self):
        cab = [c for c in CABECERA if c != "email"]
        fila = [v for c, v in zip(CABECERA, FILA_OK) if c != "email"]
        r = self.post(excel(cab, [fila]), dry_run="true")
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data["error"])

    def test_fila_sin_email(self):
        fila = list(FILA_OK)
        fila[6] = None
        r = self.post(excel(CABECERA, [fila]), dry_run="true")
        self.assertEqual(r.data["errores"], 1)
        self.assertEqual(r.data["log"][0]["mensaje"], "email es obligatorio")

    def test_email_con_formato_invalido(self):
        fila = list(FILA_OK)
        fila[6] = "no-es-un-correo"
        r = self.post(excel(CABECERA, [fila]), dry_run="true")
        self.assertIn("no tiene un formato válido", r.data["log"][0]["mensaje"])

    def test_nombre_y_apellido_siguen_siendo_obligatorios(self):
        fila = list(FILA_OK)
        fila[0] = None
        r = self.post(excel(CABECERA, [fila]), dry_run="true")
        self.assertIn("primer_nombre y primer_apellido son obligatorios", r.data["log"][0]["mensaje"])

    def test_con_errores_no_se_importa_nada(self):
        mala = list(FILA_OK)
        mala[6] = None
        r = self.post(excel(CABECERA, [FILA_OK, mala]))
        self.assertEqual(r.status_code, 422)
        self.assertEqual(Candidato.objects.count(), 0)

    def test_tipo_de_documento_inexistente_sigue_siendo_error(self):
        fila = list(FILA_OK)
        fila[4] = "Licencia"
        r = self.post(excel(CABECERA, [fila]), dry_run="true")
        self.assertEqual(r.data["errores"], 1)

    def test_compania_inexistente(self):
        r = self.client.post(self.url(999999), {"archivo": excel(CABECERA, [FILA_OK])}, format="multipart")
        self.assertEqual(r.status_code, 404)

    def test_sin_archivo(self):
        r = self.client.post(self.url(), {}, format="multipart")
        self.assertEqual(r.status_code, 400)


class ExcelOtrasEntidadesNoSeRompenTests(BaseAPITest):
    def test_unidades_se_importan_igual_que_antes(self):
        url = reverse("imp-unidades", kwargs={"compania": self.compania.pk})
        r = self.client.post(url, {"archivo": excel(["descripcion", "especialidad"],
                                                    [["Tecnología", "Software"], ["Finanzas", None]])},
                             format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["creados"], 2)
        self.assertEqual(UnidadOrg.objects.filter(compania=self.compania).count(), 2)

    def test_companias_se_validan_sin_compania_en_la_url(self):
        r = self.client.post(reverse("imp-companias"), {
            "archivo": excel(["descripcion", "nit"], [["Nueva S.A.", "811222333"]]), "dry_run": "true"},
            format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["ok"], 1)

    def test_usuario_id_invalido_no_rompe_la_importacion(self):
        url = reverse("imp-unidades", kwargs={"compania": self.compania.pk})
        r = self.client.post(url, {"archivo": excel(["descripcion"], [["Legal"]]), "usuario_id": ""},
                             format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
