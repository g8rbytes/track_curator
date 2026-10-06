"""Optional real-browser helper (Playwright) for pages that need a human or a login.

A persistent profile under ~/.track-curator/browser keeps your Beatport login and any
solved 1001Tracklists captcha between runs. Install with:
    pip install 'track-curator[browser]' && playwright install chromium
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .http import DEFAULT_HOME


class Browser:
    def __init__(self, profile_dir: Optional[Path] = None, headless: bool = False):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Playwright is not installed: pip install 'track-curator[browser]' && playwright install chromium"
            ) from e
        self._pw = sync_playwright().start()
        # Log-in pages (Beatport's included) bounce browsers that announce they are
        # automated, so drive your installed Google Chrome without the automation flags,
        # falling back to Playwright's bundled Chromium if Chrome isn't installed.
        kwargs = dict(
            headless=headless,
            no_viewport=True,
            accept_downloads=True,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        profile = str(profile_dir or DEFAULT_HOME / "browser")
        try:
            self.ctx = self._pw.chromium.launch_persistent_context(profile, channel="chrome", **kwargs)
        except Exception:
            self.ctx = self._pw.chromium.launch_persistent_context(profile, **kwargs)
        self.ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def html(self, url: str, wait_selector: Optional[str] = None, human_timeout_s: int = 180) -> str:
        """Load url; if a captcha shows, wait for the user to solve it in the window."""
        self.page.goto(url, wait_until="domcontentloaded")
        if wait_selector:
            try:
                self.page.wait_for_selector(wait_selector, timeout=8000)
            except Exception:
                print(f"    Waiting for you to clear the check in the browser window ({url})...")
                self.page.wait_for_selector(wait_selector, timeout=human_timeout_s * 1000)
        return self.page.content()

    def close(self) -> None:
        try:
            self.ctx.close()
        finally:
            self._pw.stop()
