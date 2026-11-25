"""
Importación masiva de candidatos desde hojas de vida (PDF, DOCX, PNG, JPG, HTML, MD, TXT)
usando el modelo BiLSTM-CRF.

  GET  /api/importacion/cv/estado/
       Disponibilidad del modelo, formatos y límites.

  POST /api/importacion/companias/<compania>/candidatos/cv/        (multipart/form-data)
       archivos          : uno o varios archivos (campo repetido)
       dry_run           : "true" -> solo analiza y devuelve lo extraído, no guarda nada
       omitir_errores    : "true" (por defecto) -> importa los válidos y reporta los demás;
                           "false" -> si hay algún error no se importa nada (como el Excel)
       usuario_id        : id del usuario que importa (auditoría)

Respuesta: mismo contrato que las importaciones por Excel
  { modo, total, ok, errores, [creados], log: [ {fila, archivo, estado, mensaje, datos,
                                                  advertencias, [candidato_id]} ] }
"""
import logging

from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .ia import servicio
from .ia.extractor import ModeloNoDisponible
from .views_importacion import _comp, _existe_compania, normalizar_usuario_id

logger = logging.getLogger(__name__)

MB = 1024 * 1024


def _como_bool(valor, defecto: bool) -> bool:
    if valor is None or str(valor).strip() == "":
        return defecto
    return str(valor).strip().lower() in ("1", "true", "t", "si", "sí", "yes", "y")


class EstadoImportacionCVView(APIView):
    """¿Está disponible la importación con IA? Qué formatos y límites aplica."""

    def get(self, request):
        lim = servicio.limites()
        estado = servicio.get_extractor().estado()
        return Response({
            **estado,
            "formatos": servicio.FORMATOS,
            "max_archivos": lim["max_archivos"],
            "max_mb": lim["max_bytes"] // MB,
        })


class ImportarCandidatosCVView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, compania):
        if not _existe_compania(compania):
            return Response({"error": "La compañía indicada no existe."}, status=404)

        archivos = request.FILES.getlist("archivos") or request.FILES.getlist("archivo")
        if not archivos:
            return Response(
                {"error": "Se requiere al menos un archivo (campo 'archivos')."}, status=400)
        lim = servicio.limites()
        if len(archivos) > lim["max_archivos"]:
            return Response({
                "error": f"Máximo {lim['max_archivos']} archivos por solicitud "
                         f"(se recibieron {len(archivos)}).",
            }, status=400)

        dry_run = _como_bool(request.data.get("dry_run"), False)
        omitir_errores = _como_bool(request.data.get("omitir_errores"), True)
        uid = normalizar_usuario_id(request.data.get("usuario_id", 1))

        try:
            resultados = servicio.analizar_lote(archivos, compania)
        except ModeloNoDisponible as e:
            return Response({
                "error": f"El modelo de IA no está disponible: {e}",
                "disponible": False,
            }, status=503)

        total = len(resultados)
        validos = [r for r in resultados if r.ok]
        n_errores = total - len(validos)

        if dry_run:
            return Response({
                "modo": "validacion", "total": total, "ok": len(validos),
                "errores": n_errores, "log": [r.entrada for r in resultados],
            })

        if n_errores and not omitir_errores:
            return Response({
                "error": "Hay archivos con errores. Corrígelos antes de importar "
                         "(o importe solo los válidos).",
                "errores": n_errores, "log": [r.entrada for r in resultados],
            }, status=422)

        comp = _comp(compania)
        creados = 0
        for r in validos:
            try:
                r.entrada["candidato_id"] = servicio.importar_candidato(comp, r, uid)
                r.entrada["mensaje"] = f"Importado: {r.entrada['mensaje']}"
                creados += 1
            except Exception:
                logger.exception("No se pudo guardar el candidato de %s", r.entrada["archivo"])
                r.entrada["estado"] = "ERROR"
                r.entrada["mensaje"] = "No se pudo guardar el candidato en la base de datos."

        n_errores = total - creados
        cuerpo = {"modo": "importacion", "total": total, "creados": creados,
                  "errores": n_errores, "log": [r.entrada for r in resultados]}
        if creados == 0:
            cuerpo["error"] = "No se importó ningún candidato."
            return Response(cuerpo, status=422)
        return Response(cuerpo, status=201)
