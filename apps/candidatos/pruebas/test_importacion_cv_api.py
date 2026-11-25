"""
Pruebas de integración del endpoint de importación de candidatos desde hojas de vida
(vista -> servicio -> lectores -> mapeo -> BD -> archivos en MEDIA_ROOT).
"""
import unittest
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.candidatos.models import AnexoCandidato, Candidato, DatosCandidato

from . import utils
from .base import BaseAPITest, ModeloFalso


def subir(nombre, contenido):
    return SimpleUploadedFile(nombre, contenido, content_type="application/octet-stream")


class CVApiBase(BaseAPITest):
    def url(self, compania=None):
        return reverse("imp-candidatos-cv", kwargs={"compania": compania or self.compania.pk})

    def post(self, archivos, **datos):
        for f in archivos:
            f.seek(0)      # el cliente de pruebas consume el archivo; permite reenviar el mismo objeto
        return self.client.post(self.url(), {"archivos": archivos, **datos}, format="multipart")


class EstadoTests(CVApiBase):
    def test_estado_disponible(self):
        self.usar_modelo()
        r = self.client.get(reverse("imp-cv-estado"))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["disponible"])
        self.assertEqual(set(r.data["formatos"]),
                         {"pdf", "docx", "png", "jpg", "jpeg", "html", "htm", "md", "txt"})
        self.assertEqual(r.data["max_archivos"], 50)
        self.assertEqual(r.data["max_mb"], 10)

    def test_estado_no_disponible_informa_el_motivo(self):
        self.usar_modelo(ModeloFalso(disponible=False))
        r = self.client.get(reverse("imp-cv-estado"))
        self.assertFalse(r.data["disponible"])
        self.assertIn("apagado", r.data["motivo"])


