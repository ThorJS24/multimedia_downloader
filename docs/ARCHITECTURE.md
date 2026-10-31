# Architecture

Spotify Downloader Studio is a local Windows desktop app: a Flask server bound to `127.0.0.1`, a
pywebview window (or your browser) as the UI, SQLite for state, and FFmpeg for media work.
Everything runs and stores data inside one folder (see *Storage* below).

```
 UI (static/js/*.js + templates/index.html, local assets only)
        │  fetch / SSE, session cookie
 Flask app  src/web/server.py ── auth, security headers, blueprint registration
   └ routes/  system · downloads · library · pages · automation · audio_lab
        │
 Services (plain Python classes)
   metadata_service      resolve Spotify links to track metadata (embed scraper, optional Web API)
   queue_manager         persistent job queue + worker threads + staged pipeline
   media_processor       FFmpeg transcode / verify          tag_writer   ID3 · Vorbis · FLAC · MP4 tags
   file_organizer        naming templates, collisions       lyrics_service  LRCLIB lookup
   library_indexer / library_intelligence / quality_service / fingerprint_service   library analysis
   folder_watcher        watch folders, safe intake         rules_engine  organisation rules
   plugin_engine         webhook / copy hooks, download archive
   scheduler_service + maintenance_service   release monitor, retention, scheduled backups
        │
 settings_service (typed schema) · config_loader · db (SQLite, WAL, migrations) · security · http_client
```

## Download pipeline

`QueueManager._process_job` runs one job through explicit stages. A stage returns `True` to
continue or `False` once the job reached a terminal state; any exception goes through one handler
that either schedules a retry (exponential backoff, honouring `Retry-After`, up to
`performance.retry_limit`) or marks the job `FAILED`.

| Stage | What it does |
|---|---|
| check disk | refuse to start under 50 MB free |
| metadata | resolve the track (falls back to cached job fields) |
| match | confidence check (`MatchEngine`, threshold 0.70) |
| resolve path | naming template + collision strategy; may end the job as `SKIPPED` |
| download | **Spotify jobs: direct Spotify Access Point stream only**, written chunk-by-chunk to a temp file (never held as one big buffer). YouTube / YouTube Music only for a job the user explicitly created with **Download this** in *Find alternatives*; it is tagged from the Spotify track it stands in for (`original_spotify_id`). |
| transcode | FFmpeg reads the temp file and writes to a temp output, which is then moved into place (a failed encode never leaves a partial file at the destination) |
| tag | cover art, optional lyrics (LRCLIB), tags |
| verify | `ffprobe` integrity check |
| complete | record in `download_archive`, fire `on_download_complete` hooks, index the file, pace |

**Rule A:** a Spotify job never silently becomes a YouTube download. A failed Spotify stream fails
the job with a message pointing the user to *Find alternatives*.

## Storage and containment

`config_loader` resolves one data directory (project folder in portable mode, `%LOCALAPPDATA%`
when installed, or `SPOTIFY_STUDIO_DATA_DIR`). `TEMP`/`TMP` are redirected into `<data>/temp`.

```
<data>/config/config.json   settings (secrets DPAPI-encrypted)
<data>/data/studio.db       SQLite (WAL), migrations in src/db.py
<data>/data/backups/        online backups (scheduled, last 7 "auto" kept)
<data>/logs/studio.log      rotating, secret-redacted log
<data>/.cache/              Spotify session cache
<data>/downloads/           your music
```

## Settings

`src/settings_service.py` holds the schema: every key has a type, default, range/choices and a
reader somewhere in the app. Updates are validated (unknown keys are rejected, numbers clamped),
legacy configs are migrated on first read, and secrets are masked in everything returned over the API.

## Data access

Route handlers contain no SQL (enforced by `tests/test_repositories.py`). Web read/write queries live in
`src/repositories.py` (`LibraryRepository`, `StagingRepository`, `JobRepository`, `AuditRepository`);
services such as the queue, scheduler and library intelligence own the queries for their own tables.
Search text is `LIKE`-escaped, and `IN (...)` lists are chunked to stay under SQLite's variable limit.

## Database integrity

SQLite runs with `foreign_keys=ON`. Real parent/child links are enforced with `ON DELETE CASCADE`
(`watcher_events -> watched_folders`, `release_calendar -> monitored_artists`; migration 11 also removed
existing orphans). Other `*_id` columns are loose references to external ids (Spotify ids, batches) and
are intentionally not foreign keys.

## Updates

`update_service.py` is opt-in (see `docs/SECURITY.md`): check a JSON feed, download + SHA-256 verify into
`<data>/data/updates/`, then launch the installer only on an explicit click and only if it is validly signed.
`MaintenanceService` re-checks daily when a feed is configured.

## Background services

Started once by the app shell via `start_background_services()`: the artist-release monitor
(`SchedulerService`) and `MaintenanceService` (retention pruning, scheduled DB backups). Intervals
and retention windows live under `maintenance.*` in settings.

## Frontend

Plain JavaScript files loaded in order from `templates/index.html` (`handlers → dispatch → core →
queue → library → player → settings → intelligence → staging → watcher → pages → sources → boot`).
They share one global scope.

There are no inline event handlers (the CSP forbids them). Markup names a handler instead:
`data-on-click="key"` plus optional `data-args-click='[...]'` (JSON, written with `escapeHtml(JSON.stringify([...]))`).
`static/js/handlers.js` is the allowlisted registry (`key -> function (event, args)`, `this` = the element);
`dispatch.js` is the single delegated listener on `<body>` (bubbling like inline handlers did, honouring
`stopPropagation`; `error` events are caught in the capture phase). To add behaviour, add a registry
entry and reference its key; never write `onclick=`. Styling is a compiled Tailwind stylesheet; icons (lucide) and fonts are vendored. Nothing is
fetched from a CDN. Rebuild with `python tools/build_frontend.py` after changing classes.

## Testing

`python tests/run_tests.py` (everything) or `--fast` (no browser / spawned servers). Tests run
against a throwaway data directory created by `tests/_hermetic.py`, never your real data.
