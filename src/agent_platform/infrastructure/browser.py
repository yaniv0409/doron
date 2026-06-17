from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import unescape
from re import sub
from urllib.parse import urljoin

from agent_platform.config.settings import BrowserSettings
from agent_platform.domain.exceptions import BrowserError, ConfigurationError
from agent_platform.domain.models import PageLink

try:
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    raise ConfigurationError(
        "beautifulsoup4 is required for browser extraction. Install it with `pip install -e \".[dev]\"` "
        "or `pip install beautifulsoup4`."
    ) from exc

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
    browser_stage: str


@dataclass(slots=True)
class BrowserTelemetry:
    stage: str
    message: str
    metadata: dict[str, object]


class PlaywrightBrowserEngine:
    def __init__(self, settings: BrowserSettings, telemetry_hook=None) -> None:
        self._settings = settings
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._telemetry_hook = telemetry_hook

    async def start(self) -> None:
        if async_playwright is None:
            raise BrowserError("playwright is not installed")
        if self._browser is not None:
            return
        await self._emit("browser_session", "starting playwright browser", {})
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._settings.headless,
        )
        context = await self._browser.new_context(
            viewport={
                "width": self._settings.viewport_width,
                "height": self._settings.viewport_height,
            },
            locale=self._settings.locale,
            timezone_id=self._settings.timezone_id,
            user_agent=self._settings.user_agent,
            extra_http_headers={
                "Accept-Language": self._settings.locale.replace("-", ",") + ";q=0.9",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            """
        )
        page = await context.new_page()
        page.set_default_timeout(self._settings.default_timeout_ms)
        self._page = page
        await self._emit("browser_session", "browser session ready", {})

    async def navigate(self, url: str) -> PageSnapshot:
        page = await self._require_page()
        try:
            await self._emit("browser_navigation", "navigation started", {"url": url})
            load_state = await asyncio.wait_for(
                self._navigate_with_budget(page, url),
                timeout=self._settings.navigation_timeout_ms / 1000,
            )
            await self._emit(
                "browser_navigation",
                "navigation settled",
                {"url": page.url, "load_state": load_state},
            )
            snapshot = await self._extract_snapshot(page, load_state)
        except asyncio.TimeoutError as exc:
            await self._emit(
                "browser_navigation",
                "navigation timeout",
                {"url": url, "timeout_ms": self._settings.navigation_timeout_ms},
            )
            raise BrowserError(
                f"browser_timeout: navigation exceeded {self._settings.navigation_timeout_ms}ms during page load or network idle"
            ) from exc
        except Exception as exc:  # pragma: no cover
            raise BrowserError(str(exc)) from exc
        return snapshot

    async def extract_text(self) -> PageSnapshot:
        page = await self._require_page()
        try:
            await self._emit("browser_extract", "extracting current page text", {"url": page.url})
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
            await self._emit("browser_navigation", "network idle wait started", {"url": page.url})
            await page.wait_for_load_state(
                "networkidle",
                timeout=self._settings.network_idle_timeout_ms,
            )
            await self._emit("browser_navigation", "network idle reached", {"url": page.url})
            return "networkidle"
        except asyncio.TimeoutError as exc:
            await self._emit(
                "browser_navigation",
                "network idle timeout",
                {"url": page.url, "timeout_ms": self._settings.network_idle_timeout_ms},
            )
            raise BrowserError(
                f"browser_timeout: network idle exceeded {self._settings.network_idle_timeout_ms}ms"
            ) from exc

    async def _extract_snapshot(self, page: Page, load_state: str) -> PageSnapshot:
        await self._emit(
            "browser_extract",
            "snapshot extraction started",
            {"url": page.url, "load_state": load_state},
        )
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
            browser_stage="extract_complete",
        )

    async def _navigate_with_budget(self, page: Page, url: str) -> str:
        await page.goto(url, wait_until="domcontentloaded")
        await self._emit("browser_navigation", "domcontentloaded reached", {"url": page.url})
        return await self._wait_for_network_idle(page)

    async def _emit(self, stage: str, message: str, metadata: dict[str, object]) -> None:
        if self._telemetry_hook is None:
            return
        maybe = self._telemetry_hook(BrowserTelemetry(stage=stage, message=message, metadata=metadata))
        if asyncio.iscoroutine(maybe):
            await maybe


def select_main_html(html: str, *, extract_main_content_only: bool) -> str:
    if not extract_main_content_only:
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
    soup = BeautifulSoup(html, "html.parser")
    return str(soup.body) if soup.body is not None else html


def clean_text_from_html(html: str, max_chars: int) -> str:
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    return _normalize_text(text)[:max_chars]


def extract_links_from_html(html: str, *, base_url: str, max_links: int) -> list[PageLink]:
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