class ValidacionDeEntradaTests(CVApiBase):
    def setUp(self):
        super().setUp()
        self.modelo = self.usar_modelo()

    def test_sin_archivos(self):
        r = self.client.post(self.url(), {}, format="multipart")
        self.assertEqual(r.status_code, 400)

    def test_compania_inexistente(self):
        r = self.client.post(self.url(999999), {"archivos": [subir("a.txt", b"x")]}, format="multipart")
        self.assertEqual(r.status_code, 404)

    def test_compania_con_id_invalido(self):
        r = self.client.post(self.url("abc"), {"archivos": [subir("a.txt", b"x")]}, format="multipart")
        self.assertEqual(r.status_code, 404)

    def test_modelo_no_disponible_devuelve_503(self):
        self.usar_modelo(ModeloFalso(disponible=False))
        r = self.post([subir("a.txt", utils.crear_txt(utils.CV_JUAN))])
        self.assertEqual(r.status_code, 503)
        self.assertFalse(r.data["disponible"])

    @override_settings(CV_MAX_ARCHIVOS=2)
    def test_demasiados_archivos(self):
        r = self.post([subir(f"{i}.txt", utils.crear_txt(utils.CV_JUAN)) for i in range(3)])
        self.assertEqual(r.status_code, 400)
        self.assertIn("Máximo 2", r.data["error"])

    def test_formato_no_permitido(self):
        r = self.post([subir("virus.exe", b"MZ"), subir("hoja.xlsx", b"PK")], dry_run="true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([e["estado"] for e in r.data["log"]], ["ERROR", "ERROR"])
        self.assertIn("no permitido", r.data["log"][0]["mensaje"])

    def test_archivo_vacio(self):
        r = self.post([subir("vacio.txt", b"")], dry_run="true")
        self.assertIn("vacío", r.data["log"][0]["mensaje"])

    @override_settings(CV_MAX_BYTES=1000)
    def test_archivo_demasiado_grande(self):
        r = self.post([subir("grande.txt", b"a" * 5000)], dry_run="true")
        self.assertIn("tamaño máximo", r.data["log"][0]["mensaje"])

    def test_contenido_no_corresponde_a_la_extension(self):
        r = self.post([subir("falso.pdf", b"esto es texto"), subir("falso.png", b"no es png"),
                       subir("falso.docx", b"no es docx"), subir("binario.txt", b"\x00\x01\x02" * 100)],
                      dry_run="true")
        mensajes = [e["mensaje"] for e in r.data["log"]]
        self.assertIn("PDF", mensajes[0])
        self.assertIn("PNG", mensajes[1])
        self.assertIn("DOCX", mensajes[2])
        self.assertIn("no es de texto", mensajes[3])
        self.assertEqual(self.modelo.llamadas, 0)   # ni siquiera se invoca al modelo

    def test_un_archivo_malo_no_afecta_a_los_demas(self):
        r = self.post([subir("malo.exe", b"x"), subir("ok.txt", utils.crear_txt(utils.CV_JUAN))],
                      dry_run="true")
        self.assertEqual((r.data["total"], r.data["ok"], r.data["errores"]), (2, 1, 1))

    def test_nombre_con_ruta_se_reduce_al_nombre_base(self):
        r = self.post([subir("../../etc/passwd.txt", utils.crear_txt(utils.CV_JUAN))], dry_run="true")
        self.assertEqual(r.data["log"][0]["archivo"], "passwd.txt")


class AnalisisTests(CVApiBase):
    def setUp(self):
        super().setUp()
        self.modelo = self.usar_modelo()

    def archivos_todos_los_formatos(self):
        return [
            subir("juan.txt", utils.crear_txt(utils.CV_JUAN)),
            subir("maria.md", utils.crear_md(utils.CV_MARIA)),
            subir("juan2.html", utils.crear_html(utils.CV_JUAN.replace("juan.perez", "juan.otro")
                                                 .replace("1020304050", "3333333333"))),
            subir("maria2.docx", utils.crear_docx(utils.CV_MARIA.replace("maria.rodriguez", "maria.otra")
                                                  .replace("52987654", "4444444444"))),
        ]

    def test_dry_run_no_escribe_en_la_bd(self):
        r = self.post(self.archivos_todos_los_formatos(), dry_run="true")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["modo"], "validacion")
        self.assertEqual((r.data["total"], r.data["ok"], r.data["errores"]), (4, 4, 0))
        self.assertEqual(Candidato.objects.count(), 0)
        self.assertEqual(AnexoCandidato.objects.count(), 0)

    def test_dry_run_devuelve_los_datos_extraidos(self):
        r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN))], dry_run="true")
        e = r.data["log"][0]
        self.assertEqual(e["fila"], 1)
        self.assertEqual(e["estado"], "OK")
        d = e["datos"]
        self.assertEqual((d["primer_nombre"], d["segundo_nombre"]), ("Juan", "Carlos"))
        self.assertEqual((d["primer_apellido"], d["segundo_apellido"]), ("Pérez", "Gómez"))
        self.assertEqual(d["email"], "juan.perez@example.com")
        self.assertEqual(d["tipo_documento"], "Cédula de Ciudadanía")
        self.assertEqual(d["numero_documento"], "1020304050")
        self.assertEqual(d["telefono"], "3124567890")

    @unittest.skipUnless(utils.hay_pillow() and __import__("importlib").util.find_spec("pdfplumber"),
                         "requiere pdfplumber")
    def test_pdf(self):
        r = self.post([subir("juan.pdf", utils.crear_pdf(utils.CV_JUAN))], dry_run="true")
        self.assertEqual(r.data["log"][0]["estado"], "OK", r.data["log"][0]["mensaje"])

    def test_sin_email_es_error(self):
        r = self.post([subir("pedro.txt", utils.crear_txt(utils.CV_SIN_EMAIL))], dry_run="true")
        e = r.data["log"][0]
        self.assertEqual(e["estado"], "ERROR")
        self.assertIn("correo electrónico (campo obligatorio)", e["mensaje"])
        self.assertEqual(e["datos"]["primer_nombre"], "Pedro")   # se muestra lo extraído

    def test_documento_sin_datos_utiles(self):
        r = self.post([subir("lorem.txt", utils.crear_txt(utils.CV_SIN_NADA))], dry_run="true")
        e = r.data["log"][0]
        self.assertEqual(e["estado"], "ERROR")
        self.assertIn("correo", e["mensaje"])
        self.assertIn("nombre", e["mensaje"])

    def test_tipo_de_documento_fuera_del_catalogo_es_advertencia(self):
        cv = utils.CV_JUAN.replace("Cédula de Ciudadanía", "Tarjeta de Identidad")
        r = self.post([subir("juan.txt", utils.crear_txt(cv))], dry_run="true")
        e = r.data["log"][0]
        self.assertEqual(e["estado"], "OK")
        self.assertTrue(any("catálogo" in a for a in e["advertencias"]))
        self.assertIsNone(e["datos"]["tipo_documento"])

    def test_duplicado_dentro_del_lote(self):
        r = self.post([subir("a.txt", utils.crear_txt(utils.CV_JUAN)),
                       subir("b.txt", utils.crear_txt(utils.CV_JUAN))], dry_run="true")
        self.assertEqual([e["estado"] for e in r.data["log"]], ["OK", "ERROR"])
        self.assertIn("Duplicado en este lote", r.data["log"][1]["mensaje"])
        self.assertIn("a.txt", r.data["log"][1]["mensaje"])

    def test_la_cache_evita_reprocesar_el_mismo_archivo(self):
        contenido = utils.crear_txt(utils.CV_JUAN)
        self.post([subir("a.txt", contenido)], dry_run="true")
        self.post([subir("a.txt", contenido)])          # el segundo paso (importar) reutiliza
        self.assertEqual(self.modelo.llamadas, 1)

    def test_el_duplicado_se_detecta_aunque_el_resultado_venga_de_cache(self):
        contenido = utils.crear_txt(utils.CV_JUAN)
        self.post([subir("a.txt", contenido)])                  # importa
        r = self.post([subir("a.txt", contenido)], dry_run="true")
        self.assertEqual(self.modelo.llamadas, 1)
        self.assertEqual(r.data["log"][0]["estado"], "ERROR")
        self.assertIn("Ya existe un candidato", r.data["log"][0]["mensaje"])


