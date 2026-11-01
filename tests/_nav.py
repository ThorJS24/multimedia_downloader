"""Navigation helper for browser tests: drives the real sidebar + sub-navigation, like a user."""

# Which sidebar destination owns each section (mirrors HUBS in static/js/core.js).
HUB_OF = {
    "dashboard": "home",
    "fetch": "downloads", "queue": "downloads", "history": "downloads", "matching": "downloads",
    "library": "library", "search": "library", "intelligence": "library", "quality_lab": "library",
    "comparator": "library", "optimizer": "library",
    "sync": "automation", "monitor": "automation", "watcher": "automation", "rules": "automation", "plugins": "automation",
    "settings": "settings", "logs": "settings",
}


def go_tab(page, tab: str) -> bool:
    """Open a section by clicking the sidebar destination, then its sub-tab. Returns True once visible."""
    if tab == "dashboard":
        page.click("#tab-btn-dashboard")
    else:
        sub = page.locator(f"#tab-btn-{tab}")
        if not sub.is_visible():
            page.click(f"#nav-hub-{HUB_OF[tab]}")
        sub = page.locator(f"#tab-btn-{tab}")
        if sub.is_visible() and sub.get_attribute("aria-selected") != "true":
            sub.click()
    page.wait_for_selector(f"#tab-{tab}:not(.hidden)", timeout=5000)
    return True
