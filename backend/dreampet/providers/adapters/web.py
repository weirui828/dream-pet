"""Search and fetch adapters. Only these touch the open web."""

from __future__ import annotations

import httpx

from dreampet.config import RoleConfig
from dreampet.providers.base import FetchedPage, ProviderError, SearchResult
from dreampet.text import strip_html

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


def _client(user_agent: str) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": user_agent}, follow_redirects=True)


class SearxngSearch:
    def __init__(self, rc: RoleConfig, user_agent: str):
        self.url = (rc.url or "http://localhost:8080").rstrip("/")
        self.http = _client(user_agent)

    def search(self, query: str, k: int = 5, language: str = "en") -> list[SearchResult]:
        try:
            r = self.http.get(f"{self.url}/search", params={"q": query, "format": "json", "language": language})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"searxng: {exc}") from exc
        return [SearchResult(title=x.get("title", ""), url=x["url"], snippet=x.get("content", ""), source="searxng")
                for x in r.json().get("results", [])[:k] if x.get("url")]


class TavilySearch:
    def __init__(self, rc: RoleConfig, user_agent: str):
        if not rc.api_key:
            raise ProviderError("tavily needs api_key")
        self.key = rc.api_key
        self.http = _client(user_agent)

    def search(self, query: str, k: int = 5, language: str = "en") -> list[SearchResult]:
        try:
            r = self.http.post("https://api.tavily.com/search", json={"query": query, "max_results": k},
                               headers={"Authorization": f"Bearer {self.key}"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"tavily: {exc}") from exc
        return [SearchResult(title=x.get("title", ""), url=x["url"], snippet=x.get("content", ""), source="tavily")
                for x in r.json().get("results", [])[:k]]


class BraveSearch:
    def __init__(self, rc: RoleConfig, user_agent: str):
        if not rc.api_key:
            raise ProviderError("brave needs api_key")
        self.key = rc.api_key
        self.http = _client(user_agent)

    def search(self, query: str, k: int = 5, language: str = "en") -> list[SearchResult]:
        try:
            r = self.http.get("https://api.search.brave.com/res/v1/web/search",
                              params={"q": query, "count": k, "search_lang": language.split("-")[0]},
                              headers={"X-Subscription-Token": self.key, "Accept": "application/json"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"brave: {exc}") from exc
        items = r.json().get("web", {}).get("results", [])
        return [SearchResult(title=x.get("title", ""), url=x["url"], snippet=strip_html(x.get("description", "")),
                             source="brave") for x in items[:k]]


class ExaSearch:
    def __init__(self, rc: RoleConfig, user_agent: str):
        if not rc.api_key:
            raise ProviderError("exa needs api_key")
        self.key = rc.api_key
        self.http = _client(user_agent)

    def search(self, query: str, k: int = 5, language: str = "en") -> list[SearchResult]:
        try:
            r = self.http.post("https://api.exa.ai/search", json={"query": query, "numResults": k},
                               headers={"x-api-key": self.key})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"exa: {exc}") from exc
        return [SearchResult(title=x.get("title") or "", url=x["url"], snippet=x.get("text", "")[:300], source="exa")
                for x in r.json().get("results", [])[:k]]


class TrafilaturaFetch:
    def __init__(self, rc: RoleConfig, user_agent: str):
        self.http = _client(user_agent)

    def fetch(self, url: str) -> FetchedPage:
        try:
            r = self.http.get(url)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"fetch {url}: {exc}") from exc
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise ProviderError(f"fetch {url}: unsupported content-type {ctype}")
        html = r.text
        title = ""
        try:
            import trafilatura

            text = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
            meta = trafilatura.extract_metadata(html)
            title = (meta.title if meta else "") or ""
        except ImportError:
            text = strip_html(html)
        if not title:
            import re

            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
            title = strip_html(m.group(1)) if m else url
        return FetchedPage(url=str(r.url), title=title.strip(), text=text)


class JinaFetch:
    def __init__(self, rc: RoleConfig, user_agent: str):
        self.key = rc.api_key
        self.http = _client(user_agent)

    def fetch(self, url: str) -> FetchedPage:
        headers = {"Accept": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        try:
            r = self.http.get(f"https://r.jina.ai/{url}", headers=headers)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"jina: {exc}") from exc
        d = r.json().get("data", {})
        return FetchedPage(url=d.get("url", url), title=d.get("title", ""), text=d.get("content", ""))


class FirecrawlFetch:
    def __init__(self, rc: RoleConfig, user_agent: str):
        if not rc.api_key:
            raise ProviderError("firecrawl needs api_key")
        self.key = rc.api_key
        self.http = _client(user_agent)

    def fetch(self, url: str) -> FetchedPage:
        try:
            r = self.http.post("https://api.firecrawl.dev/v1/scrape", json={"url": url, "formats": ["markdown"]},
                               headers={"Authorization": f"Bearer {self.key}"})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"firecrawl: {exc}") from exc
        d = r.json().get("data", {})
        return FetchedPage(url=url, title=(d.get("metadata") or {}).get("title", ""), text=d.get("markdown", ""))
