"""
Importación de hojas de vida con IA (BiLSTM-CRF, PyTorch).

Módulos:
  ner_documentos : script original del modelo (NO modificar: el checkpoint
                   depende de su tokenización y de la clase BiLSTMCRF).
  modelo/modelo.pt : checkpoint entrenado.
  lectores       : texto plano de PDF, DOCX, PNG, JPG, HTML, MD y TXT.
  extractor      : carga única del modelo + inferencia.
  mapeo          : salida del modelo -> campos de DatosCandidato (+ validación).
  servicio       : validación de archivos, análisis por lote e importación.
"""
