"""
Salida del modelo  ->  campos de `DatosCandidato`.

Reglas de negocio de la importación de candidatos (Excel y hojas de vida):
  * Obligatorios: primer_nombre, primer_apellido y **email** (formato válido).
  * Opcionales : segundo_nombre, segundo_apellido, tipo_documento,
                 numero_documento, telefono.
  * Longitudes máximas = las de las columnas de la BD.
"""
import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

CAMPOS_REQUERIDOS = ("primer_nombre", "primer_apellido", "email")

LONGITUD_MAX = {
    "primer_nombre": 80, "segundo_nombre": 80,
    "primer_apellido": 80, "segundo_apellido": 80,
    "numero_documento": 30, "email": 150, "telefono": 20,
}

ETIQUETAS = {
    "primer_nombre": "primer nombre", "primer_apellido": "primer apellido", "email": "correo electrónico",
}


# ───────────────────────── utilidades de texto ─────────────────────────
def sin_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def limpiar_espacios(valor) -> str | None:
    if valor is None:
        return None
    texto = re.sub(r"\s+", " ", str(valor)).strip(" \t\r\n.,;:|-")
    return texto or None


def limpiar_nombre(valor) -> str | None:
    """Nombre/apellido: sin espacios sobrantes y sin dígitos ni arrobas (no son nombres)."""
    texto = limpiar_espacios(valor)
    if texto and re.search(r"[\d@]", texto):
        return None
    return texto


def validar_email(valor) -> str | None:
    """Devuelve el correo normalizado (minúsculas, sin espacios) o None si no es válido."""
    if valor is None:
        return None
    texto = str(valor).strip().strip("<>;,. ").lower()
    if not texto or len(texto) > LONGITUD_MAX["email"]:
        return None
    try:
        validate_email(texto)
    except ValidationError:
        return None
    return texto


# ───────────────────────── tipo de documento ─────────────────────────
# clave canónica -> alias reconocidos (en mayúsculas y sin acentos)
_ALIAS_TIPO = {
    "CC": {"CC", "C C", "CEDULA", "CEDULA DE CIUDADANIA", "CEDULA CIUDADANIA"},
    "CE": {"CE", "C E", "CEDULA DE EXTRANJERIA", "CEDULA EXTRANJERIA"},
    "PASAPORTE": {"PA", "PAS", "PASAPORTE"},
    "NIT": {"NIT"},
    "PPT": {"PPT", "PERMISO POR PROTECCION TEMPORAL", "PERMISO PROTECCION TEMPORAL"},
    "TI": {"TI", "T I", "TARJETA DE IDENTIDAD", "TARJETA IDENTIDAD"},
    "RC": {"RC", "R C", "REGISTRO CIVIL"},
}
_CLAVE_POR_ALIAS = {a: k for k, alias in _ALIAS_TIPO.items() for a in alias}


def _norm_tipo(valor) -> str:
    texto = sin_acentos(str(valor or "")).upper()
    texto = re.sub(r"[^A-Z0-9 ]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def clave_tipo_documento(valor) -> str | None:
    """'C.C.', 'Cédula de Ciudadanía', 'pasaporte'... -> 'CC' | 'CE' | 'PASAPORTE' | ..."""
    norm = _norm_tipo(valor)
    return _CLAVE_POR_ALIAS.get(norm)


def resolver_tipo_documento(valor, catalogo):
    """
    Busca el TipoDocumento del catálogo que corresponde al valor extraído.
    `catalogo`: iterable de objetos con `.descripcion`.
    Devuelve (objeto | None, aviso | None).
    """
    if not valor:
        return None, None
    clave = clave_tipo_documento(valor)
    for td in catalogo:
        norm_desc = _norm_tipo(td.descripcion)
        if norm_desc == _norm_tipo(valor) or (clave and _CLAVE_POR_ALIAS.get(norm_desc) == clave):
            return td, None
    return None, f"El tipo de documento '{valor}' no está en el catálogo; se importa sin tipo."


# ───────────────────────── construcción de datos ─────────────────────────
def construir_datos(salida: dict, catalogo_tipos=()):
    """
    Convierte la salida cruda del modelo en los campos de DatosCandidato.

    Devuelve (datos, tipo_obj, errores, avisos):
      datos     : dict con los 8 campos (str | None) listo para mostrar y guardar
      tipo_obj  : instancia de TipoDocumento o None
      errores   : lista de mensajes que impiden importar
      avisos    : lista de observaciones que no impiden importar
    """
    errores, avisos = [], []
    datos = {
        "primer_nombre": limpiar_nombre(salida.get("primer_nombre")),
        "segundo_nombre": limpiar_nombre(salida.get("segundo_nombre")),
        "primer_apellido": limpiar_nombre(salida.get("primer_apellido")),
        "segundo_apellido": limpiar_nombre(salida.get("segundo_apellido")),
        "tipo_documento": None,
        "numero_documento": limpiar_espacios(salida.get("num_doc")),
        "email": None,
        "telefono": limpiar_espacios(salida.get("telefono")),
    }

    # — email (obligatorio) —
    email_crudo = limpiar_espacios(salida.get("email"))
    if not email_crudo:
        errores.append("No se encontró un correo electrónico (campo obligatorio).")
    else:
        email = validar_email(email_crudo)
        if email is None:
            errores.append(f"El correo '{email_crudo}' no tiene un formato válido.")
        else:
            datos["email"] = email

    # — nombre y apellido (obligatorios) —
    if not datos["primer_nombre"] and not datos["primer_apellido"]:
        errores.append("No se pudo identificar el nombre y el apellido de la persona.")
    else:
        if not datos["primer_nombre"]:
            errores.append("No se pudo identificar el primer nombre.")
        if not datos["primer_apellido"]:
            errores.append("No se pudo identificar el primer apellido.")

    # — longitudes —
    for campo, maximo in LONGITUD_MAX.items():
        valor = datos.get(campo)
        if valor and len(valor) > maximo:
            if campo in CAMPOS_REQUERIDOS:
                errores.append(f"El campo {ETIQUETAS.get(campo, campo)} supera {maximo} caracteres.")
            else:
                avisos.append(f"{campo} supera {maximo} caracteres y se omitió.")
                datos[campo] = None

    # — tipo de documento contra el catálogo —
    tipo_obj = None
    tipo_extraido = limpiar_espacios(salida.get("tipo_doc"))
    if tipo_extraido:
        tipo_obj, aviso = resolver_tipo_documento(tipo_extraido, list(catalogo_tipos))
        if aviso:
            avisos.append(aviso)
        if tipo_obj is not None:
            datos["tipo_documento"] = tipo_obj.descripcion
    if datos["numero_documento"] and not tipo_obj:
        avisos.append("Hay número de documento pero no se identificó el tipo de documento.")
    if tipo_obj and not datos["numero_documento"]:
        avisos.append("Se identificó el tipo de documento pero no el número.")

    # — observaciones sobre la extracción —
    personas = salida.get("_personas_detectadas")
    if isinstance(personas, int) and personas > 1:
        avisos.append(f"Se detectaron {personas} personas en el documento; se usó la de mayor "
                      "puntaje. Revise el resultado.")
    if salida.get("_fuente_nombre") == "encabezado":
        avisos.append("El nombre se tomó del título del documento (respaldo); revíselo.")

    return datos, tipo_obj, errores, avisos
