# Changelog

## 2.0.1 (unreleased)

### UI / UX overhaul
- **Navigation**: 14 flat sidebar items became 5 destinations (Home, Downloads, Library, Automation, Settings) with a sub-navigation for the sections inside each. URL hash routing keeps your place across reloads; the sidebar collapses (automatically on narrow windows); Alt+1..5, Ctrl+K, `?` for shortcuts.
- **Home** is now a status board: add-music box, what is downloading, what failed (with Retry), continue listening. Empty shelves are hidden instead of shown as placeholders.
- **One add flow**: "Review & add" (staging dry-run) or "Add all without review". The old third path (preview table) and the misleading "Quick Direct Enqueue" label are gone.
- **Match lab** (was "Match Review") now scores a Spotify track against real YouTube Music / YouTube candidates; it used to compare a track with itself.
- **Settings** rebuilt: grouped sections, every setting in the schema exposed, one save bar with unsaved-change tracking (only changed keys are sent), validation, import/export, folder picker, backups, real storage facts from the server.
- **Account**: Free / Premium state, sign-in form (Enter submits, password reveal), sign-out.
- **Design system**: semantic colour tokens (dark, light, follow-Windows), a 12px minimum type size, AA-contrast secondary text, `prefers-reduced-motion`, selectable text.
- **Accessibility**: modal focus trapping/restore with inert background, Esc closes every dialog, keyboard-operable cards and rows, accessible names for icon buttons, `aria-sort` / `aria-pressed` / `aria-current`, live regions, Space no longer hijacks focused buttons.
- **Feedback**: in-app confirm/prompt dialogs replace native ones; errors persist until dismissed and can be copied; undo for queue removals and for bulk reorganise; "Show in folder" after exports; connection-lost banner.
- **Performance**: the queue is polled adaptively (fast only when visible/active), uses an ETag (304 when unchanged) and diffs rows instead of rebuilding the table; `/api/status` no longer opens a Spotify session on every call (cached); lazy images; log view capped and batched; library sorting is server-side with stale-response protection.
- **Player**: tri-state repeat, no-repeat shuffle, whole-library play queue, remembered volume/speed/last track, OS media keys, a missing file no longer silently jumps to another track.
- **Desktop**: window size/position remembered, startup waits for the server instead of a fixed sleep, native folder picker.

- **Queue**: drag a waiting row to change the download order (the up/down buttons remain as the keyboard path; dragging is off while a filter or sort hides the true order). New `move_job` queue operation.
- **Library**: column chooser (Year, Genre and Time are now available, sortable server-side) and saved views (filter + search + sort + columns), both remembered on this computer.

- **Visual layer ("Aurora Studio")**, inspired by Magic UI, React Bits, Aceternity, Uiverse and Vanta-style backgrounds, implemented as original vanilla CSS/JS (no React, no CDN, works offline under the strict CSP): drifting aurora background, pointer-following spotlight cards, a travelling border beam and a particle network on the Home add-box, shimmer CTAs, animated gradient headings, blur-fade section entrances, number tickers, an equalizer on the player cover, animated switches, toast countdown bars, skeleton loaders and a floating empty-state icon. The accent colour follows the album art of the playing track.
- **Visual effects setting** (Settings -> Appearance): Full / Subtle (default) / Off. Windows "reduce motion" always forces Off; animations pause while the window is hidden.
- **Command palette** (Ctrl+K): jump to any section, run actions (add music, scan, pause queue, theme, effects, sign in...) or search and play a track. `/` focuses the search box.
- **First-run wizard** is now a 3-step flow with a progress indicator.

### Fixed (UI)
- Finishing the first-run wizard threw `engine is not defined` and showed an error.
- Artist Monitor's "Fetch & Download" called a function that did not exist.
- The tag editor kept Year/Genre from the previous file and could blank title/artist; it now starts clean and saves only changed fields.
- Album/playlist M3U exports all overwrote `library.m3u` and reported "undefined tracks".
- Bulk-reorganise success was shown as an error and undo required typing a batch ID.
- Collection names were inserted unescaped into the queue filter.
- Queue CSV export is protected against spreadsheet formula injection.
- Log terminal DOM grew without bound; Esc missed five dialogs (the YouTube player kept playing).
- Responsive utilities (`hidden lg:block`) were defeated by a global `!important` rule.
- Downloads folder (settings and first-run wizard) could be pointed outside the application directory.
- Dialogs created at runtime (confirm, palette) did not make the page behind them inert because their open state was registered too late.
- Closing the desktop window raised `SystemExit` inside the WinForms FormClosing handler, which Windows showed as an "Unhandled exception" dialog (`Python.Runtime.PythonException: 0`). The handler now only cleans up; the process exits after the window has closed.


