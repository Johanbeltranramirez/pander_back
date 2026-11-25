"""
Servicio de importación de candidatos desde hojas de vida.

Flujo por archivo:
    validar (extensión, tamaño, contenido) -> leer + extraer (con caché por hash)
    -> mapear a DatosCandidato -> verificar duplicados -> (opcional) importar.

El análisis (`analizar_lote`) no escribe en la BD; `importar_candidato` guarda el
Candidato, sus DatosCandidato y el archivo original como AnexoCandidato.
"""
import hashlib
import io
import logging
import os
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max

from apps.candidatos.models import AnexoCandidato, Candidato, DatosCandidato, TipoDocumento

from .extractor import ModeloNoDisponible, get_extractor
from .lectores import EXTENSIONES_PERMITIDAS, LectorError, LectorNoDisponible
from .mapeo import construir_datos

logger = logging.getLogger(__name__)

FORMATOS = sorted(e.lstrip(".") for e in EXTENSIONES_PERMITIDAS)

_FIRMAS = {
    ".pdf": (b"%PDF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
}


def limites() -> dict:
    return {
        "max_archivos": getattr(settings, "CV_MAX_ARCHIVOS", 50),
        "max_bytes": getattr(settings, "CV_MAX_BYTES", 10 * 1024 * 1024),
    }


@dataclass
class Resultado:
    """Resultado del análisis de un archivo (el dict `entrada` es lo que ve el cliente)."""
    entrada: dict
    archivo: object = None
    ext: str = ""
    datos: dict | None = None
    tipo_obj: object = None
    sha: str = ""

    @property
    def ok(self) -> bool:
        return self.entrada["estado"] == "OK"


# ───────────────────────── validación del archivo ─────────────────────────
def nombre_seguro(archivo) -> str:
    """Solo el nombre base (sin rutas): evita path traversal al mostrar/guardar."""
    nombre = os.path.basename(str(getattr(archivo, "name", "") or "")).replace("\x00", "")
    return nombre[:255] or "archivo"


def validar_archivo(archivo) -> tuple[str | None, bytes | None, str | None]:
    """Devuelve (extensión, contenido, error)."""
    nombre = nombre_seguro(archivo)
    ext = Path(nombre).suffix.lower()
    if ext not in EXTENSIONES_PERMITIDAS:
        permitidos = ", ".join(FORMATOS)
        return None, None, f"Formato '{ext or 'sin extensión'}' no permitido. Use: {permitidos}."
    max_bytes = limites()["max_bytes"]
    if archivo.size == 0:
        return ext, None, "El archivo está vacío."
    if archivo.size > max_bytes:
        return ext, None, f"El archivo supera el tamaño máximo de {max_bytes // (1024 * 1024)} MB."
    archivo.seek(0)
    contenido = archivo.read()
    archivo.seek(0)

    if ext in _FIRMAS:
        if not any(contenido.startswith(f) for f in _FIRMAS[ext]):
            return ext, None, f"El contenido no corresponde a un archivo {ext.lstrip('.').upper()}."
    elif ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as z:
                if "word/document.xml" not in z.namelist():
                    raise zipfile.BadZipFile
        except zipfile.BadZipFile:
            return ext, None, "El contenido no corresponde a un archivo DOCX."
    else:  # html, htm, md, txt: deben ser texto
        if b"\x00" in contenido[:4096]:
            return ext, None, "El archivo no es de texto."
    return ext, contenido, None


# ───────────────────────── extracción (con caché) ─────────────────────────
def _extraer(contenido: bytes, ext: str, extractor) -> tuple[dict, str]:
    sha = hashlib.sha256(contenido).hexdigest()
    clave = f"cvia:{extractor.firma()}:{sha}"
    salida = cache.get(clave)
    if salida is not None:
        return salida, sha
    with tempfile.TemporaryDirectory(prefix="cvia_") as tmp:
        ruta = Path(tmp) / f"{uuid.uuid4().hex}{ext}"   # nombre propio: nunca el del usuario
        ruta.write_bytes(contenido)
        salida = extractor.extraer_archivo(ruta)
    cache.set(clave, salida, getattr(settings, "CV_CACHE_SEGUNDOS", 3600))
    return salida, sha


def _claves_duplicado(datos: dict, tipo_obj) -> list[tuple]:
    claves = []
    if datos.get("email"):
        claves.append(("email", datos["email"].lower()))
    if tipo_obj is not None and datos.get("numero_documento"):
        claves.append(("doc", tipo_obj.pk, datos["numero_documento"]))
    return claves


def _duplicado_en_bd(compania_id, datos: dict, tipo_obj) -> str | None:
    base = DatosCandidato.objects.filter(compania_id=compania_id)
    if datos.get("email") and base.filter(email__iexact=datos["email"]).exists():
        return f"Ya existe un candidato con el correo {datos['email']}."
    if tipo_obj is not None and datos.get("numero_documento") and base.filter(
            tipo_documento=tipo_obj, numero_documento=datos["numero_documento"]).exists():
        return f"Ya existe un candidato con el documento {datos['numero_documento']}."
    return None


# ───────────────────────── análisis por lote ─────────────────────────
def analizar_lote(archivos, compania_id, extractor=None) -> list[Resultado]:
    """
    Analiza cada archivo sin escribir en la BD. Un fallo en un archivo no afecta a los demás.
    Lanza ModeloNoDisponible si el modelo no puede usarse.
    """
    extractor = extractor or get_extractor()
    motivo = extractor.motivo_no_disponible()
    if motivo:
        raise ModeloNoDisponible(motivo)

    catalogo = list(TipoDocumento.objects.all())
    vistos: dict[tuple, str] = {}
    resultados: list[Resultado] = []

    for i, archivo in enumerate(archivos, start=1):
        nombre = nombre_seguro(archivo)
        entrada = {"fila": i, "archivo": nombre, "estado": "ERROR", "mensaje": "",
                   "datos": None, "advertencias": []}
        res = Resultado(entrada=entrada, archivo=archivo)
        resultados.append(res)

        ext, contenido, error = validar_archivo(archivo)
        if error:
            entrada["mensaje"] = error
            continue
        res.ext = ext

        try:
            salida, res.sha = _extraer(contenido, ext, extractor)
        except LectorNoDisponible as e:
            entrada["mensaje"] = str(e)
            continue
        except LectorError as e:
            entrada["mensaje"] = str(e)
            continue
        except ModeloNoDisponible:
            raise
        except Exception:
            logger.exception("Fallo inesperado procesando %s", nombre)
            entrada["mensaje"] = "No se pudo procesar el archivo (error interno al extraer los datos)."
            continue

        datos, tipo_obj, errores, avisos = construir_datos(salida, catalogo)
        entrada["datos"] = datos
        entrada["advertencias"] = avisos
        res.datos, res.tipo_obj = datos, tipo_obj

        if not errores:
            dup = _duplicado_en_bd(compania_id, datos, tipo_obj)
            if dup:
                errores.append(dup)
            else:
                for clave in _claves_duplicado(datos, tipo_obj):
                    if clave in vistos:
                        errores.append(f"Duplicado en este lote: mismo "
                                       f"{'correo' if clave[0] == 'email' else 'documento'} "
                                       f"que '{vistos[clave]}'.")
                        break
        if errores:
            entrada["mensaje"] = " ".join(errores)
            continue

        for clave in _claves_duplicado(datos, tipo_obj):
            vistos[clave] = nombre
        nombre_completo = " ".join(p for p in (datos["primer_nombre"], datos["primer_apellido"]) if p)
        entrada["estado"] = "OK"
        entrada["mensaje"] = f"{nombre_completo} <{datos['email']}>"
    return resultados


# ───────────────────────── importación ─────────────────────────
def _guardar_archivo(compania_id, candidato_id, contenido: bytes, ext: str) -> str:
    relativa = Path("candidatos") / str(compania_id) / str(candidato_id) / f"{uuid.uuid4().hex}{ext}"
    absoluta = Path(settings.MEDIA_ROOT) / relativa
    absoluta.parent.mkdir(parents=True, exist_ok=True)
    absoluta.write_bytes(contenido)
    return relativa.as_posix()


def importar_candidato(compania, resultado: Resultado, usuario_id) -> int:
    """
    Crea Candidato + DatosCandidato + AnexoCandidato (el CV original) en una transacción.
    Devuelve el id del candidato. Reintenta si otro proceso tomó el mismo id_interno.
    """
    datos, archivo = resultado.datos, resultado.archivo
    archivo.seek(0)
    contenido = archivo.read()
    ultimo_error = None
    for _ in range(5):
        ruta_guardada = None
        try:
            with transaction.atomic():
                siguiente = (Candidato.objects.filter(compania=compania)
                             .aggregate(m=Max("id_interno"))["m"] or 0) + 1
                cand = Candidato.objects.create(
                    compania=compania, id_interno=siguiente, usuario_creacion=usuario_id)
                DatosCandidato.objects.create(
                    compania=compania, candidato=cand, tipo_documento=resultado.tipo_obj,
                    numero_documento=datos["numero_documento"],
                    primer_nombre=datos["primer_nombre"], segundo_nombre=datos["segundo_nombre"],
                    primer_apellido=datos["primer_apellido"], segundo_apellido=datos["segundo_apellido"],
                    email=datos["email"], telefono=datos["telefono"], usuario_creacion=usuario_id)
                ruta_guardada = _guardar_archivo(compania.pk, cand.pk, contenido, resultado.ext)
                AnexoCandidato.objects.create(
                    compania=compania, candidato=cand, id_interno=1,
                    nombre_archivo=nombre_seguro(archivo), tipo_archivo=resultado.ext.lstrip(".").upper(),
                    tamanio_bytes=len(contenido), ruta_almacenamiento=ruta_guardada,
                    usuario_creacion=usuario_id)
                return cand.pk
        except IntegrityError as e:       # carrera en (compania, id_interno): se reintenta
            ultimo_error = e
            _borrar_archivo(ruta_guardada)
            continue
        except Exception:
            _borrar_archivo(ruta_guardada)
            raise
    raise ultimo_error


def _borrar_archivo(relativa: str | None) -> None:
    if not relativa:
        return
    try:
        (Path(settings.MEDIA_ROOT) / relativa).unlink(missing_ok=True)
    except OSError:
        logger.warning("No se pudo borrar el archivo huérfano %s", relativa)
