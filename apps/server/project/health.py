import os

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def live(request):
    """Report that the application process can serve HTTP requests."""

    return JsonResponse({'status': 'ok'})


@require_GET
@never_cache
def ready(request):
    """Report readiness of the control-plane database and Git storage."""

    checks = {'database': False, 'git_root': False}
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        checks['database'] = True
    except Exception:
        # The response deliberately omits backend exceptions and credentials.
        pass

    git_root = settings.REPOSITORIES_ROOT
    checks['git_root'] = git_root.is_dir() and os.access(git_root, os.R_OK | os.W_OK | os.X_OK)
    status = 200 if all(checks.values()) else 503
    return JsonResponse({'status': 'ok' if status == 200 else 'unavailable', 'checks': checks}, status=status)
