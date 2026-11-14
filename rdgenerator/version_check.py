"""Hinweis auf neue RustDesk-Versionen fuer die Generator-Seite.

Fragt das neueste RustDesk-Release bei GitHub ab und vergleicht es mit den
Versionen, die dieses rdgen im Dropdown anbietet. Ist die neueste Version
noch nicht dabei, wird zusaetzlich geprueft, ob das Original-rdgen
(bryangerlach/rdgen) sie bereits unterstuetzt. Dann genuegt ein Abgleich des
Forks; andernfalls muessen dort erst die Build-Patches angepasst werden.

Die Ergebnisse werden zwischengespeichert, damit GitHub nicht bei jedem
Seitenaufruf abgefragt wird. Ist GitHub nicht erreichbar, gibt es keinen
Hinweis und die Seite funktioniert normal weiter.
"""
import logging
import re

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

RUSTDESK_LATEST_URL = 'https://api.github.com/repos/rustdesk/rustdesk/releases/latest'
UPSTREAM_REPO = 'bryangerlach/rdgen'
UPSTREAM_FORMS_URL = f'https://raw.githubusercontent.com/{UPSTREAM_REPO}/refs/heads/master/rdgenerator/forms.py'

CACHE_KEY = 'rdgen:version_notice'
CACHE_TTL = 6 * 60 * 60          # erfolgreiche Abfrage: 6 Stunden
CACHE_TTL_ERROR = 30 * 60        # GitHub nicht erreichbar: nach 30 Minuten erneut versuchen
TIMEOUT = 4                      # Sekunden je Anfrage

_VERSION_RE = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')


def _parse(version):
    m = _VERSION_RE.match((version or '').strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _offered_versions():
    """Versionen aus dem Dropdown, ohne 'master' (nightly)."""
    from .forms import GenerateForm
    choices = GenerateForm.base_fields['version'].choices
    return [value for value, _label in choices if _parse(value)]


def _github_get(url, accept='application/vnd.github+json'):
    headers = {'Accept': accept, 'User-Agent': 'rdgen-version-check'}
    token = getattr(settings, 'GHBEARER', '')
    if token and 'api.github.com' in url:
        response = requests.get(url, headers={**headers, 'Authorization': f'Bearer {token}'}, timeout=TIMEOUT)
        # Ein auf das eigene Repo beschraenktes Token kann fremde Repos ablehnen;
        # dann ohne Token versuchen (oeffentliche Daten, 60 Anfragen/Stunde).
        if response.status_code not in (401, 403):
            response.raise_for_status()
            return response
    response = requests.get(url, headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def _fetch_notice():
    latest_tag = _github_get(RUSTDESK_LATEST_URL).json().get('tag_name', '')
    latest = _parse(latest_tag)
    if not latest:
        return {}

    offered = [_parse(v) for v in _offered_versions()]
    if latest in offered or (offered and latest <= max(offered)):
        return {}

    latest_str = '.'.join(str(x) for x in latest)
    upstream_supported = False
    try:
        upstream_forms = _github_get(UPSTREAM_FORMS_URL, accept='text/plain').text
        upstream_supported = (f"'{latest_str}'" in upstream_forms) or (f'"{latest_str}"' in upstream_forms)
    except requests.RequestException as exc:
        logger.info('rdgen version check: upstream forms.py not reachable: %s', exc)

    return {
        'latest': latest_str,
        'release_url': f'https://github.com/rustdesk/rustdesk/releases/tag/{latest_tag}',
        'upstream_supported': upstream_supported,
        'upstream_url': f'https://github.com/{UPSTREAM_REPO}',
    }


def get_version_notice():
    """Hinweis-Daten fuer das Template oder None, wenn nichts anzuzeigen ist."""
    notice = cache.get(CACHE_KEY)
    if notice is None:
        try:
            notice = _fetch_notice()
            cache.set(CACHE_KEY, notice, CACHE_TTL)
        except (requests.RequestException, ValueError) as exc:
            logger.info('rdgen version check failed: %s', exc)
            notice = {}
            cache.set(CACHE_KEY, notice, CACHE_TTL_ERROR)
    return notice or None
