import ipaddress
import json
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


MISSING_URL = ('Set public_url in .hub-store/remote.json to your HTTPS catalog URL '
               '(for example https://catalog.example.org/mcp), or pass --url with that URL.')
INVALID_URL = ('Invalid catalog URL: use an HTTPS origin plus /mcp, with a valid host and port, '
               'and no credentials, query, fragment, backslash, whitespace or control characters.')


def validate_catalog_url(url):
    if (not isinstance(url, str) or not url or not url.isascii()
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
            or any(c in url for c in '@?#\\')):
        raise ValueError(INVALID_URL)
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
        if (parsed.scheme != 'https' or not host or parsed.path != '/mcp'
                or parsed.netloc.endswith(':') or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        if ':' in host:
            if not re.fullmatch(r'[0-9a-fA-F:]+', host):
                raise ValueError
            ipaddress.IPv6Address(host)
            authority = '[' + host + ']'
        else:
            if len(host) > 253 or not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                                          for label in host.split('.')):
                raise ValueError
            authority = host
        if port is not None:
            authority += ':' + str(port)
        if parsed.netloc.lower() != authority.lower():
            raise ValueError
    except ValueError:
        raise ValueError(INVALID_URL) from None
    return url


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate configuration key')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('invalid configuration constant')


def catalog_url(root, override=None):
    if override is not None:
        return validate_catalog_url(override)
    path = Path(root).absolute() / '.hub-store' / 'remote.json'
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Remote configuration must not use symlinks.')
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Remote configuration must be an ordinary file.')
        with path.open(encoding='utf-8') as stream:
            config = json.load(stream, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except FileNotFoundError:
        raise ValueError(MISSING_URL) from None
    except (OSError, UnicodeError, ValueError):
        raise ValueError('Invalid .hub-store/remote.json; use a JSON object with unique keys and public_url.') from None
    if not isinstance(config, dict):
        raise ValueError('Invalid .hub-store/remote.json; use a JSON object with public_url.')
    if 'public_url' not in config:
        raise ValueError(MISSING_URL)
    return validate_catalog_url(config['public_url'])


def oauth_discovery_url(url):
    parsed = urlsplit(validate_catalog_url(url))
    return parsed.scheme + '://' + parsed.netloc + '/.well-known/oauth-authorization-server'
