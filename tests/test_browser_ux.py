"""
Real-browser regression tests for the UX overhaul: navigation model, themes, modal focus handling,
keyboard/screen-reader behaviour, settings dirty-state, undo flows and the bugs found in the review.
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401
try:
    from _nav import go_tab  # noqa: F401
except ImportError:
    from tests._nav import go_tab  # noqa: F401
import json
from pathlib import Path
import socket
import threading
import time
import unittest

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from src.security import SESSION_AUTH_TOKEN
from src.web.server import app


class TestBrowserUX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cls.port = s.getsockname()[1]
        s.close()
        cls.server = make_server("127.0.0.1", cls.port, app, threaded=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception:
            cls.pw.stop()
            cls.server.shutdown()
            raise
        app.test_client().post("/api/system/complete-setup", json={"downloads_path": "downloads"})
        from src.db import get_db_connection
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM library_files")
            for i, (t, a, al) in enumerate([("One More Time", "Daft Punk", "Discovery"), ("Aerodynamic", "Daft Punk", "Discovery"), ("Airbag", "Radiohead", "OK Computer")]):
                conn.execute("""INSERT OR REPLACE INTO library_files (path, filename, title, artist, album, duration_sec, format, bitrate_kbps, year, genre, has_artwork)
                                VALUES (?,?,?,?,?,?,?,?,?,?,1)""", (f"D:\\music\\{i}.mp3", f"{i}.mp3", t, a, al, 200 + i, "mp3", 320, 2001 + i, "Pop"))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()

    def open_page(self, theme="dark", size=(1360, 860), path="/"):
        ctx = self.browser.new_context(viewport={"width": size[0], "height": size[1]})
        if theme:
            ctx.add_init_script(f"try{{if(!localStorage.getItem('studio.theme.seeded')){{localStorage.setItem('studio.theme','{theme}');localStorage.setItem('studio.theme.seeded','1')}}}}catch(e){{}}")
        page = ctx.new_page()
        self.errors = []
        page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{self.port}{path}?token={SESSION_AUTH_TOKEN}" if path == "/" else
                  f"http://127.0.0.1:{self.port}/?token={SESSION_AUTH_TOKEN}", wait_until="networkidle")
        if path != "/":
            page.evaluate(f"location.hash = '{path}'")
        page.wait_for_function("() => window.lucide && document.querySelectorAll('svg.lucide').length > 10")
        page.evaluate("document.getElementById('wizard-modal') && document.getElementById('wizard-modal').classList.add('hidden')")
        self.addCleanup(ctx.close)
        return page

    def real_errors(self):
        return [e for e in self.errors if not e.startswith("Failed to load resource")]

    # ------------------------------------------------------------------ navigation model
    def test_five_destinations_and_sub_navigation(self):
        page = self.open_page()
        self.assertEqual(page.locator("#app-sidebar nav [data-hub]").count(), 5)
        self.assertFalse(page.locator("#subnav-wrap").is_visible())          # Home has no sections
        page.click("#nav-hub-library")
        tabs = page.locator("#subnav [role=tab]")
        self.assertEqual([t.strip() for t in tabs.all_inner_texts()], ["Tracks", "Discover", "Health", "Quality", "Releases", "Cleanup"])
        self.assertEqual(page.locator("#tab-btn-library").get_attribute("aria-selected"), "true")
        self.assertEqual(self.real_errors(), [])

    def test_sub_tabs_support_arrow_key_navigation(self):
        page = self.open_page()
        go_tab(page, "queue")
        page.focus("#tab-btn-queue")
        page.keyboard.press("ArrowRight")
        page.wait_for_selector("#tab-history:not(.hidden)")
        page.keyboard.press("ArrowRight")
        page.wait_for_selector("#tab-matching:not(.hidden)")
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowLeft")
        page.wait_for_selector("#tab-queue:not(.hidden)")

    def test_url_hash_restores_the_section_after_reload(self):
        page = self.open_page()
        go_tab(page, "settings")
        self.assertTrue(page.url.endswith("#/settings"))
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#tab-settings:not(.hidden)")
        self.assertEqual(page.locator("#nav-hub-settings").get_attribute("aria-current"), "page")

    def test_sidebar_collapses_and_can_be_expanded_again(self):
        page = self.open_page(size=(1360, 860))
        page.click("#btn-sidebar-toggle")
        self.assertTrue(page.evaluate("document.body.classList.contains('sidebar-collapsed')"))
        self.assertTrue(page.locator("#btn-sidebar-toggle").is_visible())     # regression: toggle used to vanish when collapsed
        page.click("#btn-sidebar-toggle")
        self.assertFalse(page.evaluate("document.body.classList.contains('sidebar-collapsed')"))

    def test_narrow_window_auto_collapses_the_sidebar(self):
        page = self.open_page(size=(1000, 700))
        self.assertTrue(page.evaluate("document.body.classList.contains('sidebar-collapsed')"))

    # ------------------------------------------------------------------ theming
    def test_theme_switch_applies_instantly_and_persists(self):
        page = self.open_page(theme="dark")
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), "dark")
        page.click("#btn-theme-toggle")
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), "light")
        bg = page.evaluate("getComputedStyle(document.getElementById('main-content')).backgroundColor")
        self.assertNotEqual(bg, "rgb(10, 10, 10)")
        page.reload(wait_until="networkidle")
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), "light")   # no flash: set before first paint

    def test_secondary_text_meets_aa_contrast_in_both_themes(self):
        for theme in ("dark", "light"):
            page = self.open_page(theme=theme)
            ratio = page.evaluate("""() => {
                const lum = (c) => { const [r,g,b] = c.match(/\\d+/g).slice(0,3).map(Number).map(v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4); }); return 0.2126*r+0.7152*g+0.0722*b; };
                const probe = document.createElement('span'); probe.className = 'text-fg4'; probe.textContent = 'x';
                document.querySelector('#tab-dashboard .bg-s1').appendChild(probe);
                const fg = lum(getComputedStyle(probe).color);
                const bg = lum(getComputedStyle(document.querySelector('#tab-dashboard .bg-s1')).backgroundColor);
                probe.remove();
                const [a,b] = fg > bg ? [fg,bg] : [bg,fg];
                return (a + 0.05) / (b + 0.05);
            }""")
            self.assertGreaterEqual(ratio, 4.5, f"tertiary text contrast in {theme} theme: {ratio:.2f}")

    def test_minimum_font_size_is_twelve_pixels(self):
        page = self.open_page()
        for tab in ("dashboard", "queue", "library", "settings"):
            go_tab(page, tab)
            smallest = page.evaluate("""() => {
                let min = 99;
                document.querySelectorAll('#content-scroll *').forEach(el => {
                    if (!el.offsetParent || !el.childNodes.length) return;
                    const hasText = Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim());
                    if (hasText) min = Math.min(min, parseFloat(getComputedStyle(el).fontSize));
                });
                return min;
            }""")
            self.assertGreaterEqual(smallest, 12, f"{tab}: text smaller than 12px")

    # ------------------------------------------------------------------ dialogs & focus
    def test_modal_traps_focus_marks_background_inert_and_restores_focus(self):
        page = self.open_page()
        page.focus("#auth-pill")
        page.click("#auth-pill")
        page.wait_for_selector("#login-modal:not(.hidden)")
        self.assertEqual(page.get_attribute("#main-content", "inert"), "")
        self.assertEqual(page.get_attribute("#login-modal", "role"), "dialog")
        self.assertEqual(page.get_attribute("#login-modal", "aria-modal"), "true")
        self.assertTrue(page.evaluate("document.getElementById('login-modal').contains(document.activeElement)"))
        page.keyboard.press("Escape")
        page.wait_for_selector("#login-modal", state="hidden")
        self.assertIsNone(page.get_attribute("#main-content", "inert"))
        self.assertEqual(page.evaluate("document.activeElement.id"), "auth-pill")

    def test_escape_closes_every_kind_of_modal(self):
        page = self.open_page()
        for modal_id, opener in (("modal-alternatives", "openAlternativesModal({title:'t',provider:'spotify',provider_item_id:'x',primary_creator:'a'})"),
                                 ("modal-youtube-embed", "openYouTubePlayerModal({title:'t',provider_item_id:'abc',primary_creator:'a',capabilities:{}})"),
                                 ("tag-modal", "openTagModal('x','t','a','b')")):
            page.evaluate(f"() => {{ {opener}; }}")
            page.wait_for_selector(f"#{modal_id}:not(.hidden)")
            page.keyboard.press("Escape")
            page.wait_for_selector(f"#{modal_id}", state="hidden")
        self.assertEqual(page.get_attribute("#yt-player-iframe", "src") or "", "")        # video stopped, not just hidden

    def test_confirm_dialog_resolves_and_defaults_to_the_safe_choice(self):
        page = self.open_page()
        page.evaluate("() => { window.__r = null; UI.confirm({title:'Delete?', message:'m', tone:'danger', confirmText:'Delete'}).then(v => { window.__r = v; }); }")
        page.wait_for_selector("#ui-dialog:not(.hidden)")
        self.assertEqual(page.get_attribute("#main-content", "inert"), "")        # runtime-built dialogs also isolate the page
        self.assertEqual(page.evaluate("document.activeElement.id"), "ui-dialog-cancel")   # Enter can't delete by accident
        page.keyboard.press("Enter")
        page.wait_for_function("() => window.__r !== null")
        self.assertFalse(page.evaluate("window.__r"))
        page.evaluate("() => { window.__r = null; UI.confirm({title:'Go?', confirmText:'Go'}).then(v => { window.__r = v; }); }")
        page.click("#ui-dialog-ok")
        page.wait_for_function("() => window.__r !== null")
        self.assertTrue(page.evaluate("window.__r"))

    # ------------------------------------------------------------------ keyboard & screen readers
    def test_space_on_a_focused_button_does_not_hijack_into_playback(self):
        page = self.open_page()
        page.evaluate("() => { window.__played = 0; window.toggleAudioPlayback = () => { window.__played++; }; }")
        page.focus("#btn-theme-toggle")
        page.keyboard.press("Space")                       # activates the button, as users expect
        self.assertEqual(page.evaluate("window.__played"), 0)
        page.evaluate("document.activeElement.blur()")
        page.keyboard.press("Space")                       # nothing focused: global play/pause applies
        self.assertEqual(page.evaluate("window.__played"), 1)

    def test_clickable_cards_are_keyboard_operable_and_named(self):
        page = self.open_page()
        page.evaluate("""() => { const d = document.createElement('div'); d.id='probe'; d.setAttribute('data-on-click','openWatcherTab');
                                  d.textContent = 'go'; document.getElementById('tab-dashboard').appendChild(d); }""")
        page.wait_for_function("() => document.getElementById('probe').getAttribute('role') === 'button'")
        self.assertEqual(page.get_attribute("#probe", "tabindex"), "0")
        page.focus("#probe")
        page.keyboard.press("Enter")
        page.wait_for_selector("#tab-watcher:not(.hidden)")

    def test_icon_only_buttons_get_accessible_names(self):
        page = self.open_page()
        unnamed = page.evaluate("""() => Array.from(document.querySelectorAll('button')).filter(b =>
            !b.textContent.trim() && !b.getAttribute('aria-label') && !b.getAttribute('title') && !b.closest('.hidden')).map(b => b.id || b.outerHTML.slice(0, 80))""")
        self.assertEqual(unnamed, [])

    def test_reduced_motion_disables_animation(self):
        ctx = self.browser.new_context(reduced_motion="reduce")
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/?token={SESSION_AUTH_TOKEN}", wait_until="networkidle")
        self.addCleanup(ctx.close)
        dur = page.evaluate("getComputedStyle(document.querySelector('.animate-spin') || document.body).animationName")
        self.assertIn(dur, ("none", ""))

    # ------------------------------------------------------------------ search
    def test_header_search_filters_the_library_and_opens_discover_elsewhere(self):
        page = self.open_page()
        go_tab(page, "library")
        self.assertEqual(page.get_attribute("#global-search-input", "placeholder"), "Filter library…")
        page.fill("#global-search-input", "Airbag")
        page.wait_for_function("() => document.querySelectorAll('#lib-table-body tr').length === 1")
        go_tab(page, "dashboard")
        self.assertEqual(page.input_value("#global-search-input"), "")            # filter doesn't linger invisibly
        page.fill("#global-search-input", "Daft Punk")
        page.keyboard.press("Enter")
        page.wait_for_selector("#tab-search:not(.hidden)")
        self.assertEqual(page.input_value("#omni-search-input"), "Daft Punk")

    # ------------------------------------------------------------------ settings
    def test_settings_dirty_bar_tracks_changes_and_saves_only_what_changed(self):
        page = self.open_page()
        go_tab(page, "settings")
        page.wait_for_function("() => document.getElementById('cfg-format').value !== ''")
        self.assertIn("is-clean", page.get_attribute("#settings-savebar", "class"))
        page.select_option("#cfg-format", "flac")
        self.assertNotIn("is-clean", page.get_attribute("#settings-savebar", "class"))
        self.assertTrue(page.locator("#row-mp3-bitrate").evaluate("e => e.classList.contains('hidden')"))   # bitrate only matters for MP3
        with page.expect_request(lambda r: r.url.endswith("/api/settings") and r.method == "POST") as req:
            page.click("#btn-settings-save")
        self.assertEqual(json.loads(req.value.post_data), {"audio": {"output_format": "flac"}})
        page.wait_for_function("() => document.getElementById('settings-savebar').classList.contains('is-clean')")
        page.select_option("#cfg-format", "mp3")
        page.click("#settings-savebar >> text=Discard")
        page.wait_for_function("() => document.getElementById('cfg-format').value === 'flac'")   # reverts to the saved value
        self.assertEqual(page.input_value("#cfg-format"), "flac")
        # restore the default for the other tests
        page.select_option("#cfg-format", "mp3")
        page.click("#btn-settings-save")
        page.wait_for_function("() => document.getElementById('settings-savebar').classList.contains('is-clean')")

    def test_min_delay_may_be_zero_but_not_exceed_max(self):
        page = self.open_page()
        go_tab(page, "settings")
        page.wait_for_function("() => document.getElementById('cfg-min-delay').value !== ''")
        page.fill("#cfg-min-delay", "30")
        page.fill("#cfg-max-delay", "5")
        self.assertTrue(page.is_visible("#cfg-delay-error"))
        page.click("#btn-settings-save")
        self.assertTrue(page.locator("#toast-container .toast-error").count() >= 1)
        page.click("#settings-savebar >> text=Discard")

    def test_storage_facts_come_from_the_server(self):
        page = self.open_page()
        go_tab(page, "settings")
        page.wait_for_function("() => document.getElementById('storage-root').textContent.length > 3")
        self.assertIn("Writable", page.inner_text("#storage-write"))
        self.assertNotIn("0 bytes", page.inner_text("#storage-health"))

    # ------------------------------------------------------------------ bugs from the review
    def test_wizard_completion_reports_success_not_a_javascript_error(self):
        page = self.open_page()
        page.evaluate("() => { openWizardModal(); }")
        page.wait_for_selector("#wizard-modal:not(.hidden)")
        self.assertFalse(page.is_visible("#btn-wizard-finish"))                  # finish only appears on the last step
        page.click("#btn-wizard-next")
        page.click("#btn-wizard-next")
        page.click("#btn-wizard-finish")
        page.wait_for_selector("#wizard-modal", state="hidden")
        page.wait_for_selector("#toast-container .toast-success")
        self.assertEqual(page.locator("#toast-container .toast-error").count(), 0)
        self.assertEqual(self.real_errors(), [])

    def test_tag_editor_never_carries_values_over_between_files(self):
        page = self.open_page()
        page.evaluate("() => { openTagModal('D%3A%5Cmusic%5C0.mp3','One','A','B'); }")
        page.wait_for_selector("#tag-modal:not(.hidden)")
        page.fill("#edit-tag-year", "1999")
        page.fill("#edit-tag-genre", "Polka")
        page.keyboard.press("Escape")
        page.evaluate("() => { openTagModal('D%3A%5Cmusic%5C2.mp3','Airbag','Radiohead','OK Computer'); }")
        page.wait_for_selector("#tag-modal:not(.hidden)")
        self.assertNotEqual(page.input_value("#edit-tag-year"), "1999")
        self.assertNotEqual(page.input_value("#edit-tag-genre"), "Polka")

    def test_tag_editor_sends_only_changed_fields(self):
        page = self.open_page()
        page.evaluate("() => { openTagModal('D%3A%5Cmusic%5C0.mp3','One More Time','Daft Punk','Discovery'); }")
        page.wait_for_selector("#tag-modal:not(.hidden)")
        page.wait_for_timeout(300)
        page.fill("#edit-tag-album", "Discovery (Remastered)")
        with page.expect_request(lambda r: r.url.endswith("/api/tags/edit")) as req:
            page.click("text=Save Tags to Audio File")
        body = json.loads(req.value.post_data)
        self.assertEqual(body["metadata"], {"album": "Discovery (Remastered)"})   # title/artist untouched, never blanked

    def test_artist_monitor_download_button_fills_the_add_tab(self):
        page = self.open_page()
        page.evaluate("() => { fetchLinkDirectly(encodeURIComponent('https://open.spotify.com/album/abc')); }")
        page.wait_for_selector("#tab-fetch:not(.hidden)")
        self.assertEqual(page.input_value("#multiline-input"), "https://open.spotify.com/album/abc")
        self.assertEqual(self.real_errors(), [])

    def test_collection_names_with_markup_do_not_break_the_queue_filter(self):
        page = self.open_page()
        go_tab(page, "queue")
        page.evaluate("""() => { cachedQueueJobs = [{id:'j1', track_id:'t', title:'T', artists:['A'], album:'', collection_name:'<img src=x onerror=alert(1)>', stage:'QUEUED', status:'QUEUED', progress:0}];
                                  updateQueueCollectionsDropdown(cachedQueueJobs); }""")
        opts = page.locator("#q-select-collection option").all_inner_texts()
        self.assertTrue(any("<img src=x" in o for o in opts))                    # shown as text...
        self.assertEqual(page.locator("#q-select-collection img").count(), 0)    # ...never parsed as markup

    def test_queue_removal_can_be_undone(self):
        page = self.open_page()
        added = page.evaluate("""async () => (await (await fetch('/api/queue/add', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({tracks:[{id:'undo_t1', title:'Undo Me', artists:['X'], album:'Y', duration_ms:1000, cover_url:'', isrc:''}], collection_name:'UndoSet'})})).json()).added""")
        self.assertEqual(added, 1)
        page.evaluate("() => { fetch('/api/queue/pause', {method:'POST'}); }")
        go_tab(page, "queue")
        page.wait_for_selector("#master-queue-body tr[data-job-id] >> text=Undo Me")
        page.click("#master-queue-body tr:has-text('Undo Me') button[aria-label^='Remove']")
        page.wait_for_selector("#toast-container .toast-action")
        self.assertEqual(page.locator("#master-queue-body tr:has-text('Undo Me')").count(), 0)
        page.click("#toast-container .toast-action")
        page.wait_for_selector("#master-queue-body tr:has-text('Undo Me')")

    def test_queue_polling_reuses_rows_instead_of_rebuilding_the_table(self):
        page = self.open_page()
        go_tab(page, "queue")
        page.evaluate("""() => { fetch('/api/queue/add', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({tracks:[{id:'keep_t1', title:'Keep Row', artists:['X'], album:'Y', duration_ms:1000, cover_url:'', isrc:''}]})}); }""")
        page.wait_for_selector("#master-queue-body tr:has-text('Keep Row')")
        page.evaluate("() => { window.__row = Array.from(document.querySelectorAll('#master-queue-body tr')).find(r => r.textContent.includes('Keep Row')); }")
        page.evaluate("() => { applyQueueFiltersAndRender(); applyQueueFiltersAndRender(); }")
        same = page.evaluate("Array.from(document.querySelectorAll('#master-queue-body tr')).some(r => r === window.__row)")
        self.assertTrue(same, "an unchanged row must keep its DOM node (hover/focus/selection survive polling)")

    def test_queue_rows_can_be_reordered_by_dragging(self):
        page = self.open_page()
        page.evaluate("() => { fetch('/api/queue/pause', {method:'POST'}); }")
        ids = ["dnd_a", "dnd_b", "dnd_c"]
        page.evaluate("""async (ids) => { await fetch('/api/queue/add', {method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({tracks: ids.map(i => ({id:i, title:'Drag '+i, artists:['X'], album:'Y', duration_ms:1000, cover_url:'', isrc:''}))})}); }""", ids)
        go_tab(page, "queue")
        page.wait_for_selector("#master-queue-body tr:has-text('Drag dnd_c')")
        self.assertEqual(page.locator("#master-queue-body tr:has-text('Drag dnd_a')").get_attribute("draggable"), "true")
        page.locator("#master-queue-body tr:has-text('Drag dnd_c')").drag_to(page.locator("#master-queue-body tr:has-text('Drag dnd_a')"), target_position={"x": 40, "y": 4})
        page.wait_for_function("() => { const t = Array.from(document.querySelectorAll('#master-queue-body tr')).map(r => r.textContent); const i = t.findIndex(x => x.includes('Drag dnd_c')); const j = t.findIndex(x => x.includes('Drag dnd_a')); return i >= 0 && i < j; }")
        # a filtered view is not the queue order, so dragging is switched off
        page.fill("#q-search-input", "Drag")
        page.dispatch_event("#q-search-input", "input")
        page.wait_for_function("() => { const r = Array.from(document.querySelectorAll('#master-queue-body tr')).find(x => x.textContent.includes('Drag dnd_a')); return r && !r.draggable; }")

    def test_library_column_chooser_persists_and_changes_the_table(self):
        page = self.open_page()
        go_tab(page, "library")
        page.wait_for_selector("#lib-head-row th[data-lib-sort=title]")
        self.assertEqual(page.locator("#lib-head-row th[data-lib-sort=year]").count(), 0)
        page.click("#btn-lib-columns")
        self.assertEqual(page.get_attribute("#btn-lib-columns", "aria-expanded"), "true")
        page.check("#lib-columns-menu input[data-args-change*=year]")
        page.wait_for_selector("#lib-head-row th[data-lib-sort=year]")
        page.wait_for_function("() => document.getElementById('lib-table-body').textContent.includes('2001')")
        page.keyboard.press("Escape")
        self.assertTrue(page.evaluate("document.getElementById('lib-columns-menu').classList.contains('hidden')"))
        page.reload(wait_until="networkidle")
        go_tab(page, "library")
        page.wait_for_selector("#lib-head-row th[data-lib-sort=year]")          # remembered across restarts
        page.click("#btn-lib-columns")
        page.click("text=Reset to default")
        self.assertEqual(page.locator("#lib-head-row th[data-lib-sort=year]").count(), 0)

    def test_saved_views_round_trip(self):
        page = self.open_page()
        go_tab(page, "library")
        page.wait_for_function("() => document.querySelectorAll('#lib-table-body tr').length >= 3")
        page.fill("#lib-search-input", "Daft")
        page.wait_for_function("() => document.querySelectorAll('#lib-table-body tr').length === 2")
        page.click("th[data-lib-sort=title]")
        page.click("#btn-lib-save-view")
        page.fill("#ui-dialog-input", "Daft by title")
        page.click("#ui-dialog-ok")
        page.wait_for_function("() => document.getElementById('lib-views-select').value === 'Daft by title'")
        page.fill("#lib-search-input", "")
        page.wait_for_function("() => document.querySelectorAll('#lib-table-body tr').length === 3")
        self.assertEqual(page.input_value("#lib-views-select"), "")             # editing leaves the saved view
        page.select_option("#lib-views-select", "Daft by title")
        page.wait_for_function("() => document.querySelectorAll('#lib-table-body tr').length === 2")
        self.assertEqual(page.input_value("#lib-search-input"), "Daft")
        self.assertEqual(page.get_attribute("th[data-lib-sort=title]", "aria-sort"), "ascending")
        page.click("#btn-lib-delete-view")
        page.click("#ui-dialog-ok")
        page.wait_for_function("() => document.getElementById('lib-views-select').options.length === 1")

    # ------------------------------------------------------------------ visual effects layer
    def test_command_palette_navigates_runs_actions_and_finds_tracks(self):
        page = self.open_page()
        page.keyboard.press("Control+k")
        page.wait_for_selector("#cmdk:not(.hidden)")
        self.assertEqual(page.get_attribute("#cmdk-input", "role"), "combobox")
        self.assertEqual(page.get_attribute("#cmdk", "role"), "dialog")
        self.assertEqual(page.evaluate("document.activeElement.id"), "cmdk-input")
        self.assertEqual(page.get_attribute("#main-content", "inert"), "")        # background is inert while it is open
        page.keyboard.type("diagnostics")
        page.wait_for_selector("#cmdk-list [role=option]:has-text('Diagnostics')")
        self.assertEqual(page.locator("#cmdk-list [role=option][aria-selected=true]").count(), 1)
        page.keyboard.press("Enter")
        page.wait_for_selector("#tab-logs:not(.hidden)")
        self.assertTrue(page.evaluate("document.getElementById('cmdk').classList.contains('hidden')"))
        # live track search + play
        page.keyboard.press("Control+k")
        page.keyboard.type("airbag")
        page.wait_for_selector("#cmdk-list [role=option]:has-text('Airbag')")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.getElementById('player-title').innerText === 'Airbag'")
        # arrows move the selection, Esc closes and gives focus back
        page.keyboard.press("Control+k")
        first = page.get_attribute("#cmdk-input", "aria-activedescendant")
        page.keyboard.press("ArrowDown")
        self.assertNotEqual(page.get_attribute("#cmdk-input", "aria-activedescendant"), first)
        page.keyboard.press("Escape")
        page.wait_for_selector("#cmdk", state="hidden")
        self.assertIsNone(page.get_attribute("#main-content", "inert"))

    def test_slash_focuses_the_header_search(self):
        page = self.open_page()
        page.keyboard.press("/")
        self.assertEqual(page.evaluate("document.activeElement.id"), "global-search-input")

    def test_wizard_stepper_moves_forward_and_back_with_progress(self):
        page = self.open_page()
        page.evaluate("() => { openWizardModal(); }")
        page.wait_for_selector("#wizard-modal:not(.hidden)")
        self.assertIn("Step 1 of 3", page.text_content("#wizard-step-label"))
        self.assertFalse(page.is_visible("#btn-wizard-back"))
        page.click("#btn-wizard-next")
        self.assertIn("Step 2 of 3", page.text_content("#wizard-step-label"))
        self.assertEqual(page.get_attribute(".wiz-dot[data-dot='2']", "aria-current"), "step")
        self.assertTrue(page.locator(".wiz-dot[data-dot='1']").evaluate("e => e.classList.contains('is-done')"))
        page.click("#btn-wizard-next")
        self.assertTrue(page.is_visible("#wizard-downloads-path"))
        self.assertFalse(page.is_visible("#btn-wizard-next"))
        page.click("#btn-wizard-back")
        self.assertIn("Step 2 of 3", page.text_content("#wizard-step-label"))

    def test_settings_switches_stay_real_keyboard_operable_checkboxes(self):
        page = self.open_page()
        go_tab(page, "settings")
        page.wait_for_function("() => document.getElementById('cfg-format').value !== ''")
        self.assertEqual(page.evaluate("getComputedStyle(document.getElementById('cfg-normalize')).appearance"), "none")
        before = page.is_checked("#cfg-normalize")
        page.focus("#cfg-normalize")
        page.keyboard.press("Space")
        self.assertNotEqual(page.is_checked("#cfg-normalize"), before)
        self.assertNotIn("is-clean", page.get_attribute("#settings-savebar", "class"))
        page.click("#settings-savebar >> text=Discard")

    def test_equalizer_and_toast_countdown_follow_state(self):
        page = self.open_page()
        page.evaluate("() => { const a = document.getElementById('audio-element'); a.dispatchEvent(new Event('play')); }")
        self.assertTrue(page.evaluate("document.body.classList.contains('is-playing')"))
        page.evaluate("() => { document.getElementById('audio-element').dispatchEvent(new Event('pause')); }")
        self.assertFalse(page.evaluate("document.body.classList.contains('is-playing')"))
        page.evaluate("() => { UI.toast('hello', 'info'); UI.toast('stay', 'error'); }")
        self.assertEqual(page.locator("#toast-container .toast-info .toast-bar").count(), 1)   # auto-dismiss shows its countdown
        self.assertEqual(page.locator("#toast-container .toast-error .toast-bar").count(), 0)  # errors never auto-dismiss

    def test_library_shows_skeleton_rows_while_loading(self):
        page = self.open_page()
        page.route("**/api/library?**", lambda r: (page.wait_for_timeout(700), r.continue_()))
        page.evaluate("() => { document.getElementById('lib-table-body').innerHTML = ''; refreshLibrary(); }")
        self.assertTrue(page.evaluate("document.querySelectorAll('#lib-table-body .skeleton').length > 0"))
        page.wait_for_function("() => document.querySelectorAll('#lib-table-body .skeleton').length === 0")

    def test_diagnostics_shows_live_log_lines_from_the_server(self):
        from src.web.state import logger as studio_logger
        page = self.open_page()
        go_tab(page, "logs")
        marker = f"live-log-probe-{time.time_ns()}"
        page.wait_for_timeout(600)                                       # let the EventSource connect
        studio_logger.warning(marker)
        page.wait_for_function("(m) => document.getElementById('log-terminal').textContent.includes(m)", arg=marker, timeout=8000)
        self.assertIn("WARNING", page.inner_text("#log-terminal").upper())
        page.fill("#log-search-input", "no-such-text-xyz")
        page.dispatch_event("#log-search-input", "input")
        page.wait_for_function("() => !document.getElementById('log-terminal').textContent.includes('live-log-probe')")

    def test_rules_screen_create_preview_apply_and_undo(self):
        import shutil
        from src.config_loader import get_downloads_path
        from src.db import get_db_connection
        folder = get_downloads_path() / "ui_rules"
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / "ui_loose.mp3"
        f.write_bytes(b"ID3" + bytes(80))
        conn = get_db_connection()
        with conn:
            conn.execute("""INSERT OR REPLACE INTO library_files (path, filename, folder, title, artist, album, genre, format, duration_sec, bitrate_kbps)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""", (str(f.resolve()), f.name, str(folder), "UiRuleSong", "UiRuleArtist", "UiRuleAlbum", "Jazz", "mp3", 100, 320))
        self.addCleanup(lambda: shutil.rmtree(folder, ignore_errors=True))
        self.addCleanup(lambda: (conn.execute("DELETE FROM library_files WHERE title='UiRuleSong'"), conn.execute("DELETE FROM organization_rules WHERE name LIKE 'UI:%'"), conn.commit()))

        page = self.open_page()
        go_tab(page, "rules")
        page.wait_for_selector("#rules-list li")
        page.click("text=New rule")
        page.fill("#rule-name", "UI: jazz")
        page.select_option("#rule-cond-type", "genre_contains")
        page.fill("#rule-cond-value", "jazz")
        page.fill("#rule-action-value", "Sorted/{artist}/{title}.{ext}")
        page.click("#rule-form button[type=submit]")
        page.wait_for_selector("#rules-list li:has-text('UI: jazz')")
        # a bad pattern shows the server's explanation inline and keeps the form open
        page.click("text=New rule")
        page.fill("#rule-name", "UI: bad")
        page.fill("#rule-cond-value", "x")
        page.fill("#rule-action-value", "../escape/{title}.{ext}")
        page.click("#rule-form button[type=submit]")
        page.wait_for_selector("#rule-form-error:not(.hidden)")
        self.assertIn("relative", page.inner_text("#rule-form-error"))
        page.click("#rule-form >> text=Cancel")

        page.click("#btn-rules-preview")
        page.wait_for_selector("#rules-preview-body tr:has-text('ui_loose.mp3')")
        self.assertTrue(f.exists())                                                  # previewing never moves anything
        self.assertTrue(page.is_enabled("#btn-rules-apply"))
        page.click("#btn-rules-apply")
        page.wait_for_selector("#ui-dialog:not(.hidden)")
        page.click("#ui-dialog-ok")
        page.wait_for_function("() => !!document.querySelector('#rules-batches button')")
        self.assertFalse(f.exists())
        moved = folder.parent / "Sorted" / "UiRuleArtist" / "UiRuleSong.mp3"
        self.addCleanup(lambda: shutil.rmtree(folder.parent / "Sorted", ignore_errors=True))
        self.assertTrue(moved.exists())
        page.click("#rules-batches button:has-text('Undo')")
        page.click("#ui-dialog-ok")
        page.wait_for_function("() => document.querySelector('#rules-batches').textContent.includes('Undone')")
        self.assertTrue(f.exists())                                                  # one click put it back

    def test_hooks_can_be_added_and_removed_and_unsafe_targets_explain_themselves(self):
        page = self.open_page()
        go_tab(page, "rules")
        page.fill("#hook-name", "UI: ping")
        page.fill("#hook-target", "https://example.com/hook")
        page.click("#hook-form button[type=submit]")
        page.wait_for_selector("#hooks-list li:has-text('UI: ping')")
        page.fill("#hook-name", "UI: copy")
        page.select_option("#hook-action", "copy_to_folder")
        self.assertIn("Folder", page.inner_text("#hook-target-label"))
        page.fill("#hook-target", "C:\\Users\\Public\\Music")
        page.click("#hook-form button[type=submit]")
        page.wait_for_selector("#hook-form-error:not(.hidden)")
        self.assertIn("inside the application", page.inner_text("#hook-form-error"))
        page.click("#hooks-list li:has-text('UI: ping') button")
        page.click("#ui-dialog-ok")
        page.wait_for_function("() => !document.getElementById('hooks-list').textContent.includes('UI: ping')")

    def test_failure_messages_are_translated_into_plain_language_with_an_action(self):
        page = self.open_page()
        cases = {
            "Spotify direct audio stream retrieval is unsupported. Protected DRM full-track streams cannot be downloaded.": ("signin", "Not signed in"),
            "Spotify Audio Key Error: Full direct stream extraction requires a Spotify Premium account.": ("premium", "Premium"),
            "HTTP 429 Too Many Requests": ("rate", "slow down"),
            "Verification failure: bad checksum": ("verify", "check"),
            "ffmpeg exited with code 1": ("ffmpeg", "Converting"),
            "Failed to load stream from Spotify: boom": ("stream", "wouldn't provide"),
            "Connection timed out": ("network", "Network"),
        }
        for raw, (key, fragment) in cases.items():
            got = page.evaluate("(raw) => { const h = explainError(raw); return h ? [h.key, h.short, !!h.action] : null; }", raw)
            self.assertIsNotNone(got, raw)
            self.assertEqual(got[0], key, raw)
            self.assertIn(fragment.lower(), got[1].lower(), raw)
            self.assertTrue(got[2])
        self.assertIsNone(page.evaluate("explainError('something totally unexpected')"))      # unknown text is shown as-is

    def test_premium_failures_show_a_sign_in_banner_only_when_it_helps(self):
        page = self.open_page()
        go_tab(page, "queue")
        page.evaluate("""() => { cachedQueueJobs = [{id:'p1', track_id:'t1', title:'Locked', artists:['A'], album:'', collection_name:'C', stage:'FAILED', status:'FAILED', progress:0,
            error:'Spotify Audio Key Error: Full direct stream extraction requires a Spotify Premium account.'}];
            window.__auth = {authenticated:false, accountType:'unknown', username:'', credentialsExist:false}; applyQueueFiltersAndRender(); renderQueueAlerts(); }""")
        page.wait_for_selector("#queue-alert:not(.hidden)")
        self.assertIn("1 download can't start", page.inner_text("#queue-alert"))
        self.assertIn("Needs Spotify Premium", page.inner_text("#master-queue-body"))        # row says it plainly, raw text stays in the tooltip
        page.click("#queue-alert button:has-text(\"Sign in\")")
        page.wait_for_selector("#login-modal:not(.hidden)")
        page.keyboard.press("Escape")
        page.evaluate("() => { window.__auth = {authenticated:true, accountType:'free', username:'x'}; renderQueueAlerts(); }")
        self.assertIn("Free Spotify account", page.inner_text("#queue-alert"))
        page.evaluate("() => { window.__auth = {authenticated:true, accountType:'premium', username:'x'}; renderQueueAlerts(); }")
        self.assertTrue(page.evaluate("document.getElementById('queue-alert').classList.contains('hidden')"))   # Premium: nothing to fix here

    def test_job_details_explain_what_to_do_and_the_button_acts(self):
        page = self.open_page()
        go_tab(page, "queue")
        page.evaluate("""() => { cachedQueueJobs = [{id:'p2', track_id:'t2', title:'Slow', artists:['A'], album:'', collection_name:'C', stage:'FAILED', status:'FAILED', progress:0,
            error:'HTTP 429 Too Many Requests'}]; openQueueJobDetailsModal('p2'); }""")
        page.wait_for_selector("#modal-queue-job-details:not(.hidden)")
        self.assertIn("Too many requests", page.inner_text("#detail-job-help-title"))
        self.assertIn("429", page.inner_text("#detail-job-error-text"))                       # the raw text is still there for support
        page.click("#detail-job-help-action")
        page.wait_for_selector("#tab-settings:not(.hidden)")

    def test_player_remembers_volume_and_has_tri_state_repeat(self):
        page = self.open_page()
        page.evaluate("() => { setAudioVolume(0.4); }")
        page.evaluate("repeatMode = 'off'; toggleRepeat()")
        self.assertEqual(page.evaluate("repeatMode"), "all")
        page.evaluate("toggleRepeat()")
        self.assertEqual(page.evaluate("repeatMode"), "one")
        self.assertEqual(page.get_attribute("#btn-player-repeat", "aria-label"), "Repeat: current track")
        page.reload(wait_until="networkidle")
        self.assertAlmostEqual(page.evaluate("document.getElementById('audio-element').volume"), 0.4, places=2)

    def test_shuffle_plays_every_track_before_repeating(self):
        page = self.open_page()
        order = page.evaluate("""() => { playerQueue = [0,1,2,3,4,5].map(i => ({path:'p'+i})); playerCurrentIndex = 0; shuffleBag = [];
            const seen = []; for (let i = 0; i < 5; i++) { seen.push(nextShuffleIndex()); } return seen; }""")
        self.assertEqual(sorted(order), [1, 2, 3, 4, 5])                                # no repeats within a cycle

    def test_clipboard_and_link_hint(self):
        page = self.open_page()
        go_tab(page, "fetch")
        page.fill("#multiline-input", "https://open.spotify.com/track/abc\nsome search words")
        self.assertIn("1 Spotify link detected", page.inner_text("#fetch-link-hint"))

    def test_library_columns_sort_and_expose_aria_sort(self):
        page = self.open_page()
        go_tab(page, "library")
        page.wait_for_function("() => document.querySelectorAll('#lib-table-body tr').length >= 3")
        with page.expect_request(lambda r: "/api/library?" in r.url and "sort_by=title" in r.url and "sort_dir=asc" in r.url):
            page.click("th[data-lib-sort=title]")
        self.assertEqual(page.get_attribute("th[data-lib-sort=title]", "aria-sort"), "ascending")
        with page.expect_request(lambda r: "sort_dir=desc" in r.url):
            page.click("th[data-lib-sort=title]")
        self.assertEqual(page.get_attribute("th[data-lib-sort=title]", "aria-sort"), "descending")

    def test_the_interface_stays_quiet_no_decoration_no_loops_no_heavy_type(self):
        """The visual layer is restrained: no ambient background, no gradients or looping motion on idle screens,
        nothing heavier than weight 600, and no shouting in capitals (short codes such as MP3 are fine)."""
        page = self.open_page()
        self.assertEqual(page.locator("#fx-aurora, canvas").count(), 0)
        audit = """() => {
          const out = {heavy: [], looping: [], gradient: [], shouting: []};
          for (const el of document.querySelectorAll('body *')) {
            const r = el.getBoundingClientRect();
            if (!r.width || !r.height || el.closest('.hidden,[hidden]')) continue;
            const cs = getComputedStyle(el), tag = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '');
            if (el.childNodes.length && Array.from(el.childNodes).some(n => n.nodeType === 3 && n.textContent.trim()) && parseInt(cs.fontWeight) > 600) out.heavy.push(tag + '.' + el.className.toString().slice(0, 50));
            if (cs.animationName !== 'none' && cs.animationIterationCount.includes('infinite') && !el.closest('.skeleton,.animate-spin,svg')) out.looping.push(tag);
            if (cs.backgroundImage.includes('gradient') && !el.classList.contains('skeleton')) out.gradient.push(tag);
            const own = Array.from(el.childNodes).filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ');
            if (cs.textTransform === 'uppercase' && own.length > 4) out.shouting.push(tag + ':' + own.slice(0, 20));
          }
          return out;
        }"""
        for tab in ("dashboard", "library", "settings", "plugins"):
            go_tab(page, tab)
            page.wait_for_timeout(400)
            found = page.evaluate(audit)
            self.assertEqual(found, {"heavy": [], "looping": [], "gradient": [], "shouting": []}, f"{tab}: {found}")
        self.assertEqual(self.real_errors(), [])

    def test_toast_errors_persist_and_can_be_copied(self):
        page = self.open_page()
        page.evaluate("UI.toast('Something failed: details here', 'error')")
        page.wait_for_selector("#toast-container .toast-error")
        page.wait_for_timeout(5200)                                                    # longer than any auto-dismiss
        self.assertEqual(page.locator("#toast-container .toast-error").count(), 1)
        self.assertEqual(page.get_attribute("#toast-container .toast-error", "role"), "alert")
        self.assertEqual(page.locator("#toast-container .toast-copy").count(), 1)
        page.evaluate("UI.toast('Something failed: details here', 'error')")          # duplicates collapse into a counter
        self.assertEqual(page.locator("#toast-container .toast-error").count(), 1)
        self.assertIn("×2", page.inner_text("#toast-container .toast-count"))

    def test_connection_banner_appears_when_the_server_stops_answering(self):
        page = self.open_page()
        page.route("**/api/status**", lambda r: r.abort())
        page.evaluate("() => { fetchStatus(); UI.api('/api/status', {quiet:true}).catch(()=>{}); UI.api('/api/status', {quiet:true}).catch(()=>{}); }")
        page.wait_for_function("() => !document.getElementById('conn-banner').classList.contains('hidden')")

    # ------------------------------------------------------------------ audit batch: health, cleanup, library, search, backups, history
    def test_health_tiles_are_real_buttons_that_lead_to_where_you_fix_things(self):
        page = self.open_page()
        go_tab(page, "intelligence")
        for tile in ("drill-missing-tags", "drill-missing-art", "drill-unreadable", "drill-exact-dups", "drill-probable-dups", "drill-anomalies"):
            self.assertEqual(page.evaluate(f"document.getElementById('{tile}').closest('button') !== null"), True, tile)
        for stat in ("intel-total-tracks", "intel-total-artists", "intel-total-size", "intel-total-hours", "intel-health-score"):
            self.assertTrue(page.locator(f"#{stat}").count() == 1, stat)
        self.assertIn("Review in Cleanup", page.locator("#drill-exact-dups").locator("xpath=..").inner_text())
        self.assertFalse(page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth"))
        page.locator("#drill-exact-dups").click()
        page.wait_for_selector("#tab-optimizer:not(.hidden)")
        self.assertEqual(page.locator("#tab-btn-optimizer").get_attribute("aria-selected"), "true")
        self.assertEqual(self.real_errors(), [])

    def test_cleanup_has_one_duplicate_workflow_and_points_to_sound_matching(self):
        page = self.open_page()
        go_tab(page, "optimizer")
        self.assertEqual(page.locator("#tab-optimizer >> text=Scan Exact (SHA-256)").count(), 0)          # the second, duplicate set of scan buttons is gone
        self.assertEqual(page.locator("#tab-optimizer >> text=Rescan current mode").count(), 1)
        page.click("#tab-optimizer >> text=Compare by sound instead")
        page.wait_for_selector("#tab-quality_lab:not(.hidden)")
        self.assertEqual(self.real_errors(), [])

    def test_library_sheds_low_priority_columns_as_the_window_narrows(self):
        page = self.open_page(size=(1360, 860))
        go_tab(page, "library")
        page.wait_for_selector("#lib-table-body tr")
        head = lambda name: page.locator(f"#lib-head-row th:has-text('{name}')").first.is_visible()   # noqa: E731
        self.assertTrue(head("Album") and head("Format") and head("Identified issues"))
        page.set_viewport_size({"width": 1100, "height": 800})
        self.assertFalse(head("Identified issues"))
        self.assertTrue(head("Album"))
        page.set_viewport_size({"width": 900, "height": 800})
        self.assertFalse(head("Album"))
        self.assertTrue(head("Format"))
        page.set_viewport_size({"width": 760, "height": 800})
        self.assertFalse(head("Format"))
        self.assertTrue(head("Title") and head("Artist"))                                                     # the essentials always stay
        self.assertTrue(page.locator("#lib-table-body tr").first.locator("td:visible").count() >= 3)
        self.assertEqual(self.real_errors(), [])

    def test_preferences_search_filters_sections_and_fields(self):
        page = self.open_page()
        go_tab(page, "settings")
        page.wait_for_selector("#settings-search")
        visible = lambda sec: page.locator(f"#{sec}").is_visible()   # noqa: E731
        self.assertTrue(visible("set-audio") and visible("set-account") and visible("set-maintenance"))
        page.fill("#settings-search", "bitrate")
        self.assertTrue(visible("set-audio"))
        self.assertFalse(visible("set-account"))
        self.assertFalse(visible("set-appearance"))
        self.assertTrue(page.locator("#cfg-bitrate").is_visible())
        self.assertFalse(page.locator("#cfg-format").is_visible())                                           # only the matching field stays
        self.assertFalse(page.locator("#settings-index a[href='#set-account']").is_visible())
        page.fill("#settings-search", "appearance")                                                          # a section title keeps the whole section
        self.assertTrue(visible("set-appearance"))
        self.assertFalse(visible("set-audio"))
        page.fill("#settings-search", "zzzzqq")
        self.assertTrue(page.locator("#settings-search-empty").is_visible())
        self.assertIn("zzzzqq", page.locator("#settings-search-empty").inner_text())
        page.fill("#settings-search", "")
        self.assertTrue(visible("set-audio") and visible("set-account") and visible("set-maintenance"))
        self.assertTrue(page.locator("#cfg-format").is_visible())
        self.assertFalse(page.locator("#settings-search-empty").is_visible())
        self.assertEqual(self.real_errors(), [])

    def test_backups_can_be_listed_and_a_restore_prepared_then_cancelled(self):
        client = app.test_client()
        created = client.post("/api/backups/create").get_json()
        self.assertTrue(created["success"], created)
        self.addCleanup(lambda: client.post("/api/backups/restore/cancel"))
        self.addCleanup(lambda: Path(created["path"]).unlink(missing_ok=True))
        page = self.open_page()
        go_tab(page, "settings")
        page.wait_for_selector("#backup-list li button")
        self.assertIn(Path(created["path"]).name, page.locator("#backup-list").inner_text())
        self.assertFalse(page.locator("#restore-banner").is_visible())
        page.locator("#backup-list li", has_text=Path(created["path"]).name).locator("text=Restore").click()
        page.wait_for_selector("#ui-dialog:not(.hidden)")
        self.assertIn("saved first", page.locator("#ui-dialog").inner_text())
        self.assertEqual(page.evaluate("document.activeElement.id"), "ui-dialog-cancel")                    # Enter cannot start it by accident
        page.click("#ui-dialog-ok")
        page.wait_for_selector("#restore-banner:not(.hidden)")
        self.assertIn("Close Spotify Downloader Studio and open it again", page.locator("#restore-banner").inner_text())
        self.assertEqual(client.get("/api/backups/list").get_json()["pending"]["source"], Path(created["path"]).name)
        page.click("#restore-banner >> text=Cancel restore")
        page.wait_for_function("() => document.getElementById('restore-banner').classList.contains('hidden')")
        self.assertIsNone(client.get("/api/backups/list").get_json()["pending"])
        self.assertEqual(self.real_errors(), [])

    def test_history_lists_finished_downloads_searches_and_queues_again(self):
        import time
        from src.db import get_db_connection
        conn = get_db_connection()
        now = time.time()
        with conn:
            conn.execute("DELETE FROM jobs WHERE id LIKE 'uihist_%'")
            for i, (title, artists, status, err) in enumerate([("Hist Alpha", "Band One", "COMPLETED", ""), ("Hist Beta", "Band Two", "FAILED", "Failed to load stream"), ("Hist Gamma", "Band One", "CANCELLED", "")]):
                conn.execute("INSERT INTO jobs (id, track_id, title, artists, album, status, error, added_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                             (f"uihist_{i}", f"uih_{i}", title, artists, "Hist Album", status, err, now - i, now - i))
        self.addCleanup(lambda: (conn.execute("DELETE FROM jobs WHERE id LIKE 'uihist_%'"), conn.commit()))
        page = self.open_page()
        go_tab(page, "history")
        page.wait_for_selector("#history-list li >> text=Hist Alpha")
        self.assertEqual(page.locator("#tab-btn-history").get_attribute("aria-selected"), "true")
        self.assertIn("download", page.locator("#history-count").inner_text())
        page.fill("#history-search", "Band Two")
        page.wait_for_function("() => document.querySelectorAll('#history-list li').length === 1")
        self.assertIn("Hist Beta", page.locator("#history-list").inner_text())
        self.assertIn("wouldn't provide the audio", page.locator("#history-list").inner_text())                 # failure explained, not the raw text
        page.fill("#history-search", "")
        page.select_option("#history-status", "CANCELLED")
        page.wait_for_function("() => document.querySelectorAll('#history-list li').length === 1 && document.querySelector('#history-list').innerText.includes('Hist Gamma')")
        page.fill("#history-search", "nothing-like-this")
        page.wait_for_selector("#history-list >> text=Nothing in the history matches that")
        page.fill("#history-search", "")
        page.select_option("#history-status", "")
        page.wait_for_selector("#history-list li >> text=Hist Alpha")
        sent = []
        page.route("**/api/history/redownload", lambda r: (sent.append(r.request.post_data), r.fulfill(status=200, content_type="application/json", body='{"success": true}')))
        page.locator("#history-list li", has_text="Hist Alpha").locator("text=Download again").click()
        page.wait_for_selector("#toast-container .toast-success >> text=Added to the queue")
        self.assertEqual(json.loads(sent[0]), {"job_id": "uihist_0"})
        self.assertEqual(self.real_errors(), [])

    # ------------------------------------------------------------------ feature batch: cleanup, auto-fix, schedule, plugins, support
    def _wav(self, path, frames=800):
        import wave
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * frames)

    def _confirm(self, page):
        page.wait_for_selector("#ui-dialog:not(.hidden)")
        page.click("#ui-dialog-ok")

    def test_duplicate_cleanup_plans_moves_aside_and_can_be_undone(self):
        import shutil
        from src.config_loader import get_downloads_path
        from src.db import get_db_connection
        root = get_downloads_path() / "ui_dedup"
        keep, drop = root / "Dedup UI Song.wav", root / "deeper" / "folder" / "Dedup UI Song copy.wav"
        for f in (keep, drop):
            self._wav(f)
        conn = get_db_connection()
        with conn:
            for f in (keep, drop):
                conn.execute("""INSERT OR REPLACE INTO library_files (path, filename, folder, title, artist, album, duration_sec, format, size_bytes, sha256, has_artwork, bitrate_kbps, tag_status)
                                VALUES (?,?,?,?,?,?,?,?,?,?,1,0,'VALID')""", (str(f), f.name, str(f.parent), "Dedup UI Song", "Dedup Artist", "A", 1.0, "wav", f.stat().st_size, "d" * 64))

        def cleanup():
            shutil.rmtree(root, ignore_errors=True)
            shutil.rmtree(get_downloads_path() / "_duplicates_backup", ignore_errors=True)
            with conn:
                conn.execute("DELETE FROM library_files WHERE path LIKE '%ui_dedup%'")
                conn.execute("DELETE FROM organize_history WHERE original_path LIKE '%ui_dedup%'")
        self.addCleanup(cleanup)

        page = self.open_page()
        go_tab(page, "optimizer")
        page.click("#btn-dedup-plan")
        page.wait_for_selector("#dedup-plan:not(.hidden) >> text=Dedup UI Song")
        self.assertIn("Identical file", page.locator("#dedup-plan").inner_text())
        page.evaluate("() => document.querySelectorAll('.dedup-group').forEach((cb) => { cb.checked = cb.closest('li').innerText.includes('Dedup UI Song'); cb.dispatchEvent(new Event('change', {bubbles: true})); })")
        self.assertIn("Move 1 file aside", page.locator("#btn-dedup-apply").inner_text())
        page.click("#btn-dedup-apply")
        self._confirm(page)
        page.wait_for_selector("#toast-container >> text=Moved 1 duplicate aside")
        self.assertTrue(keep.exists())
        self.assertFalse(drop.exists())
        page.wait_for_selector("#btn-dedup-undo:not(.hidden)")
        page.click("#btn-dedup-undo")
        page.wait_for_selector("#toast-container >> text=Restored 1 file")
        self.assertTrue(drop.exists())
        self.assertEqual(self.real_errors(), [])

    def test_autofix_looks_up_reviews_and_writes_tags(self):
        import shutil
        from unittest.mock import patch
        from mutagen.wave import WAVE
        from src.config_loader import get_downloads_path
        from src.db import get_db_connection
        from src.metadata_enricher import MetadataEnricher, MusicBrainzClient
        root = get_downloads_path() / "ui_autofix"
        wav = root / "Blue Monday.wav"
        self._wav(wav, 4000)
        conn = get_db_connection()
        with conn:
            conn.execute("""INSERT OR REPLACE INTO library_files (path, filename, folder, title, artist, album, duration_sec, format, size_bytes, has_artwork, tag_status)
                            VALUES (?,?,?,?,?,?,?,?,?,0,'MISSING')""", (str(wav), wav.name, str(root), "Blue Monday", "Unknown Artist", "Unknown Album", 0.5, "wav", wav.stat().st_size))

        def cleanup():
            shutil.rmtree(root, ignore_errors=True)
            with conn:
                conn.execute("DELETE FROM library_files WHERE path LIKE '%ui_autofix%'")
        self.addCleanup(cleanup)

        def fake_search(title, artist=""):
            if title != "Blue Monday":
                return []
            return [{"id": "r", "score": 100, "title": "Blue Monday", "length": 500,
                     "artist-credit": [{"name": "New Order", "joinphrase": ""}],
                     "releases": [{"id": "11111111-2222-3333-4444-555555555555", "title": "Power, Corruption & Lies", "date": "1983-05-02", "status": "Official",
                                   "track-count": 8, "release-group": {"primary-type": "Album"}, "media": [{"track": [{"number": "7"}]}]}]}]
        with patch.object(MusicBrainzClient, "search_recordings", fake_search), patch.object(MusicBrainzClient, "cover_available", return_value=False), \
                patch.object(MusicBrainzClient, "min_interval", 0):
            page = self.open_page()
            go_tab(page, "intelligence")
            page.select_option("#autofix-filter", "missing_tags")
            page.select_option("#autofix-limit", "200")
            page.click("#btn-autofix-start")
            page.wait_for_selector("#autofix-results:not(.hidden) >> text=New Order")
            results = page.locator("#autofix-results").inner_text()
            self.assertIn("fills a gap", results)
            self.assertIn("Power, Corruption & Lies", results)
            self.assertIn("1983", results)
            self.assertEqual(page.locator("#autofix-results .autofix-opt:checked").count(), 4)          # gaps are pre-ticked
            page.uncheck("#autofix-results .autofix-opt[data-field='year']")
            self.assertIn("3 changes in 1 file", page.locator("#autofix-count").inner_text())
            page.click("#btn-autofix-apply")
            self._confirm(page)
            page.wait_for_selector("#toast-container >> text=Updated 1 file")
        tags = WAVE(str(wav)).tags
        self.assertEqual(str(tags["TPE1"]), "New Order")
        self.assertEqual(str(tags["TALB"]), "Power, Corruption & Lies")
        self.assertNotIn("TDRC", tags)                                                                     # the box that was unticked was not written
        self.assertIn("No suggestions", page.locator("#autofix-results").inner_text())
        MetadataEnricher.cancel()
        self.assertEqual(self.real_errors(), [])

    def test_schedule_settings_and_the_queue_note(self):
        from datetime import datetime, timedelta
        from src.download_schedule import DownloadSchedule
        from src.settings_service import SettingsService
        before = SettingsService.get_all()["schedule"]
        self.addCleanup(lambda: (SettingsService.update({"schedule": before}), DownloadSchedule.set_override(False)))
        page = self.open_page()
        go_tab(page, "settings")
        page.wait_for_selector("#set-schedule")
        self.assertTrue(page.locator("#schedule-fields").evaluate("el => el.classList.contains('opacity-50')"))      # off: fields look inactive
        page.check("#cfg-schedule-enabled")
        later = datetime.now() + timedelta(hours=2)
        page.fill("#cfg-schedule-start", later.strftime("%H:%M"))
        page.fill("#cfg-schedule-end", (later + timedelta(hours=1)).strftime("%H:%M"))
        click = lambda day: page.locator(f".schedule-day[value='{day}']").evaluate("el => el.click()")   # noqa: E731  (the sticky bars can cover it)
        for day in ("tue", "wed", "thu", "fri", "sat", "sun"):
            click(day)
        self.assertEqual(page.input_value("#cfg-schedule-days"), "mon")
        click("mon")
        self.assertEqual(page.input_value("#cfg-schedule-days"), "")
        self.assertFalse(page.locator(".schedule-day[value='mon']").is_checked())                            # the box stays as clicked
        self.assertTrue(page.locator("#cfg-days-error").is_visible())
        for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
            click(day)
        self.assertEqual(page.input_value("#cfg-schedule-days"), "mon,tue,wed,thu,fri,sat,sun")
        self.assertFalse(page.locator("#cfg-days-error").is_visible())
        page.click("#btn-settings-save")
        page.wait_for_selector("#toast-container .toast-success")
        saved = SettingsService.get_all()["schedule"]
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["start"], later.strftime("%H:%M"))

        go_tab(page, "queue")
        page.wait_for_selector("#queue-schedule-note:not(.hidden) >> text=waiting for the schedule")
        page.click("#queue-schedule-note >> text=Download now anyway")
        page.wait_for_selector("#queue-schedule-note >> text=switched off for now")
        self.assertTrue(DownloadSchedule.override)
        page.click("#queue-schedule-note >> text=Follow the schedule again")
        page.wait_for_selector("#queue-schedule-note >> text=waiting for the schedule")
        self.assertFalse(DownloadSchedule.override)
        self.assertEqual(self.real_errors(), [])

    def test_plugins_screen_built_in_switches_packs_secrets_test_and_import(self):
        from unittest.mock import patch
        from src.db import get_db_connection
        from src.plugin_engine import PluginEngine
        from src.settings_service import SettingsService
        conn = get_db_connection()
        with conn:
            conn.execute("DELETE FROM plugin_packs")
        before = SettingsService.get_all()["audio"]["embed_lyrics"]
        self.addCleanup(lambda: (SettingsService.update({"audio": {"embed_lyrics": before}}), conn.execute("DELETE FROM plugin_packs"), conn.commit()))
        page = self.open_page()
        go_tab(page, "plugins")
        page.wait_for_selector("#plugins-packs article")
        self.assertEqual(page.locator("#tab-btn-plugins").get_attribute("aria-selected"), "true")
        self.assertGreaterEqual(page.locator("#plugins-packs article").count(), 6)
        self.assertGreaterEqual(page.locator("#plugins-builtin li").count(), 6)

        switch = page.locator("#plugins-builtin li", has_text="Embed lyrics").locator("input")
        switch.click()
        for _ in range(50):                                                                               # the page saves it in the background
            if SettingsService.get_all()["audio"]["embed_lyrics"] == (not before):
                break
            time.sleep(0.1)
        self.assertEqual(SettingsService.get_all()["audio"]["embed_lyrics"], not before)

        card = page.locator("#pack-ntfy-push")
        self.assertIn("what it will do", card.inner_text().lower())
        self.assertIn("the address you enter", card.inner_text())
        card.locator("text=Turn on").click()
        card.locator(".pack-error:not(.hidden)").wait_for()
        self.assertIn("Fill in", card.locator(".pack-error").inner_text())
        card.locator("[data-pack-field=topic]").fill("a-very-long-random-topic")
        self.assertEqual(card.locator("[data-pack-field=topic]").get_attribute("type"), "password")           # a secret is never shown in clear
        card.locator("text=Turn on").click()
        page.wait_for_selector("#pack-ntfy-push >> text=Send a test")
        self.assertEqual(page.locator("#pack-ntfy-push [data-pack-field=topic]").input_value(), "********")   # the page never gets it back
        with patch.object(PluginEngine, "deliver", return_value="Delivered (HTTP 200)"):
            page.locator("#pack-ntfy-push").locator("text=Send a test").click()
            page.wait_for_selector("#pack-ntfy-push .pack-test:not(.hidden) >> text=Worked")
        page.locator("#pack-ntfy-push").locator("text=Turn off").click()
        page.wait_for_selector("#pack-ntfy-push >> text=Turn on")

        mine = {"schema": 1, "id": "ui-import", "name": "UI import pack", "version": "0.1", "description": "From a file.",
                "settings": [], "hooks": [{"event": "on_queue_finished", "action": "webhook", "target": "https://example.org/hook"}]}
        page.set_input_files("#plugin-file-input", {"name": "pack.json", "mimeType": "application/json", "buffer": json.dumps(mine).encode()})
        self._confirm(page)
        page.wait_for_selector("#pack-ui-import >> text=Added by you")
        self.assertIn("example.org", page.locator("#pack-ui-import").inner_text())                          # what it will contact is spelled out
        page.set_input_files("#plugin-file-input", {"name": "bad.json", "mimeType": "application/json", "buffer": b"{not json"})
        page.wait_for_selector("#toast-container .toast-error >> text=isn't valid JSON")
        page.locator("#pack-ui-import").locator("text=Remove").click()
        self._confirm(page)
        page.wait_for_selector("#pack-ui-import", state="detached")
        self.assertEqual(self.real_errors(), [])

    def test_support_bundle_button_and_feed_shortcut(self):
        import shutil
        from src.diagnostics_service import DIAGNOSTICS_DIR
        before = set(DIAGNOSTICS_DIR.glob("*.zip")) if DIAGNOSTICS_DIR.exists() else set()

        def cleanup():
            for z in (set(DIAGNOSTICS_DIR.glob("*.zip")) - before):
                z.unlink(missing_ok=True)
            if DIAGNOSTICS_DIR.exists() and not any(DIAGNOSTICS_DIR.iterdir()):
                shutil.rmtree(DIAGNOSTICS_DIR, ignore_errors=True)
        self.addCleanup(cleanup)
        page = self.open_page()
        go_tab(page, "logs")
        page.click("text=Create support bundle")
        page.wait_for_selector("#toast-container .toast-success >> text=Support bundle saved")
        self.assertEqual(len(set(DIAGNOSTICS_DIR.glob("*.zip")) - before), 1)
        self.assertTrue(page.locator("#toast-container .toast-action").count() >= 1)                       # "Show in folder"

        go_tab(page, "settings")
        page.wait_for_function("() => document.getElementById('cfg-update-feed-url') !== null")
        page.click("text=Use this project's GitHub releases")
        from src.version import RELEASE_FEED_URL
        self.assertEqual(page.input_value("#cfg-update-feed-url"), RELEASE_FEED_URL)
        self.assertFalse(page.locator("#settings-savebar").evaluate("el => el.classList.contains('is-clean')"))   # filled in, not saved
        self.assertEqual(self.real_errors(), [])


if __name__ == "__main__":
    unittest.main()