class ImportacionTests(CVApiBase):
    def setUp(self):
        super().setUp()
        self.modelo = self.usar_modelo()

    def test_importa_candidato_datos_y_guarda_el_cv(self):
        contenido = utils.crear_txt(utils.CV_JUAN)
        r = self.post([subir("juan.txt", contenido)], usuario_id="7")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["modo"], r.data["creados"], r.data["errores"]), ("importacion", 1, 0))
        self.assertTrue(r.data["log"][0]["mensaje"].startswith("Importado:"))

        cand = Candidato.objects.get(pk=r.data["log"][0]["candidato_id"])
        self.assertEqual((cand.compania_id, cand.id_interno, cand.usuario_creacion), (self.compania.pk, 1, 7))
        datos = DatosCandidato.objects.get(candidato=cand)
        self.assertEqual(datos.primer_nombre, "Juan")
        self.assertEqual(datos.segundo_apellido, "Gómez")
        self.assertEqual(datos.email, "juan.perez@example.com")
        self.assertEqual(datos.numero_documento, "1020304050")
        self.assertEqual(datos.tipo_documento.descripcion, "Cédula de Ciudadanía")
        self.assertEqual(datos.telefono, "3124567890")

        anexo = AnexoCandidato.objects.get(candidato=cand)
        self.assertEqual((anexo.nombre_archivo, anexo.tipo_archivo, anexo.tamanio_bytes),
                         ("juan.txt", "TXT", len(contenido)))
        guardado = Path(self._media.name) / anexo.ruta_almacenamiento
        self.assertEqual(guardado.read_bytes(), contenido)
        self.assertTrue(anexo.ruta_almacenamiento.startswith(f"candidatos/{self.compania.pk}/{cand.pk}/"))

    def test_importacion_masiva_multiples_formatos(self):
        archivos = [
            subir("juan.txt", utils.crear_txt(utils.CV_JUAN)),
            subir("maria.md", utils.crear_md(utils.CV_MARIA)),
            subir("ana.html", utils.crear_html(utils.CV_JUAN.replace("Juan Carlos Pérez Gómez", "Ana Lucía Torres Ruiz")
                                               .replace("juan.perez", "ana.torres")
                                               .replace("1020304050", "1111111111"))),
            subir("luis.docx", utils.crear_docx(utils.CV_MARIA.replace("María Fernanda Rodríguez Vargas", "Luis Miguel Soto Díaz")
                                                .replace("maria.rodriguez", "luis.soto")
                                                .replace("52987654", "2222222222"))),
        ]
        r = self.post(archivos)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["total"], r.data["creados"], r.data["errores"]), (4, 4, 0))
        self.assertEqual(Candidato.objects.count(), 4)
        self.assertEqual(sorted(Candidato.objects.values_list("id_interno", flat=True)), [1, 2, 3, 4])
        self.assertEqual(set(AnexoCandidato.objects.values_list("tipo_archivo", flat=True)),
                         {"TXT", "MD", "HTML", "DOCX"})

    def test_id_interno_continua_la_secuencia_existente(self):
        Candidato.objects.create(compania=self.compania, id_interno=5, usuario_creacion=1)
        r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN))])
        self.assertEqual(Candidato.objects.get(pk=r.data["log"][0]["candidato_id"]).id_interno, 6)

    def test_importacion_parcial_por_defecto(self):
        r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN)),
                       subir("pedro.txt", utils.crear_txt(utils.CV_SIN_EMAIL)),
                       subir("malo.exe", b"x")])
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.data["total"], r.data["creados"], r.data["errores"]), (3, 1, 2))
        self.assertEqual([e["estado"] for e in r.data["log"]], ["OK", "ERROR", "ERROR"])
        self.assertEqual(Candidato.objects.count(), 1)

    def test_omitir_errores_false_no_importa_nada(self):
        r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN)),
                       subir("pedro.txt", utils.crear_txt(utils.CV_SIN_EMAIL))], omitir_errores="false")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(Candidato.objects.count(), 0)
        self.assertEqual(r.data["errores"], 1)

    def test_si_todos_fallan_responde_422(self):
        r = self.post([subir("pedro.txt", utils.crear_txt(utils.CV_SIN_EMAIL))])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.data["error"], "No se importó ningún candidato.")

    def test_no_se_importa_un_candidato_existente_en_la_compania(self):
        self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN))])
        cv = (utils.CV_JUAN.replace("juan.perez@example.com", "JUAN.PEREZ@EXAMPLE.COM")   # mismo correo
              .replace("1020304050", "9999999999"))                                      # otro documento
        r = self.post([subir("otro.txt", utils.crear_txt(cv))])
        self.assertEqual(r.status_code, 422)
        self.assertIn("Ya existe un candidato con el correo", r.data["log"][0]["mensaje"])
        self.assertEqual(Candidato.objects.count(), 1)

    def test_duplicado_por_documento(self):
        self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN))])
        cv = utils.CV_JUAN.replace("juan.perez@example.com", "distinto@example.com")
        r = self.post([subir("otro.txt", utils.crear_txt(cv))])
        self.assertIn("Ya existe un candidato con el documento", r.data["log"][0]["mensaje"])

    def test_el_mismo_correo_en_otra_compania_si_se_permite(self):
        from apps.empresa.models import Compania
        otra = Compania.objects.create(descripcion="Otra", nit="800", usuario_creacion=1)
        self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN))])
        r = self.client.post(self.url(otra.pk), {"archivos": [subir("juan.txt", utils.crear_txt(utils.CV_JUAN))]},
                             format="multipart")
        self.assertEqual(r.status_code, 201, r.data)

    def test_fallo_al_guardar_hace_rollback_y_no_deja_archivos_huerfanos(self):
        with mock.patch("apps.candidatos.ia.servicio.AnexoCandidato.objects.create",
                        side_effect=RuntimeError("fallo simulado")), \
                self.assertLogs("apps.candidatos.views_importacion_cv", level="ERROR"):
            r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN)),
                           subir("maria.txt", utils.crear_txt(utils.CV_MARIA))])
        self.assertEqual(r.status_code, 422)
        self.assertEqual(Candidato.objects.count(), 0)
        self.assertEqual(DatosCandidato.objects.count(), 0)
        self.assertEqual([p for p in Path(self._media.name).rglob("*") if p.is_file()], [])
        self.assertEqual(r.data["log"][0]["mensaje"], "No se pudo guardar el candidato en la base de datos.")

    def test_un_fallo_al_guardar_no_cancela_a_los_demas(self):
        original = AnexoCandidato.objects.create
        llamadas = {"n": 0}

        def falla_la_primera(**kw):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise RuntimeError("fallo simulado")
            return original(**kw)

        with mock.patch("apps.candidatos.ia.servicio.AnexoCandidato.objects.create", side_effect=falla_la_primera), \
                self.assertLogs("apps.candidatos.views_importacion_cv", level="ERROR"):
            r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN)),
                           subir("maria.txt", utils.crear_txt(utils.CV_MARIA))])
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.data["creados"], r.data["errores"]), (1, 1))
        self.assertEqual(Candidato.objects.count(), 1)

    def test_usuario_id_uuid_se_conserva_como_texto(self):
        from apps.candidatos.views_importacion import normalizar_usuario_id
        self.assertEqual(normalizar_usuario_id("12"), 12)
        self.assertEqual(normalizar_usuario_id(" 12 "), 12)
        self.assertEqual(normalizar_usuario_id("0e93c505-699e-453f-b199-8056623e3ef5"),
                         "0e93c505-699e-453f-b199-8056623e3ef5")
        self.assertEqual(normalizar_usuario_id(None), 1)
        self.assertEqual(normalizar_usuario_id(""), 1)

    def test_booleanos_del_formulario(self):
        r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN))], dry_run="TRUE")
        self.assertEqual(r.data["modo"], "validacion")
        r = self.post([subir("juan.txt", utils.crear_txt(utils.CV_JUAN))], dry_run="false")
        self.assertEqual(r.data["modo"], "importacion")


