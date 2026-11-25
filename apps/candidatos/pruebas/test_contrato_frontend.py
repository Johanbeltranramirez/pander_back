"""
Contrato entre la API de hojas de vida y la pantalla React (ImportacionCV.jsx / cvImport.js).
Si alguna de estas pruebas se rompe, hay que revisar el frontend antes de cambiar el backend.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.candidatos.models import Candidato, DatosCandidato

from . import utils
from .base import BaseAPITest

CAMPOS_LOG = {"fila", "archivo", "estado", "mensaje", "datos", "advertencias"}
CAMPOS_DATOS = {"primer_nombre", "segundo_nombre", "primer_apellido", "segundo_apellido",
                "tipo_documento", "numero_documento", "email", "telefono"}


def cv(nombre_archivo, persona, correo, doc):
    """Hoja de vida .txt de una persona distinta (el correo y el documento no se repiten)."""
    texto = (f"{persona}\nAnalista\n\nCorreo electrónico: {correo}\nTeléfono: 3124567890\n"
             f"Cédula de Ciudadanía: {doc}\n" if correo else
             f"{persona}\nAnalista\n\nTeléfono: 3124567890\nCédula de Ciudadanía: {doc}\n")
    return SimpleUploadedFile(nombre_archivo, utils.crear_txt(texto), content_type="text/plain")


class ContratoFrontendTests(BaseAPITest):
    def setUp(self):
        super().setUp()
        self.usar_modelo()
        self.url = reverse("imp-candidatos-cv", kwargs={"compania": self.compania.pk})

    def enviar(self, archivos, **datos):
        """Igual que construirFormData() del frontend: campo 'archivos' repetido + banderas como texto."""
        for f in archivos:
            f.seek(0)
        return self.client.post(self.url, {"archivos": archivos, **datos}, format="multipart")

    def test_estado_trae_lo_que_lee_la_pantalla(self):
        r = self.client.get(reverse("imp-cv-estado"))
        self.assertEqual(r.status_code, 200)
        for clave in ("disponible", "motivo", "formatos", "max_archivos", "max_mb"):
            self.assertIn(clave, r.data)
        self.assertIsInstance(r.data["max_archivos"], int)
        self.assertIsInstance(r.data["max_mb"], int)

    def test_cada_entrada_del_log_trae_las_claves_que_pinta_la_pantalla(self):
        r = self.enviar([cv("ana.txt", "Ana María Gil Soto", "ana@example.com", "111111"),
                         cv("sin.txt", "Pedro Luis Ríos Mora", None, "222222")],
                        dry_run="true", omitir_errores="true")
        self.assertEqual(r.status_code, 200)
        for esperado_fila, e in enumerate(r.data["log"], start=1):
            self.assertEqual(e["fila"], esperado_fila)                # posición dentro de la petición
            self.assertTrue(CAMPOS_LOG <= set(e), set(e))
            self.assertIsInstance(e["advertencias"], list)
        ok, error = r.data["log"]
        self.assertEqual((ok["estado"], error["estado"]), ("OK", "ERROR"))
        self.assertEqual(set(ok["datos"]), CAMPOS_DATOS)
        # en un error igual se devuelve lo que la IA leyó, para que el usuario lo revise
        self.assertEqual(set(error["datos"]), CAMPOS_DATOS)
        self.assertIsNone(error["datos"]["email"])
        self.assertNotIn("candidato_id", ok)                          # el análisis no crea nada

    def test_al_importar_cada_candidato_creado_trae_candidato_id(self):
        r = self.enviar([cv("ana.txt", "Ana María Gil Soto", "ana@example.com", "111111")],
                        dry_run="false", omitir_errores="true", usuario_id="4")
        self.assertEqual(r.status_code, 201)
        e = r.data["log"][0]
        self.assertTrue(Candidato.objects.filter(pk=e["candidato_id"]).exists())
        self.assertEqual(Candidato.objects.get(pk=e["candidato_id"]).usuario_creacion, 4)

    def test_sin_usuario_id_se_usa_el_de_por_defecto(self):
        # el frontend no manda usuario_id si la sesión no lo trae
        r = self.enviar([cv("ana.txt", "Ana María Gil Soto", "ana@example.com", "111111")])
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Candidato.objects.get().usuario_creacion, 1)

    def test_flujo_completo_por_lotes_como_lo_hace_la_pantalla(self):
        nombres = ["Ana María Gil Soto", "Luis Pablo Ríos Mora", "Rosa Elena Paz Rey",
                   "Mario José Vega Luna", "Clara Inés Roa Díaz"]       # sin dígitos: son nombres válidos
        personas = [(f"cv{i}.txt", nombre, f"p{i}@example.com", f"{100000 + i}")
                    for i, nombre in enumerate(nombres, start=1)]
        lotes = [personas[0:2], personas[2:4], personas[4:5]]

        # 1) analizar: cada lote reinicia la fila en 1 y no se guarda nada
        for lote in lotes:
            r = self.enviar([cv(*p) for p in lote], dry_run="true")
            self.assertEqual(r.status_code, 200)
            self.assertEqual([e["fila"] for e in r.data["log"]], list(range(1, len(lote) + 1)))
        self.assertEqual(Candidato.objects.count(), 0)

        # 2) importar lote por lote: el id_interno sigue la secuencia entre peticiones
        for lote in lotes:
            r = self.enviar([cv(*p) for p in lote], dry_run="false")
            self.assertEqual(r.status_code, 201)
            self.assertEqual(r.data["creados"], len(lote))
        self.assertEqual(sorted(Candidato.objects.values_list("id_interno", flat=True)), [1, 2, 3, 4, 5])

        # 3) volver a mandar un archivo ya importado en un lote posterior: error claro, nada se duplica
        r = self.enviar([cv(*personas[0])], dry_run="true")
        e = r.data["log"][0]
        self.assertEqual(e["estado"], "ERROR")
        self.assertIn("Ya existe", e["mensaje"])
        self.assertEqual(DatosCandidato.objects.count(), 5)

    def test_si_todo_falla_el_422_trae_error_y_log_por_archivo(self):
        r = self.enviar([cv("sin1.txt", "Pedro Luis Ríos Mora", None, "1"),
                         cv("sin2.txt", "Rosa Elena Paz Rey", None, "2")], dry_run="false")
        self.assertEqual(r.status_code, 422)
        self.assertIn("error", r.data)
        self.assertEqual([e["estado"] for e in r.data["log"]], ["ERROR", "ERROR"])
        self.assertEqual(Candidato.objects.count(), 0)

    def test_modo_estricto_no_importa_nada_si_hay_un_error(self):
        r = self.enviar([cv("ana.txt", "Ana María Gil Soto", "ana@example.com", "111111"),
                         cv("sin.txt", "Pedro Luis Ríos Mora", None, "222222")],
                        dry_run="false", omitir_errores="false")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(Candidato.objects.count(), 0)
        self.assertEqual([e["estado"] for e in r.data["log"]], ["OK", "ERROR"])
