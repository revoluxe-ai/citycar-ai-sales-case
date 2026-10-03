"""Small read-only client for amoCRM API v4."""
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AmoAPIError(RuntimeError):
    pass


class AmoClient:
    def __init__(self, subdomain, token, *, opener=None, sleep=None):
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", subdomain):
            raise ValueError("AMO_SUBDOMAIN must be a subdomain, not a URL")
        if not token:
            raise ValueError("AMO_ACCESS_TOKEN is required")
        self.base = f"https://{subdomain}.amocrm.ru/api/v4"
        self.headers = {"Authorization": f"Bearer {token}"}
        self.opener = opener or urlopen
        self.sleep = sleep or time.sleep

    def _page(self, resource, params):
        request = Request(f"{self.base}/{resource}?{urlencode(params)}",
                          headers=self.headers, method="GET")
        for attempt in range(4):
            self.sleep(0.2)
            try:
                with self.opener(request, timeout=30) as response:
                    if response.status == 204:
                        return None
                    return json.load(response)
            except HTTPError as error:
                retryable = error.code == 429 or error.code in (500, 502, 503, 504)
                if not retryable or attempt == 3:
                    raise AmoAPIError(f"amoCRM HTTP {error.code}; collection stopped") from None
                delay = 2 ** attempt
                try:
                    delay = max(delay, float(error.headers.get("Retry-After", delay)))
                except (TypeError, ValueError, AttributeError):
                    pass
                self.sleep(min(delay, 30))
            except URLError:
                if attempt == 3:
                    raise AmoAPIError("amoCRM connection failed; collection stopped") from None
                self.sleep(2 ** attempt)
            except (ValueError, UnicodeError):
                raise AmoAPIError("amoCRM returned invalid JSON") from None
        raise AmoAPIError("Collection stopped")

    def get_all(self, resource, filters=None):
        if resource not in {"leads", "tasks"}:
            raise ValueError("Only read-only leads/tasks endpoints are supported")
        records = {}
        for page in range(1, 10001):
            params = {**(filters or {}), "order[id]": "asc", "limit": 250, "page": page}
            data = self._page(resource, params)
            if data is None:
                return list(records.values())
            if not isinstance(data, dict):
                raise AmoAPIError("Unexpected amoCRM response")
            embedded = data.get("_embedded")
            rows = embedded.get(resource) if isinstance(embedded, dict) else None
            if not isinstance(rows, list):
                raise AmoAPIError("Missing collection in amoCRM response")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), int):
                    raise AmoAPIError("Invalid record in amoCRM response")
                records[row["id"]] = row
            if "next" not in data.get("_links", {}):
                return list(records.values())
        raise AmoAPIError("Pagination safety limit reached")