@unittest.skipUnless(utils.hay_modelo_real(), "PyTorch / pytorch-crf / checkpoint no disponibles")
class ImportacionConModeloRealTests(CVApiBase):
    """Cadena completa con el modelo entrenado (sin simulaciones)."""

    def test_carga_masiva_con_modelo_real(self):
        from apps.candidatos.ia import extractor
        extractor.reiniciar_extractor()
        self.addCleanup(extractor.reiniciar_extractor)
        otro = utils.CV_MARIA.replace("María Fernanda Rodríguez Vargas", "Laura Sofía Mendoza Rojas")\
                             .replace("maria.rodriguez", "laura.mendoza").replace("52987654", "41234567")
        archivos = [
            subir("juan.txt", utils.crear_txt(utils.CV_JUAN)),
            subir("maria.md", utils.crear_md(utils.CV_MARIA)),
            subir("juan2.docx", utils.crear_docx(utils.CV_JUAN.replace("juan.perez", "j.perez2")
                                                 .replace("1020304050", "1099887766"))),
            subir("laura.html", utils.crear_html(otro)),
            subir("sin_correo.txt", utils.crear_txt(utils.CV_SIN_EMAIL)),
        ]
        r = self.post(archivos, dry_run="true")
        self.assertEqual(r.status_code, 200)
        estados = {e["archivo"]: e["estado"] for e in r.data["log"]}
        self.assertEqual(estados, {"juan.txt": "OK", "maria.md": "OK", "juan2.docx": "OK",
                                   "laura.html": "OK", "sin_correo.txt": "ERROR"})
        r = self.post(archivos)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["creados"], r.data["errores"]), (4, 1))
        emails = set(DatosCandidato.objects.values_list("email", flat=True))
        self.assertEqual(emails, {"juan.perez@example.com", "maria.rodriguez@example.org",
                                  "j.perez2@example.com", "laura.mendoza@example.org"})
        maria = DatosCandidato.objects.get(email="maria.rodriguez@example.org")
        self.assertEqual((maria.primer_nombre, maria.primer_apellido), ("María", "Rodríguez"))
        self.assertEqual(maria.tipo_documento.descripcion, "Cédula de Ciudadanía")
        self.assertEqual(maria.numero_documento, "52987654")
