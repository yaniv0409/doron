from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from re import sub
from urllib.parse import urljoin

from agent_platform.config.settings import BrowserSettings
from agent_platform.domain.exceptions import BrowserError
from agent_platform.domain.models import PageLink

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None

try:
    from inscriptis import get_text as html_to_text
except ImportError:  # pragma: no cover
    html_to_text = None

try:
    from playwright.async_api import Browser, Page, async_playwright
except ImportError:  # pragma: no cover
    Browser = None
    Page = None
    async_playwright = None


@dataclass(slots=True)
class PageSnapshot:
    url: str
    title: str
    text: str
    links: list[PageLink]
    load_state: str


class PlaywrightBrowserEngine:
    def __init__(self, settings: BrowserSettings) -> None:
        self._settings = settings
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def start(self) -> None:
        if async_playwright is None:
            raise BrowserError("playwright is not installed")
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._settings.headless,
        )
        page = await self._browser.new_page()
        page.set_default_timeout(self._settings.default_timeout_ms)
        self._page = page

    async def navigate(self, url: str) -> PageSnapshot:
        page = await self._require_page()
        try:
            await page.goto(url, wait_until="domcontentloaded")
            load_state = await self._wait_for_network_idle(page)
            snapshot = await self._extract_snapshot(page, load_state)
        except Exception as exc:  # pragma: no cover
            raise BrowserError(str(exc)) from exc
        return snapshot

    async def extract_text(self) -> PageSnapshot:
        page = await self._require_page()
        try:
            return await self._extract_snapshot(page, "current_page")
        except Exception as exc:  # pragma: no cover
            raise BrowserError(str(exc)) from exc

    async def screenshot(self, path: str) -> None:
        page = await self._require_page()
        await page.screenshot(path=path, full_page=True)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = None
        self._page = None
        self._playwright = None

    async def _require_page(self) -> Page:
        if self._page is None:
            await self.start()
        if self._page is None:
            raise BrowserError("browser page is not available")
        return self._page

    async def _wait_for_network_idle(self, page: Page) -> str:
        try:
            await page.wait_for_load_state(
                "networkidle",
                timeout=self._settings.network_idle_timeout_ms,
            )
            return "networkidle"
        except Exception:
            return "domcontentloaded_fallback"

    async def _extract_snapshot(self, page: Page, load_state: str) -> PageSnapshot:
        title = await page.title()
        html = await page.content()
        scoped_html = select_main_html(
            html,
            extract_main_content_only=self._settings.extract_main_content_only,
        )
        text = clean_text_from_html(scoped_html, self._settings.content_text_max_chars)
        links = extract_links_from_html(
            scoped_html,
            base_url=page.url,
            max_links=self._settings.max_links_per_page,
        )
        return PageSnapshot(
            url=page.url,
            title=title,
            text=text,
            links=links,
            load_state=load_state,
        )


def select_main_html(html: str, *, extract_main_content_only: bool) -> str:
    if not extract_main_content_only:
        return extract_body_html(html)
    if BeautifulSoup is None:
        return extract_body_html(html)
    soup = BeautifulSoup(html, "html.parser")
    for selector in ("main", "article", '[role="main"]'):
        node = soup.select_one(selector)
        if node is not None:
            return str(node)
    body = soup.body
    if body is not None:
        return str(body)
    return html


def extract_body_html(html: str) -> str:
    if BeautifulSoup is None:
        return html
    soup = BeautifulSoup(html, "html.parser")
    return str(soup.body) if soup.body is not None else html


def clean_text_from_html(html: str, max_chars: int) -> str:
    if html_to_text is not None:
        text = html_to_text(html)
    elif BeautifulSoup is not None:
        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    else:
        text = _strip_tags(html)
    text = _normalize_text(text)
    return text[:max_chars]


def extract_links_from_html(html: str, *, base_url: str, max_links: int) -> list[PageLink]:
    if BeautifulSoup is not None:
        return _extract_links_with_bs4(html, base_url=base_url, max_links=max_links)
    parser = AnchorCollector(base_url=base_url, max_links=max_links)
    parser.feed(html)
    return parser.links


def _extract_links_with_bs4(html: str, *, base_url: str, max_links: int) -> list[PageLink]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[PageLink] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = normalize_href(anchor.get("href", ""), base_url)
        if not href or href in seen:
            continue
        seen.add(href)
        text = _normalize_text(anchor.get_text(" ", strip=True))
        title = anchor.get("title")
        links.append(PageLink(text=text or href, href=href, title=title))
        if len(links) >= max_links:
            break
    return links


def normalize_href(href: str, base_url: str) -> str:
    href = href.strip()
    if not href:
        return ""
    absolute = urljoin(base_url, href)
    if absolute.startswith(("javascript:", "mailto:", "tel:")):
        return ""
    return absolute


def _normalize_text(text: str) -> str:
    cleaned = unescape(text)
    cleaned = sub(r"\r\n?", "\n", cleaned)
    cleaned = sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _strip_tags(html: str) -> str:
    no_scripts = sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    no_tags = sub(r"(?s)<[^>]+>", " ", no_scripts)
    return no_tags


class AnchorCollector(HTMLParser):
    def __init__(self, *, base_url: str, max_links: int) -> None:
        super().__init__()
        self._base_url = base_url
        self._max_links = max_links
        self._seen: set[str] = set()
        self._current_href: str | None = None
        self._current_title: str | None = None
        self._current_text: list[str] = []
        self.links: list[PageLink] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or len(self.links) >= self._max_links:
            return
        attr_map = dict(attrs)
        href = normalize_href(attr_map.get("href") or "", self._base_url)
        if not href or href in self._seen:
            self._current_href = None
            return
        self._current_href = href
        self._current_title = attr_map.get("title")
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._current_href is None:
            return
        self._seen.add(self._current_href)
        text = _normalize_text(" ".join(self._current_text))
        self.links.append(
            PageLink(
                text=text or self._current_href,
                href=self._current_href,
                title=self._current_title,
            )
        )
        self._current_href = None
        self._current_title = None
        self._current_text = []
