"""
Carga única (perezosa) del modelo BiLSTM-CRF y extracción de datos.

* `ExtractorCV.disponible()` es barato: solo comprueba que exista el checkpoint y
  que PyTorch / pytorch-crf se puedan importar. No carga el modelo.
* El modelo se carga en la primera extracción y se reutiliza en todo el proceso.
* La inferencia se serializa con un lock: el OCR y el modelo no son seguros para
  uso concurrente dentro del mismo proceso.
"""
import importlib.util
import threading
from pathlib import Path

from django.conf import settings

from . import ner_documentos as ner
from .lectores import leer_texto


class ModeloNoDisponible(Exception):
    """El modelo no puede usarse (falta el checkpoint o una dependencia)."""


def _ruta_por_defecto() -> Path:
    return Path(getattr(settings, "CV_MODEL_PATH",
                        Path(__file__).resolve().parent / "modelo" / "modelo.pt"))


class ExtractorCV:
    def __init__(self, ruta_modelo=None, dispositivo: str | None = None):
        self.ruta = Path(ruta_modelo) if ruta_modelo else _ruta_por_defecto()
        self._dispositivo_pref = dispositivo or getattr(settings, "CV_DEVICE", None)
        self._lock = threading.RLock()
        self._modelo = None      # (model, w2i, c2i)
        self._device = None

    # ── estado ──
    def motivo_no_disponible(self) -> str | None:
        if not self.ruta.is_file():
            return f"No se encontró el modelo entrenado en '{self.ruta}'."
        for paquete, pip in (("torch", "torch"), ("torchcrf", "pytorch-crf")):
            if importlib.util.find_spec(paquete) is None:
                return f"Falta la dependencia '{pip}' (pip install {pip})."
        return None

    def disponible(self) -> bool:
        return self.motivo_no_disponible() is None

    def estado(self) -> dict:
        motivo = self.motivo_no_disponible()
        return {"disponible": motivo is None, "motivo": motivo, "version_modelo": ner.VERSION}

    def firma(self) -> str:
        """Identifica modelo+código: sirve de llave para la caché de resultados."""
        try:
            st = self.ruta.stat()
            return f"{ner.VERSION}-{st.st_size}-{int(st.st_mtime)}"
        except OSError:
            return ner.VERSION

    # ── carga / inferencia ──
    def _cargar(self):
        if self._modelo is not None:
            return
        with self._lock:
            if self._modelo is not None:
                return
            motivo = self.motivo_no_disponible()
            if motivo:
                raise ModeloNoDisponible(motivo)
            import torch
            self._device = torch.device(
                self._dispositivo_pref or ("cuda" if torch.cuda.is_available() else "cpu"))
            # El checkpoint es un artefacto propio (confiable); contiene la configuración
            # y los vocabularios, por lo que se carga con weights_only=False como en el script.
            self._modelo = ner.load_model(str(self.ruta), self._device)

    def extraer_texto(self, texto: str) -> dict:
        self._cargar()
        model, w2i, c2i = self._modelo
        with self._lock:
            return ner.extract(texto, model, w2i, c2i, self._device)

    def extraer_archivo(self, path) -> dict:
        """Lee el archivo (cualquier formato permitido) y devuelve los campos extraídos."""
        with self._lock:  # el OCR tampoco es concurrente
            texto = leer_texto(path, max_paginas=getattr(settings, "CV_MAX_PAGINAS", 10))
            return self.extraer_texto(texto)


_instancia: ExtractorCV | None = None
_instancia_lock = threading.Lock()


def get_extractor() -> ExtractorCV:
    """Instancia única del proceso."""
    global _instancia
    if _instancia is None:
        with _instancia_lock:
            if _instancia is None:
                _instancia = ExtractorCV()
    return _instancia


def reiniciar_extractor() -> None:
    """Descarta la instancia (útil en pruebas o tras cambiar CV_MODEL_PATH)."""
    global _instancia
    with _instancia_lock:
        _instancia = None