### Security
- Every non-static request now requires the per-run session token (HttpOnly/SameSite=Strict cookie); `Host` and `Origin` are checked.
- Content-Security-Policy and standard security headers; no CDN scripts, styles or fonts (Tailwind, lucide and fonts are vendored).
- Remote metadata (titles, artists, errors, log lines) is HTML-escaped before reaching `innerHTML`.
- Removed shell-command plugin hooks; webhooks must be http(s). File routes can no longer read config, database or code folders.
- `spotify.password` / `client_secret` are DPAPI-encrypted at rest and masked everywhere they are returned.
- Secrets are redacted from the console, the live log stream and the new rotating `logs/studio.log`.
- yt-dlp no longer runs with TLS verification disabled.

### Fixed
- The first-run wizard offered a "compliant" engine that was never used; removed. Spotify links always use the direct Spotify session; alternatives are explicit only.
- Plugin hooks now actually fire (`on_download_complete`, `on_download_failed`); the download archive is now written; the release monitor and scheduled backups now run.
- Acoustic fingerprinting hashed time-domain amplitude, so different tones collided; it is now a real FFT spectral fingerprint (cache rebuilt by migration 10).
- Loudness analysis read the first per-frame value instead of the summary, and invented numbers when parsing failed.
- Quality status now reports every finding and the most severe one instead of whichever check ran last.
- A refused second launch leaked its mutex handle, which blocked later launches.
- Metadata cache never expired, so re-downloading a changed playlist reused the old track list.
- A corrupt `config.json` is kept as `config.json.corrupt` instead of being silently replaced.
- Build ran the exe in installed mode before writing `portable.dat`, writing data to C:.

### Added
- Typed, validated settings schema (unknown keys rejected, values clamped), settings export/import, legacy-config migration.
- Staged download pipeline with automatic retries (exponential backoff, honours `Retry-After`), lyrics via LRCLIB, retention pruning, scheduled DB backups, crash logging.
- Shared HTTP client with 429/5xx handling; `restore_queue_on_startup` and `retry_limit` now work.
- Accessibility pass (labels, dialog roles, skip link, `aria-current`), local-only assets.
- `python tests/run_tests.py [--fast]`; hermetic tests that never touch real data.
- Build: single version source (`src/version.py`), optional Authenticode signing via `STUDIO_SIGN_*`, `tools/build_frontend.py`.

### Also in this release
- Inline `on*=` handlers (317) replaced by an allowlisted registry + delegated dispatcher; CSP is now `script-src 'self'`.
- The **Find alternatives** flow returned HTTP 500 on select/current (routes called the service with the wrong arguments); fixed, plus Remove Association (`/api/alternatives/clear`) which the button always promised.
- YouTube embed player allowed through the CSP (`frame-src`), pre-escaped JSON no longer double-escaped in handler arguments.
- `/api/library/overview` crashed (nonexistent column); M3U export crashed on NULL durations and allowed newline injection; `/api/sync/apply` accepted an empty playlist id.
- Concurrent settings saves/reads could fail with "Access denied" on Windows; reads and the atomic rename are now serialised.
- Foreign keys enforced for `watcher_events` and `release_calendar` (cascade deletes, migration 11 removes orphans).
- Metadata fallback no longer downloads a whole track just to read its tags; search results now carry the canonical capability data.
- Opt-in update checker with verified download and signature-gated installer launch.
- The project now runs on Python 3.14 (full suite of 399 tests passes on 3.14 and 3.10); the old 3.10 venv is kept as .venv310 for rollback.

### Final pass
- Downloads are streamed to disk chunk-by-chunk (byte-for-byte identical to librespot's own `read()`, verified in tests) instead of being assembled in memory; transcoding reads that file and writes atomically.
- **Find alternatives** now works end to end: `find` crashed (`dict.to_dict`) and returned a different shape than the modal reads; **Download this** queues the chosen match, tagged from the Spotify track.
- Route handlers no longer contain SQL (repository layer + architecture test); search wildcards (`%`, `_`) are literal; large `IN` lists no longer crash.
- ruff and mypy both pass (`pyproject.toml`); exceptions are chained, import placement fixed, typing gaps closed.
- Per-thread SQLite connections are closed when their thread ends (no `ResourceWarning` on 3.13+).
- Signing hook and version-resource generation are unit-tested.

### Added (project)
- MIT `LICENSE`; the exe version resource now states it.

### Changed
- `server.py` split into blueprints; `app.js` split into ordered scripts; ffmpeg "essentials" build; installer 256 MB -> 81 MB.

### Removed
- Dead modules (legacy downloader pipeline, rate limiter, task manager), unused settings, process-report docs.

## 2.0.0
Initial release.
