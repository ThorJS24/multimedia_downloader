try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
try:
    from _nav import go_tab  # noqa: F401
except ImportError:
    from tests._nav import go_tab  # noqa: F401
import os
import sys
import time
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from werkzeug.serving import make_server

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db import init_db, get_db_connection
from src.web.server import app
from src.security import SESSION_AUTH_TOKEN


SCREENSHOT_DIR = _hermetic.TEST_DATA_DIR / "screenshots" / "phase24"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class TestBrowserPhase24Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="spot_phase24_test_")
        cls.orig_env = os.environ.copy()
        os.environ["TEMP"] = cls.test_dir
        os.environ["TMP"] = cls.test_dir

        # Initialize test DB and populate with realistic mock library items
        cls.db_path = Path(cls.test_dir) / "test_library.db"
        init_db()
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM library_files")
            mock_tracks = [
                ("D:\\downloads\\Daft Punk - Discovery\\01 One More Time.mp3", "01 One More Time.mp3", "One More Time", "Daft Punk", "Discovery", 320, "mp3", 320, 1, 1, 2001, "Electronic", 1, 15, time.time() - 100),
                ("D:\\downloads\\Daft Punk - Discovery\\02 Aerodynamic.mp3", "02 Aerodynamic.mp3", "Aerodynamic", "Daft Punk", "Discovery", 212, "mp3", 320, 2, 1, 2001, "Electronic", 1, 8, time.time() - 500),
                ("D:\\downloads\\Daft Punk - Discovery\\03 Digital Love.mp3", "03 Digital Love.mp3", "Digital Love", "Daft Punk", "Discovery", 301, "mp3", 320, 3, 1, 2001, "Electronic", 1, 12, time.time() - 300),
                ("D:\\downloads\\Daft Punk - Discovery\\04 Harder Better Faster Stronger.mp3", "04 Harder Better Faster Stronger.mp3", "Harder Better Faster Stronger", "Daft Punk", "Discovery", 224, "mp3", 320, 4, 1, 2001, "Electronic", 1, 22, time.time() - 50),
                ("D:\\downloads\\The Weeknd - After Hours\\01 Blinding Lights.mp3", "01 Blinding Lights.mp3", "Blinding Lights", "The Weeknd", "After Hours", 200, "mp3", 320, 1, 1, 2020, "Synth-pop", 1, 45, time.time() - 20),
                ("D:\\downloads\\The Weeknd - After Hours\\02 Save Your Tears.mp3", "02 Save Your Tears.mp3", "Save Your Tears", "The Weeknd", "After Hours", 215, "mp3", 320, 2, 1, 2020, "Synth-pop", 1, 30, time.time() - 80),
                ("D:\\downloads\\Radiohead - OK Computer\\01 Airbag.mp3", "01 Airbag.mp3", "Airbag", "Radiohead", "OK Computer", 284, "mp3", 320, 1, 1, 1997, "Alternative", 1, 5, time.time() - 1200),
                ("D:\\downloads\\Radiohead - OK Computer\\02 Paranoid Android.mp3", "02 Paranoid Android.mp3", "Paranoid Android", "Radiohead", "OK Computer", 383, "mp3", 320, 2, 1, 1997, "Alternative", 1, 18, time.time() - 900),
                ("D:\\downloads\\Radiohead - OK Computer\\03 Subterranean Homesick Alien.mp3", "03 Subterranean Homesick Alien.mp3", "Subterranean Homesick Alien", "Radiohead", "OK Computer", 267, "mp3", 320, 3, 1, 1997, "Alternative", 1, 3, time.time() - 1500),
                ("D:\\downloads\\Taylor Swift - Midnights\\01 Anti-Hero.mp3", "01 Anti-Hero.mp3", "Anti-Hero", "Taylor Swift", "Midnights", 200, "mp3", 320, 1, 1, 2022, "Pop", 1, 14, time.time() - 400),
            ]
            conn.executemany(
                """INSERT OR REPLACE INTO library_files
                   (path, filename, title, artist, album, duration_sec, format, bitrate_kbps, track_number, disc_number, year, genre, has_artwork, play_count, last_played_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                mock_tracks
            )

        # Start background Flask server on free port
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', 0))
        cls.port = sock.getsockname()[1]
        sock.close()

        cls.server = make_server('127.0.0.1', cls.port, app, threaded=True)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}/?token={SESSION_AUTH_TOKEN}"
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
        except Exception:
            pass
        os.environ.clear()
        os.environ.update(cls.orig_env)
        try:
            shutil.rmtree(cls.test_dir, ignore_errors=True)
        except Exception:
            pass

    def test_01_home_page_shelves_and_screenshots(self):
        """Verify Home page dedicated shelves rendering and take screenshot."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1920, "height": 1080})
            page = context.new_page()

            console_errors = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: console_errors.append(str(err)))

            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(500)

            # Ensure on Dashboard / Home
            self.assertTrue(page.locator("#tab-dashboard").is_visible())

            # Verify Home Shelves containers
            self.assertTrue(page.locator("#home-shelf-played-container").is_visible())
            self.assertTrue(page.locator("#home-shelf-added-container").is_visible())
            self.assertTrue(page.locator("#home-shelf-artists-container").is_visible())
            self.assertTrue(page.locator("#home-shelf-albums-container").is_visible())
            self.assertTrue(page.locator("#home-shelf-playlists-container").is_visible())

            # Capture Screenshot 1: Home Shelves
            page.screenshot(path=str(SCREENSHOT_DIR / "01_home_shelves.png"))
            self.assertTrue((SCREENSHOT_DIR / "01_home_shelves.png").exists())

            # Tab switch to Search
            go_tab(page, "search")
            page.wait_for_timeout(300)
            self.assertTrue(page.locator("#tab-search").is_visible())

            # Perform Omni Search
            page.fill("#omni-search-input", "Daft Punk")
            page.wait_for_selector("#omni-results-container:not(.hidden)", timeout=10000)

            # Verify Search Results
            self.assertTrue(page.locator("#omni-results-container").is_visible())
            self.assertTrue(page.locator("#omni-top-result-card").is_visible())
            self.assertTrue(page.locator("#omni-tracks-list").is_visible())

            # Capture Screenshot 2: Omni Search
            page.screenshot(path=str(SCREENSHOT_DIR / "02_omni_search_results.png"))
            self.assertTrue((SCREENSHOT_DIR / "02_omni_search_results.png").exists())

            # Open Album View via JS call or click
            page.evaluate("openAlbumView('Discovery', 'Daft Punk')")
            page.wait_for_timeout(500)
            self.assertTrue(page.locator("#tab-album-detail").is_visible())
            self.assertEqual(page.locator("#album-view-title").inner_text(), "Discovery")
            self.assertIn("Daft Punk", page.locator("#album-view-artist-link").inner_text())

            # Capture Screenshot 3: Album Detail View
            page.screenshot(path=str(SCREENSHOT_DIR / "03_album_detail_view.png"))
            self.assertTrue((SCREENSHOT_DIR / "03_album_detail_view.png").exists())

            # Open Artist View
            page.evaluate("openArtistView('The Weeknd')")
            page.wait_for_timeout(500)
            self.assertTrue(page.locator("#tab-artist-detail").is_visible())
            self.assertEqual(page.locator("#artist-view-name").inner_text(), "The Weeknd")
            self.assertTrue(page.locator("#artist-view-popular-body").is_visible())

            # Capture Screenshot 4: Artist Detail View
            page.screenshot(path=str(SCREENSHOT_DIR / "04_artist_detail_view.png"))
            self.assertTrue((SCREENSHOT_DIR / "04_artist_detail_view.png").exists())

            # Open Playlist View
            page.evaluate("openPlaylistView('Discovery')")
            page.wait_for_timeout(500)
            self.assertTrue(page.locator("#tab-playlist-detail").is_visible())

            # Capture Screenshot 5: Playlist Detail View
            page.screenshot(path=str(SCREENSHOT_DIR / "05_playlist_detail_view.png"))
            self.assertTrue((SCREENSHOT_DIR / "05_playlist_detail_view.png").exists())

            # Open Library Tab
            go_tab(page, "library")
            page.wait_for_timeout(400)
            self.assertTrue(page.locator("#tab-library").is_visible())

            # Capture Screenshot 6: Library View with Clickable Links
            page.screenshot(path=str(SCREENSHOT_DIR / "06_library_clickable_links.png"))
            self.assertTrue((SCREENSHOT_DIR / "06_library_clickable_links.png").exists())

            # Open Lyrics Drawer
            page.evaluate("toggleNowPlayingDrawer()")
            page.wait_for_timeout(350)
            self.assertFalse(page.locator("#drawer-now-playing").evaluate("el => el.classList.contains('translate-x-full')"))

            # Capture Screenshot 7: Player and Lyrics Drawer
            page.screenshot(path=str(SCREENSHOT_DIR / "07_player_and_lyrics_drawer.png"))
            self.assertTrue((SCREENSHOT_DIR / "07_player_and_lyrics_drawer.png").exists())

            # Close Lyrics Drawer and Open Playback Queue Drawer
            page.evaluate("toggleNowPlayingDrawer()")  # toggle off
            page.wait_for_timeout(200)
            page.evaluate("togglePlaybackQueueDrawer()")  # toggle on
            page.wait_for_timeout(350)
            self.assertFalse(page.locator("#drawer-playback-queue").evaluate("el => el.classList.contains('translate-x-full')"))

            # Capture Screenshot 8: Playback Queue Drawer
            page.screenshot(path=str(SCREENSHOT_DIR / "08_playback_queue_drawer.png"))
            self.assertTrue((SCREENSHOT_DIR / "08_playback_queue_drawer.png").exists())

            context.close()
            browser.close()

            # Assert zero severe JS errors
            severe_errors = [e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]
            self.assertEqual(len(severe_errors), 0, f"Uncaught console errors: {severe_errors}")


if __name__ == "__main__":
    unittest.main()
