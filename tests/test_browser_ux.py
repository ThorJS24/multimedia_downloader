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
        page.wait_for_selector("#tab-matching:not(.hidden)")
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

    def test_effect_modes_apply_persist_and_switch_features_off(self):
        page = self.open_page()
        self.assertEqual(page.evaluate("document.body.dataset.fx"), "subtle")             # calm by default
        page.evaluate("() => { setFxChoice('full'); }")
        self.assertTrue(page.evaluate("document.body.classList.contains('fx-full')"))
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('canvas.fx-net')).display"), "block")
        page.evaluate("() => { setFxChoice('off'); }")
        self.assertEqual(page.evaluate("getComputedStyle(document.getElementById('fx-aurora')).display"), "none")
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('canvas.fx-net')).display"), "none")
        page.reload(wait_until="networkidle")
        self.assertEqual(page.evaluate("document.body.dataset.fx"), "off")                # remembered
        go_tab(page, "settings")
        self.assertTrue(page.is_checked("input[name=fx][value=off]"))
        page.check("input[name=fx][value=subtle]")
        self.assertEqual(page.evaluate("document.body.dataset.fx"), "subtle")

    def test_windows_reduce_motion_overrides_the_chosen_effect_mode(self):
        ctx = self.browser.new_context(reduced_motion="reduce")
        ctx.add_init_script("try{localStorage.setItem('studio.fx','\"full\"')}catch(e){}")
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{self.port}/?token={SESSION_AUTH_TOKEN}", wait_until="networkidle")
        self.addCleanup(ctx.close)
        self.assertEqual(page.evaluate("document.body.dataset.fx"), "off")
        self.assertEqual(page.evaluate("getComputedStyle(document.getElementById('fx-aurora')).display"), "none")
        page.evaluate("document.getElementById('wizard-modal') && document.getElementById('wizard-modal').classList.add('hidden')")
        go_tab(page, "settings")
        self.assertTrue(page.is_visible("#fx-reduced-hint"))

    def test_number_ticker_always_lands_on_the_exact_value(self):
        page = self.open_page()
        page.evaluate("() => { setFxChoice('full'); }")
        page.evaluate("() => { const e = document.getElementById('dash-completed'); e.dataset.value = '0'; e.textContent = '0'; FX.tick(e, 250, 300); }")
        page.wait_for_function("() => document.getElementById('dash-completed').textContent === '250'")
        page.evaluate("() => { setFxChoice('off'); const e = document.getElementById('dash-completed'); FX.tick(e, 7); }")
        self.assertEqual(page.inner_text("#dash-completed"), "7")                            # instant when effects are off

    def test_cover_art_colour_drives_the_accent_and_resets(self):
        page = self.open_page()
        accent = page.evaluate("""async () => {
            const c = document.createElement('canvas'); c.width = c.height = 8;
            const g = c.getContext('2d'); g.fillStyle = '#d8203a'; g.fillRect(0, 0, 8, 8);
            await FX.coverAccent(c.toDataURL());
            return document.documentElement.style.getPropertyValue('--np');
        }""")
        r, gch, b = [int(x) for x in accent.split()]
        self.assertTrue(r > 150 and r > gch + 60 and r > b + 40, accent)                      # a vivid red, not the default green
        page.evaluate("() => { FX.resetAccent(); }")
        self.assertEqual(page.evaluate("document.documentElement.style.getPropertyValue('--np')"), "")

    def test_spotlight_follows_the_pointer_on_cards(self):
        page = self.open_page()
        box = page.locator("#tab-dashboard .fx-card").first.bounding_box()
        page.mouse.move(box["x"] + 40, box["y"] + 30)
        page.mouse.move(box["x"] + 60, box["y"] + 35)
        page.wait_for_function("() => { const c = document.querySelector('#tab-dashboard .fx-card'); return c.style.getPropertyValue('--mx') !== ''; }")
        self.assertRegex(page.evaluate("document.querySelector('#tab-dashboard .fx-card').style.getPropertyValue('--mx')"), r"^[\d.]+px$")

    def test_decorations_do_not_leak_into_the_accessibility_tree_or_layout(self):
        page = self.open_page()
        self.assertEqual(page.get_attribute("#fx-aurora", "aria-hidden"), "true")
        self.assertEqual(page.get_attribute("canvas.fx-net", "aria-hidden"), "true")
        # the screen-reader-only label of the Home box must stay hidden (regression: a style rule un-hid it)
        box = page.locator("label[for=home-quick-input]").bounding_box()
        self.assertTrue(box is None or (box["width"] <= 2 and box["height"] <= 2), box)

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


if __name__ == "__main__":
    unittest.main()
