"""One careful way to call every external API: timeout, readable errors, rate-limit awareness.

Callers never see raw network exceptions - they get a ProviderError whose message is safe to show
to a user (it never contains the API key, which is only ever sent from the server).
"""
import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = 8


class ProviderError(Exception):
    """A readable reason why an external provider could not be used."""

    def __init__(self, message, *, rate_limited=False):
        super().__init__(message)
        self.rate_limited = rate_limited


def get_json(url, params=None, *, timeout=DEFAULT_TIMEOUT, provider='The provider', headers=None):
    query = urllib.parse.urlencode(params or {})
    full = f'{url}?{query}' if query else url
    request = urllib.request.Request(full, headers={'Accept': 'application/json', 'User-Agent': 'FoodWastePredictor/1.0',
                                                    **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ProviderError(f'{provider} is rate limiting requests. Try again in a few minutes.', rate_limited=True) from None
        if e.code in (401, 403):
            raise ProviderError(f'{provider} rejected the request (check the API key and its permissions).') from None
        raise ProviderError(f'{provider} returned an error ({e.code}).') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError(f'Could not reach {provider}. Check the internet connection and try again.') from None
    except ValueError:
        raise ProviderError(f'{provider} sent a response that could not be read.') from None
