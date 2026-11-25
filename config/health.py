from django.db import connection
from django.http import JsonResponse


def health(request):
    """Endpoint para las probes de Kubernetes.
    Con ?db=1 también comprueba la conexión a PostgreSQL (readiness)."""
    if request.GET.get('db'):
        try:
            with connection.cursor() as cur:
                cur.execute('SELECT 1')
        except Exception:
            return JsonResponse({'status': 'error', 'db': False}, status=503)
        return JsonResponse({'status': 'ok', 'db': True})
    return JsonResponse({'status': 'ok'})
