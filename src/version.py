"""Single source of truth for the application version (read by the CLI, diagnostics and build scripts)."""
APP_NAME = "Spotify Downloader Studio"
__version__ = "2.1.0"
VERSION_LABEL = f"{APP_NAME} v{__version__}"
RELEASE_REPO = "ThorJS24/multimedia_downloader"
# Where the release pipeline publishes the update feed. Update checks stay off until someone enters a feed address.
RELEASE_FEED_URL = f"https://github.com/{RELEASE_REPO}/releases/latest/download/updates.json"
