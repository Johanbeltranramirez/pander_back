from django.urls import path
from apps.candidatos import views_importacion as v
from apps.candidatos import views_importacion_cv as v_cv

# <str:compania> acepta tanto PK entera como UUID (los views validan que exista).
urlpatterns = [
    # Plantillas descargables
    path("plantilla/<str:entidad>/", v.PlantillaExcelView.as_view(), name="plantilla-excel"),

    # Importación de candidatos desde hojas de vida con IA (PDF, DOCX, PNG, JPG, HTML, MD, TXT)
    path("cv/estado/", v_cv.EstadoImportacionCVView.as_view(), name="imp-cv-estado"),

    # Importaciones globales (sin compania en la URL)
    path("companias/",               v.ImportarCompaniasView.as_view(), name="imp-companias"),

    # Importaciones por compañía (matriz de Excel)
    path("companias/<str:compania>/analistas/",     v.ImportarAnalistasView.as_view(),     name="imp-analistas"),
    path("companias/<str:compania>/usuarios/",      v.ImportarUsuariosView.as_view(),      name="imp-usuarios"),
    path("companias/<str:compania>/unidades/",      v.ImportarUnidadesView.as_view(),      name="imp-unidades"),
    path("companias/<str:compania>/vacantes/",      v.ImportarVacantesView.as_view(),      name="imp-vacantes"),
    path("companias/<str:compania>/candidatos/",    v.ImportarCandidatosView.as_view(),    name="imp-candidatos"),
    path("companias/<str:compania>/postulaciones/", v.ImportarPostulacionesView.as_view(), name="imp-postulaciones"),

    # Candidatos desde hojas de vida (multi-archivo, modelo IA)
    path("companias/<str:compania>/candidatos/cv/", v_cv.ImportarCandidatosCVView.as_view(), name="imp-candidatos-cv"),
]
