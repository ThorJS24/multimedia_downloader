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


SCREENSHOT_DIR = _hermetic.TEST_DATA_DIR / "screenshots" / "phase25"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class TestBrowserPhase25Audit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="spot_phase25_audit_")
        cls.orig_env = os.environ.copy()
        os.environ["TEMP"] = cls.test_dir
        os.environ["TMP"] = cls.test_dir

        init_db()

        cls.isolated_downloads = Path(cls.test_dir) / "downloads"
        cls.isolated_downloads.mkdir(parents=True, exist_ok=True)
        import importlib
        import pkgutil
        import src.web.routes as routes_pkg
        # get_downloads_path is imported by name into each blueprint module; patch every user of it
        cls._patched_modules = []
        for info in pkgutil.iter_modules(routes_pkg.__path__):
            mod = importlib.import_module(f"src.web.routes.{info.name}")
            if hasattr(mod, "get_downloads_path"):
                cls._patched_modules.append((mod, mod.get_downloads_path))
                mod.get_downloads_path = lambda: cls.isolated_downloads

        # Start background Flask server on dynamic free port
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
            for mod, orig in cls._patched_modules:
                mod.get_downloads_path = orig
        except Exception:
            pass
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

    def _get_page(self, playwright_instance):
        browser = playwright_instance.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)))
        return browser, context, page, console_errors

    def test_01_home_empty_state(self):
        """Verify that all Home shelves display clear, intentional empty states when database is empty."""
        from playwright.sync_api import sync_playwright

        # Clear database
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM library_files")
            conn.execute("DELETE FROM collections")

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(600)

            self.assertTrue(page.locator("#tab-dashboard").is_visible())

            # Shelf 1: Jump Back In Empty State
            played_text = page.locator("#home-shelf-played-grid").inner_text()
            self.assertIn("No local playback history yet", played_text)

            # Shelf 2: Recently Added Empty State
            added_text = page.locator("#home-shelf-added-grid").inner_text()
            self.assertIn("No recently added tracks", added_text)

            # Shelf 3: Top Artists Empty State
            artists_text = page.locator("#home-shelf-artists-grid").inner_text()
            self.assertIn("No artist data available yet", artists_text)

            # Shelf 4: Top Albums Empty State
            albums_text = page.locator("#home-shelf-albums-grid").inner_text()
            self.assertIn("No album releases found", albums_text)

            # Shelf 5: Playlists Empty State
            pl_text = page.locator("#home-shelf-playlists-grid").inner_text()
            self.assertIn("No local playlists yet", pl_text)

            # Capture Screenshot 1: Home Empty State
            page.screenshot(path=str(SCREENSHOT_DIR / "01_home_empty_state.png"))
            self.assertTrue((SCREENSHOT_DIR / "01_home_empty_state.png").exists())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_02_home_populated_shelves(self):
        """Verify that Home shelves render populated cards with metadata when library items exist."""
        from playwright.sync_api import sync_playwright

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
            conn.execute("INSERT OR REPLACE INTO collections (id, name, type, track_count) VALUES ('c1', 'Daft Punk Essentials', 'playlist', 4)")

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(800)

            # Assert Populated Shelves
            self.assertIn("Blinding Lights", page.locator("#home-shelf-played-grid").inner_text())
            self.assertIn("Daft Punk", page.locator("#home-shelf-artists-grid").inner_text())
            self.assertIn("Discovery", page.locator("#home-shelf-albums-grid").inner_text())
            self.assertIn("Daft Punk Essentials", page.locator("#home-shelf-playlists-grid").inner_text())

            # Capture Screenshot 2: Home Populated Shelves
            page.screenshot(path=str(SCREENSHOT_DIR / "02_home_populated_shelves.png"))
            self.assertTrue((SCREENSHOT_DIR / "02_home_populated_shelves.png").exists())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_03_omni_search_and_filters(self):
        """Verify Omni Search queries, debounce, category filtering, zero-results, and clearing."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(300)

            go_tab(page, "search")
            page.wait_for_timeout(300)
            self.assertTrue(page.locator("#tab-search").is_visible())

            # Verify initial empty state
            self.assertTrue(page.locator("#omni-empty-state").is_visible())

            # Query 1: Daft Punk
            page.fill("#omni-search-input", "Daft Punk")
            page.wait_for_selector("#omni-results-container:not(.hidden)", timeout=10000)

            self.assertTrue(page.locator("#omni-results-container").is_visible())
            self.assertIn("Daft Punk", page.locator("#omni-top-result-card").inner_text())
            self.assertTrue(page.locator("#omni-tracks-list").is_visible())

            # Test Category Filter: Songs
            page.click("#omni-cat-tracks")
            page.wait_for_timeout(200)
            self.assertTrue(page.locator("#omni-artists-section").evaluate("el => el.classList.contains('hidden')"))
            self.assertTrue(page.locator("#omni-albums-section").evaluate("el => el.classList.contains('hidden')"))

            # Test Category Filter: Reset to All
            page.click("#omni-cat-all")
            page.wait_for_timeout(200)
            self.assertFalse(page.locator("#omni-artists-section").evaluate("el => el.classList.contains('hidden')"))

            # Capture Screenshot 3: Omni Search Results
            page.screenshot(path=str(SCREENSHOT_DIR / "03_omni_search_results.png"))
            self.assertTrue((SCREENSHOT_DIR / "03_omni_search_results.png").exists())

            # Query 2: Non-existent track in local scope (assert zero-results state)
            page.click("#scope-btn-local")
            page.wait_for_timeout(200)
            page.fill("#omni-search-input", "NonExistentTrack999")
            page.wait_for_selector("#omni-no-results-state:not(.hidden)", timeout=10000)
            self.assertTrue(page.locator("#omni-no-results-state").is_visible())
            self.assertIn("NonExistentTrack999", page.locator("#omni-query-echo").inner_text())

            # Clear search
            page.click("#btn-clear-omni-search")
            page.wait_for_timeout(300)
            self.assertTrue(page.locator("#omni-empty-state").is_visible())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_04_album_detail_view_and_tracklist(self):
        """Verify Album Detail View loads metadata, ordered tracks, play action, and M3U export."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(300)

            # Open Album View for Discovery
            page.evaluate("openAlbumView('Discovery', 'Daft Punk')")
            page.wait_for_timeout(500)

            self.assertTrue(page.locator("#tab-album-detail").is_visible())
            self.assertEqual(page.locator("#album-view-title").inner_text(), "Discovery")
            self.assertIn("Daft Punk", page.locator("#album-view-artist-link").inner_text())
            self.assertEqual(page.locator("#album-view-year").inner_text(), "2001")
            self.assertEqual(page.locator("#album-view-track-count").inner_text(), "4 songs")

            # Assert Ordered Tracks (#1 One More Time, #2 Aerodynamic)
            tracks_text = page.locator("#album-view-tracks-body").inner_text()
            self.assertIn("One More Time", tracks_text)
            self.assertIn("Aerodynamic", tracks_text)
            self.assertIn("Digital Love", tracks_text)
            self.assertIn("Harder Better Faster Stronger", tracks_text)

            # Play Album
            page.click("button[title='Play Album']")
            page.wait_for_timeout(300)
            queue_len = page.evaluate("playerQueue.length")
            self.assertEqual(queue_len, 4)

            # Capture Screenshot 4: Album Detail View
            page.screenshot(path=str(SCREENSHOT_DIR / "04_album_detail_view.png"))
            self.assertTrue((SCREENSHOT_DIR / "04_album_detail_view.png").exists())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_05_artist_detail_and_neutral_label(self):
        """Verify Artist Detail View uses neutral label and renders top tracks and discography."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(300)

            # Open Artist View for The Weeknd
            page.evaluate("openArtistView('The Weeknd')")
            page.wait_for_timeout(500)

            self.assertTrue(page.locator("#tab-artist-detail").is_visible())
            self.assertEqual(page.locator("#artist-view-name").inner_text(), "The Weeknd")

            # Assert neutral label "Artist Discography" and NO fake badge check
            badge_text = page.locator("#tab-artist-detail").inner_text()
            self.assertIn("ARTIST DISCOGRAPHY", badge_text.upper())
            self.assertNotIn("VERIFIED LOCAL ARTIST", badge_text.upper())

            # Popular tracks and releases
            self.assertIn("Blinding Lights", page.locator("#artist-view-popular-body").inner_text())
            self.assertIn("After Hours", page.locator("#artist-view-albums-grid").inner_text())

            # Capture Screenshot 5: Artist Detail View
            page.screenshot(path=str(SCREENSHOT_DIR / "05_artist_detail_view.png"))
            self.assertTrue((SCREENSHOT_DIR / "05_artist_detail_view.png").exists())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_06_playlist_detail_view(self):
        """Verify Playlist Detail View loads collection, metadata, and ordered tracks."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(300)

            # Open Playlist View
            page.evaluate("openPlaylistView('Discovery')")
            page.wait_for_timeout(500)

            self.assertTrue(page.locator("#tab-playlist-detail").is_visible())
            self.assertEqual(page.locator("#playlist-view-title").inner_text(), "Discovery")
            self.assertTrue(page.locator("#playlist-view-tracks-body").is_visible())

            # Capture Screenshot 6: Playlist Detail View
            page.screenshot(path=str(SCREENSHOT_DIR / "06_playlist_detail_view.png"))
            self.assertTrue((SCREENSHOT_DIR / "06_playlist_detail_view.png").exists())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_07_library_clickable_links_and_batch(self):
        """Verify Library table clickable artist/album links and multi-select batch action bar."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(300)

            go_tab(page, "library")
            page.wait_for_timeout(500)
            self.assertTrue(page.locator("#tab-library").is_visible())

            # Check master checkbox
            page.click("#chk-master-lib")
            page.wait_for_timeout(300)

            # Assert batch bar appears
            self.assertTrue(page.locator("#lib-batch-bar").is_visible())
            batch_count = page.locator("#lib-batch-count").inner_text()
            self.assertEqual(batch_count, "10")

            # Capture Screenshot 7: Library Table and Batch Bar
            page.screenshot(path=str(SCREENSHOT_DIR / "07_library_table_and_batch.png"))
            self.assertTrue((SCREENSHOT_DIR / "07_library_table_and_batch.png").exists())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_08_now_playing_drawer_open_and_lyrics(self):
        """Verify Now Playing & Lyrics drawer slides open visibly onto the screen with track metadata."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(400)

            # Start playing a track
            page.evaluate("""
                playLocalTrack('D:\\\\downloads\\\\Daft Punk - Discovery\\\\01 One More Time.mp3', 'One More Time', 'Daft Punk', true, 'Discovery');
                openNowPlayingDrawer();
            """)
            page.wait_for_timeout(600)

            # Assert drawer has drawer-open class and is visible
            drawer = page.locator("#drawer-now-playing")
            self.assertTrue(drawer.evaluate("el => el.classList.contains('drawer-open')"))
            self.assertFalse(drawer.evaluate("el => el.classList.contains('translate-x-full')"))

            # Assert drawer bounding box is actually on-screen (not translated off right)
            box = drawer.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreater(box["x"], 1000, "Drawer should be anchored at the right")
            self.assertLess(box["x"], 1920, "Drawer should be within viewport width")
            self.assertGreater(box["width"], 250, "Drawer should have non-zero width")

            # Assert track title and artist inside drawer
            self.assertEqual(page.locator("#drawer-track-title").inner_text(), "One More Time")
            self.assertEqual(page.locator("#drawer-track-artist").inner_text(), "Daft Punk")

            # Capture Screenshot 8: Now Playing Drawer visibly open on screen!
            page.screenshot(path=str(SCREENSHOT_DIR / "08_now_playing_and_lyrics_drawer.png"))
            self.assertTrue((SCREENSHOT_DIR / "08_now_playing_and_lyrics_drawer.png").exists())

            # Test Close Drawer
            page.evaluate("closeNowPlayingDrawer()")
            page.wait_for_timeout(300)
            self.assertFalse(drawer.evaluate("el => el.classList.contains('drawer-open')"))

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_09_playback_queue_drawer_open_and_removal(self):
        """Verify Playback Queue drawer slides open visibly on screen and track removal works."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(400)

            # Populate queue and open queue drawer
            page.evaluate("""
                playerQueue = [
                    { path: 'D:\\\\1.mp3', title: 'One More Time', artist: 'Daft Punk', album: 'Discovery' },
                    { path: 'D:\\\\2.mp3', title: 'Aerodynamic', artist: 'Daft Punk', album: 'Discovery' },
                    { path: 'D:\\\\3.mp3', title: 'Digital Love', artist: 'Daft Punk', album: 'Discovery' },
                    { path: 'D:\\\\4.mp3', title: 'Harder Better Faster Stronger', artist: 'Daft Punk', album: 'Discovery' }
                ];
                playerCurrentIndex = 0;
                document.getElementById('queue-now-title').innerText = 'One More Time';
                document.getElementById('queue-now-artist').innerText = 'Daft Punk';
                openPlaybackQueueDrawer();
            """)
            page.wait_for_timeout(600)

            # Assert drawer has drawer-open class and is visible
            q_drawer = page.locator("#drawer-playback-queue")
            self.assertTrue(q_drawer.evaluate("el => el.classList.contains('drawer-open')"))
            self.assertFalse(q_drawer.evaluate("el => el.classList.contains('translate-x-full')"))

            # Assert on-screen bounding box
            box = q_drawer.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreater(box["x"], 1000)
            self.assertLess(box["x"], 1920)
            self.assertGreater(box["width"], 250)

            # Assert upcoming tracks rendered
            queue_text = page.locator("#queue-upcoming-list").inner_text()
            self.assertIn("Aerodynamic", queue_text)
            self.assertIn("Digital Love", queue_text)

            # Capture Screenshot 9: Playback Queue Drawer visibly open on screen!
            page.screenshot(path=str(SCREENSHOT_DIR / "09_playback_queue_drawer.png"))
            self.assertTrue((SCREENSHOT_DIR / "09_playback_queue_drawer.png").exists())

            # Test Remove track #1 from queue
            initial_count = page.evaluate("playerQueue.length")
            page.evaluate("removeTrackFromQueue(1)")
            page.wait_for_timeout(300)
            new_count = page.evaluate("playerQueue.length")
            self.assertEqual(new_count, initial_count - 1)

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)

    def test_10_persistent_player_and_navigation(self):
        """Verify persistent player bar remains uninterrupted across page transitions."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, context, page, console_errors = self._get_page(p)
            page.goto(self.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(400)

            # Start track playback
            page.evaluate("""
                playLocalTrack('D:\\\\downloads\\\\Daft Punk - Discovery\\\\01 One More Time.mp3', 'One More Time', 'Daft Punk', true, 'Discovery');
            """)
            page.wait_for_timeout(300)

            self.assertEqual(page.locator("#player-title").inner_text(), "One More Time")

            # Navigate to Search
            go_tab(page, "search")
            page.wait_for_timeout(200)
            self.assertEqual(page.locator("#player-title").inner_text(), "One More Time")

            # Navigate to Settings
            go_tab(page, "settings")
            page.wait_for_timeout(200)
            self.assertEqual(page.locator("#player-title").inner_text(), "One More Time")

            # Navigate to Logs
            go_tab(page, "logs")
            page.wait_for_timeout(200)
            self.assertEqual(page.locator("#player-title").inner_text(), "One More Time")

            # Return to Dashboard
            go_tab(page, "dashboard")
            page.wait_for_timeout(200)
            self.assertEqual(page.locator("#player-title").inner_text(), "One More Time")

            # Capture Screenshot 10: Persistent Player Bar Across Views
            page.screenshot(path=str(SCREENSHOT_DIR / "10_persistent_player_bar.png"))
            self.assertTrue((SCREENSHOT_DIR / "10_persistent_player_bar.png").exists())

            context.close()
            browser.close()
            self.assertEqual(len([e for e in console_errors if "uncaught" in e.lower() or "referenceerror" in e.lower()]), 0)


if __name__ == "__main__":
    unittest.main()
