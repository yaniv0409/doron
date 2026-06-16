from __future__ import annotations

from dataclasses import dataclass

from agent_platform.config.settings import BrowserSettings
from agent_platform.domain.exceptions import BrowserError

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
            title = await page.title()
            text = await page.locator("body").inner_text()
        except Exception as exc:  # pragma: no cover
            raise BrowserError(str(exc)) from exc
        return PageSnapshot(url=url, title=title, text=text[:10_000])

    async def extract_text(self) -> str:
        page = await self._require_page()
        try:
            return await page.locator("body").inner_text()
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
