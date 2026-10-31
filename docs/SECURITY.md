# Security model

The server only listens on `127.0.0.1`, but "local" is not "trusted": any web page in your browser
can try to reach it. These are the controls, and where they live.

| Threat | Control |
|---|---|
| Another site or process calling the local API | Per-run session token (`SESSION_AUTH_TOKEN`). The launcher opens `/?token=…`, which is exchanged for an `HttpOnly`, `SameSite=Strict` cookie and removed from the URL. Every non-static request needs the cookie (or `X-Session-Token`). |
| DNS rebinding | `Host` must be `127.0.0.1`, `localhost` or `[::1]`. |
| Cross-origin requests | `Origin`, when present, must equal `Host`. |
| Script injection into the UI | No remote scripts or fonts (all assets are served locally). Content-Security-Policy is `script-src 'self'` (no inline scripts, no inline `on*=` handlers, no eval); remote metadata (titles, artists, errors, log lines) is HTML-escaped before it reaches `innerHTML`. |
| Reading credentials/config/database through the file APIs | File routes reject paths inside `.cache`, `config`, `data`, `.venv`, `bin` and `src`. |
| Arbitrary command execution | Plugin hooks are limited to `webhook` (http/https) and `copy_to_folder`. There is no shell hook. |
| Secrets at rest | `spotify.password` and `spotify.client_secret` are encrypted with Windows DPAPI (`dpapi:` prefix) and masked (`********`) in API responses and diagnostics. |
| Secrets in logs | Every log handler formats through `RedactingFormatter` (passwords, client secrets, tokens, bearer headers, DPAPI blobs). |
| Rate-limit abuse of upstream services | `http_client` retries 429/5xx honouring `Retry-After`; `spotipy` is configured with retries; failed jobs back off exponentially. |
| Tampered or corrupt updates | Update checks are opt-in (`maintenance.update_feed_url`, HTTPS only). A download must match the feed's SHA-256, and the installer is only launched when it carries a valid Authenticode signature (`maintenance.update_require_signature`, on by default). The hash comes from the same server as the file, so it guards against corruption, not against a compromised server; the signature check is what covers that. |
| Corrupt or half-written config | `save_config` writes a temp file and renames it; an unreadable `config.json` is kept as `config.json.corrupt`. |

## Known gaps

- `style-src` still allows `'unsafe-inline'` (the templates and some scripts use inline `style=` attributes).
  Inline styles cannot execute code, but they can alter how a page looks.
- DPAPI protects secrets from other Windows users, not from other programs running as you.
- The exe and installer are unsigned unless you set the `STUDIO_SIGN_*` variables (see
  `build_common.py`); Windows SmartScreen will warn on first run.
- Downloading Spotify audio is against Spotify's terms of service; use a secondary account and only
  copy content you have the right to.

## Reporting

This is a personal project: open an issue with details, and do not post real credentials or tokens.
