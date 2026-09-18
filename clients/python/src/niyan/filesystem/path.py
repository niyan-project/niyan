"""Parse canonical Niyān filesystem URLs without discovering local state."""

from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from niyan.http import normalize_host


@dataclass(frozen=True)
class ParsedLocation:
    """Represent one installation, dataset locator, and requested revision."""

    host: str | None
    locator_path: str
    revision: str | None


def parse_location(path: str, *, configured_host: str | None = None, configured_dataset: str | None = None, configured_revision: str | None = None) -> ParsedLocation:
    """Parse a canonical URL or a path interpreted by explicit storage options.

    Parameters
    ----------
    path : str
        Canonical Niyān URL, full locator path, or repository-relative path.
    configured_host : str, optional
        Installation selected when the path has no authority.
    configured_dataset : str, optional
        Dataset prefix used for repository-relative paths.
    configured_revision : str, optional
        Revision supplied as a filesystem storage option.

    Returns
    -------
    ParsedLocation
        Normalized host, full locator path, and requested revision.
    """

    if not isinstance(path, str):
        raise TypeError('Niyān filesystem paths must be strings.')
    parsed_host = None
    is_canonical = path.startswith('niyan://')
    query = ''
    if is_canonical:
        parsed = urlsplit(path)
        if not parsed.netloc or parsed.fragment:
            raise ValueError('A Niyān URL requires an installation authority and cannot contain a fragment.')
        parsed_host = normalize_host(_origin_for_authority(parsed.netloc, parsed.hostname))
        locator_path = unquote(parsed.path).strip('/')
        query = parsed.query
    else:
        if '#' in path:
            raise ValueError('A Niyān path cannot contain a fragment.')
        locator_path, separator, query = path.partition('?')
        locator_path = unquote(locator_path).strip('/')

    parameters = parse_qs(query, keep_blank_values=True)
    if set(parameters) - {'revision'} or any(len(values) != 1 for values in parameters.values()):
        raise ValueError('Niyān URLs accept only one revision query parameter.')
    url_revision = parameters.get('revision', [None])[0]
    if url_revision == '':
        raise ValueError('The revision query parameter cannot be empty.')
    if configured_revision is not None and url_revision is not None and configured_revision != url_revision:
        raise ValueError('The URL revision and filesystem revision option do not agree.')

    normalized_configured_host = normalize_host(configured_host) if configured_host else None
    if parsed_host and normalized_configured_host and parsed_host != normalized_configured_host:
        raise ValueError('The URL authority and filesystem host option do not agree.')
    host = parsed_host or normalized_configured_host
    if configured_dataset:
        dataset_path = configured_dataset.strip('/')
        if is_canonical:
            if locator_path != dataset_path and not locator_path.startswith(f'{dataset_path}/'):
                raise ValueError('The URL path and filesystem dataset option do not agree.')
        else:
            locator_path = f'{dataset_path}/{locator_path}' if locator_path else dataset_path
    if not locator_path:
        raise ValueError('A Niyān path must identify a dataset.')
    if any(component in ('', '.', '..') for component in locator_path.split('/')):
        raise ValueError('A Niyān path contains an invalid component.')
    return ParsedLocation(host=host, locator_path=locator_path, revision=url_revision or configured_revision)


def strip_protocol(path: str) -> str:
    """Remove the Niyān protocol and authority for fsspec URL handling."""

    if path.startswith('niyan://'):
        parsed = urlsplit(path)
        suffix = f'?{parsed.query}' if parsed.query else ''
        return f'{parsed.path.strip("/")}{suffix}'
    if path.startswith('niyan::'):
        return path[len('niyan::') :]
    return path


def options_from_url(path: str) -> dict[str, str]:
    """Extract non-secret filesystem construction options from a canonical URL."""

    if not path.startswith('niyan://'):
        return {}
    parsed = urlsplit(path)
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    options = {'host': normalize_host(_origin_for_authority(parsed.netloc, parsed.hostname))}
    if set(parameters) == {'revision'} and len(parameters['revision']) == 1 and parameters['revision'][0]:
        options['revision'] = parameters['revision'][0]
    return options


def canonical_name(*, host: str, dataset_path: str, repository_path: str, resolved_commit: str) -> str:
    """Return a reusable canonical URL pinned to an exact commit."""

    authority = urlsplit(host).netloc
    full_path = '/'.join(part for part in (dataset_path.strip('/'), repository_path.strip('/')) if part)
    return f'niyan://{authority}/{quote(full_path, safe="/")}?{urlencode({"revision": resolved_commit})}'


def _origin_for_authority(authority: str, hostname: str | None) -> str:
    """Apply the accepted HTTPS default with loopback HTTP development support."""

    scheme = 'http' if hostname in ('localhost', '127.0.0.1', '::1') else 'https'
    return f'{scheme}://{authority}'
