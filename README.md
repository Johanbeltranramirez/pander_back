## INSTRUCCIONES PARA GENERAR MIGRACIONES A LA BD (Local)
Correr estos comandos (DENTRO DE LA CARPETA `pander_back`):
1. python manage.py migrate (Crear BD en PostgreSQL con el nombre 'pander_db')
2. Usar credenciales de Admin para ingreso `Usuario: admin | Pwd: Admin1234*`

## INSTRUCCIONES PARA CORRER LA APP O SERVIDOR
Correr estos comandos (DENTRO DE LA CARPETA `pander_back`):
1. python -m venv env
2. env\Scripts\activate
3. pip install -r requirements.txt
4. python manage.py runserver

## DOCUMENTACIÓN E IMPLEMENTACIÓN DEL MODELO RNN

```
./apps/candidatos/
    ├── ia/
    │   ├── entrenar_colab.ipynb              entrenamiento del modelo en Google Colab
    │   └── documentacion_importacion_ia.ipynb   Documentación del Modelo
    │   ├── ner_documentos.py             el modelo (BiLSTM-CRF) y su CLI: gen / train / predict
    │   ├── modelo/modelo.pt              el modelo ya entrenado
    │   ├── lectores.py                   texto de PDF, DOCX, imágenes, HTML, MD, TXT
    │   ├── extractor.py                  carga el modelo una sola vez y extrae
    │   ├── mapeo.py                      salida del modelo → campos de DatosCandidato + validaciones
    │   └── servicio.py                   validar archivos, analizar lotes, importar
    └── pruebas/                          pruebas unitarias y de integración
```