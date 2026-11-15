"""Build Django's DATABASES entry from a DATABASE_URL environment variable.

    DATABASE_URL=postgres://user:password@host:5432/dbname      -> PostgreSQL (shared, production)
    (unset)                                                       -> local SQLite file (development)

No credentials ever live in the source code: they come from the environment only.
"""
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlparse

POSTGRES_SCHEMES = ('postgres', 'postgresql', 'pgsql')


def database_from_url(url, base_dir, conn_max_age=600):
    if not url:
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': Path(base_dir) / 'db.sqlite3'}
    parsed = urlparse(url)
    if parsed.scheme in ('sqlite', 'sqlite3'):
        name = unquote(parsed.path.lstrip('/')) or 'db.sqlite3'
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': Path(base_dir) / name}
    if parsed.scheme not in POSTGRES_SCHEMES:
        raise ValueError(f'Unsupported DATABASE_URL scheme {parsed.scheme!r}. Use postgres://user:pass@host:5432/dbname')
    name = unquote(parsed.path.lstrip('/'))
    if not parsed.hostname or not name:
        raise ValueError('DATABASE_URL must include a host and a database name.')
    config = {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': name,
        'USER': unquote(parsed.username or ''),
        'PASSWORD': unquote(parsed.password or ''),
        'HOST': parsed.hostname,
        'PORT': str(parsed.port or 5432),
        'CONN_MAX_AGE': conn_max_age,
        'CONN_HEALTH_CHECKS': True,
    }
    options = dict(parse_qsl(parsed.query))
    if options:                          # e.g. ?sslmode=require
        config['OPTIONS'] = options
    return config
