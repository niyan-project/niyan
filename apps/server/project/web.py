from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseNotAllowed


RESERVED_PREFIXES = ('admin/', 'api/', 'git/', 'static/')


def spa_entry(request, route=''):
    """Serve the generated Nuxt entry document for non-Django browser routes.

    Parameters
    ----------
    request : django.http.HttpRequest
        Browser request for a client-side route.
    route : str, optional
        Unmatched path captured only to protect Django-owned prefixes.

    Returns
    -------
    django.http.FileResponse
        Generated Nuxt SPA entry document.

    Raises
    ------
    django.http.Http404
        If the frontend build is absent or a reserved route was not matched.
    """

    if request.method not in {'GET', 'HEAD'}:
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    normalized_route = route.lstrip('/')
    if normalized_route.startswith(RESERVED_PREFIXES):
        raise Http404
    index_path = Path(settings.NIYAN_WEB_DIST_ROOT) / 'index.html'
    if not index_path.is_file():
        raise Http404('The Niyān web application has not been built.')
    return FileResponse(index_path.open('rb'), content_type='text/html; charset=utf-8')
