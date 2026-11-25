"""Pruebas unitarias del mapeo salida-del-modelo -> campos de DatosCandidato."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.candidatos.ia import mapeo

CATALOGO = [SimpleNamespace(pk=i, descripcion=d) for i, d in enumerate(
    ["Cédula de Ciudadanía", "Cédula de Extranjería", "Pasaporte", "NIT",
     "Permiso por Protección Temporal"], start=1)]


def salida(**kw):
    base = {"primer_nombre": "Juan", "segundo_nombre": "Carlos", "primer_apellido": "Pérez",
            "segundo_apellido": "Gómez", "tipo_doc": "Cédula de Ciudadanía", "num_doc": "1020304050",
            "email": "juan@example.com", "telefono": "3124567890",
            "_personas_detectadas": 1, "_fuente_nombre": "modelo"}
    base.update(kw)
    return base


class EmailTests(SimpleTestCase):
    def test_validos_se_normalizan(self):
        self.assertEqual(mapeo.validar_email("  Juan.Perez@Example.COM "), "juan.perez@example.com")
        self.assertEqual(mapeo.validar_email("<a@b.co>;"), "a@b.co")

    def test_invalidos(self):
        for malo in (None, "", "   ", "sin-arroba", "a@", "@b.com", "a b@c.com", "a@b", "x" * 200 + "@b.com"):
            with self.subTest(malo=malo):
                self.assertIsNone(mapeo.validar_email(malo))


class TipoDocumentoTests(SimpleTestCase):
    def test_claves(self):
        casos = {"CC": "CC", "C.C.": "CC", "cédula": "CC", "Cedula de Ciudadania": "CC",
                 "CE": "CE", "Cédula de Extranjería": "CE", "pasaporte": "PASAPORTE", "PAS": "PASAPORTE",
                 "NIT": "NIT", "PPT": "PPT", "Tarjeta de Identidad": "TI", "xyz": None, "": None}
        for crudo, esperado in casos.items():
            with self.subTest(crudo=crudo):
                self.assertEqual(mapeo.clave_tipo_documento(crudo), esperado)

    def test_resuelve_contra_el_catalogo(self):
        for crudo, descripcion in (("CC", "Cédula de Ciudadanía"), ("c.e.", "Cédula de Extranjería"),
                                   ("Pasaporte", "Pasaporte"), ("PPT", "Permiso por Protección Temporal")):
            with self.subTest(crudo=crudo):
                obj, aviso = mapeo.resolver_tipo_documento(crudo, CATALOGO)
                self.assertEqual(obj.descripcion, descripcion)
                self.assertIsNone(aviso)

    def test_tipo_fuera_del_catalogo_genera_aviso(self):
        obj, aviso = mapeo.resolver_tipo_documento("Tarjeta de Identidad", CATALOGO)
        self.assertIsNone(obj)
        self.assertIn("no está en el catálogo", aviso)

    def test_vacio(self):
        self.assertEqual(mapeo.resolver_tipo_documento(None, CATALOGO), (None, None))


class ConstruirDatosTests(SimpleTestCase):
    def test_caso_completo(self):
        datos, tipo, errores, avisos = mapeo.construir_datos(salida(), CATALOGO)
        self.assertEqual(errores, [])
        self.assertEqual(avisos, [])
        self.assertEqual(datos["primer_nombre"], "Juan")
        self.assertEqual(datos["numero_documento"], "1020304050")
        self.assertEqual(datos["tipo_documento"], "Cédula de Ciudadanía")
        self.assertEqual(tipo.pk, 1)

    def test_email_es_obligatorio(self):
        _, _, errores, _ = mapeo.construir_datos(salida(email=None), CATALOGO)
        self.assertEqual(len(errores), 1)
        self.assertIn("correo electrónico (campo obligatorio)", errores[0])

    def test_email_con_formato_invalido(self):
        _, _, errores, _ = mapeo.construir_datos(salida(email="juan@@x"), CATALOGO)
        self.assertTrue(any("formato válido" in e for e in errores))

    def test_email_se_normaliza_a_minusculas(self):
        datos, *_ = mapeo.construir_datos(salida(email="JUAN@Example.com"), CATALOGO)
        self.assertEqual(datos["email"], "juan@example.com")

    def test_sin_nombre_ni_apellido(self):
        _, _, errores, _ = mapeo.construir_datos(
            salida(primer_nombre=None, primer_apellido=None), CATALOGO)
        self.assertTrue(any("nombre y el apellido" in e for e in errores))

    def test_falta_solo_apellido(self):
        _, _, errores, _ = mapeo.construir_datos(salida(primer_apellido=None), CATALOGO)
        self.assertEqual(errores, ["No se pudo identificar el primer apellido."])

    def test_nombres_con_digitos_o_arroba_se_descartan(self):
        datos, _, errores, _ = mapeo.construir_datos(salida(primer_nombre="Juan2"), CATALOGO)
        self.assertIsNone(datos["primer_nombre"])
        self.assertTrue(errores)

    def test_campos_opcionales_ausentes_no_son_error(self):
        datos, tipo, errores, _ = mapeo.construir_datos(
            salida(segundo_nombre=None, segundo_apellido=None, tipo_doc=None, num_doc=None,
                   telefono=None), CATALOGO)
        self.assertEqual(errores, [])
        self.assertIsNone(tipo)
        self.assertIsNone(datos["telefono"])

    def test_tipo_fuera_de_catalogo_es_aviso_no_error(self):
        datos, tipo, errores, avisos = mapeo.construir_datos(
            salida(tipo_doc="Tarjeta de Identidad"), CATALOGO)
        self.assertEqual(errores, [])
        self.assertIsNone(tipo)
        self.assertIsNone(datos["tipo_documento"])
        self.assertTrue(any("catálogo" in a for a in avisos))

    def test_numero_sin_tipo_es_aviso(self):
        _, _, errores, avisos = mapeo.construir_datos(salida(tipo_doc=None), CATALOGO)
        self.assertEqual(errores, [])
        self.assertTrue(any("tipo de documento" in a for a in avisos))

    def test_telefono_demasiado_largo_se_omite(self):
        datos, _, errores, avisos = mapeo.construir_datos(salida(telefono="3" * 40), CATALOGO)
        self.assertEqual(errores, [])
        self.assertIsNone(datos["telefono"])
        self.assertTrue(any("telefono" in a for a in avisos))

    def test_nombre_demasiado_largo_es_error(self):
        _, _, errores, _ = mapeo.construir_datos(salida(primer_nombre="A" * 81), CATALOGO)
        self.assertTrue(any("primer nombre" in e for e in errores))

    def test_avisos_de_calidad_de_la_extraccion(self):
        _, _, _, avisos = mapeo.construir_datos(
            salida(_personas_detectadas=3, _fuente_nombre="encabezado"), CATALOGO)
        self.assertEqual(len(avisos), 2)

    def test_salida_vacia_del_modelo(self):
        _, _, errores, _ = mapeo.construir_datos({}, CATALOGO)
        self.assertEqual(len(errores), 2)  # correo + nombre/apellido
