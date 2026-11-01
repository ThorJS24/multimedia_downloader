# Plugins & extensions

**Automation → Plugins** has two parts.

**Built-in features** are switches for things the app already does (notifications, lyrics, cover art, folder watcher, backups…). Each links to its full settings.

**Plugin packs** are small add-ons that react to what the app does. A pack is a JSON file that only *describes* its behaviour; **no code from a pack ever runs**. The app performs the actions itself, with its normal safeguards, and shows you in plain words what a pack will do before you turn it on.

## What ships with the app

| Pack | What it does |
|---|---|
| Discord notifications | Posts a message to a channel when a batch of downloads finishes |
| Slack notifications | The same, for Slack |
| Phone notifications (ntfy) | Push notification to your phone through [ntfy](https://ntfy.sh) |
| Home Assistant | Sends events to a webhook automation (flash a light, speak a message) |
| Send events to any web address | JSON events for n8n, Zapier, IFTTT and similar |
| Copy finished tracks to a folder | A copy of every finished download in a folder (for a sync tool or a music server) |

Turn one on, fill in what it asks (a webhook address, a topic, a folder), then use **Send a test** to check it works. A card shows the result of the last attempt, so a broken webhook is visible instead of silent.

## Privacy and safety

- **Secrets stay secret.** Settings a pack marks `secret` (webhook addresses, topics) are encrypted with Windows' data protection on this computer, shown masked in the page, never written to the log, and left out of the support bundle. Log lines show only the host of a web address, never the part that holds the token.
- **You see what it will do first.** The *What it will do* list names the event, what is sent (for example "a message containing completed, failed, skipped", or "all details of the event, including output_path") and where (the host, or "the address you enter").
- **Imported packs start switched off.** Replacing a pack with a newer file switches it off again so the new behaviour is reviewed. Only add packs from people you trust.
- **Folders stay inside the app.** A pack can only copy into a folder inside the application's own folder.

## Writing a pack

Save as `something.json` and use **Add a plugin pack…**.

```json
{
  "schema": 1,
  "id": "my-notifier",
  "name": "My notifier",
  "version": "1.0.0",
  "author": "You",
  "description": "Tells my server when a batch finishes.",
  "homepage": "https://example.com/my-notifier",
  "settings": [
    {"key": "url", "label": "Server address", "type": "url", "secret": true, "required": true,
     "help": "Shown under the field."}
  ],
  "hooks": [
    {"event": "on_queue_finished", "action": "webhook", "target": "{url}",
     "body": {"text": "{completed} downloaded, {failed} failed"},
     "headers": {"X-Source": "studio"}}
  ]
}
```

**Top level.** `schema` must be `1`. `id` is 3-40 characters (lowercase letters, digits, hyphens) and cannot reuse a bundled pack's id. `name`, `version` and `description` are required.

**settings** (up to 10) ask the person for values. `type` is `text`, `url` (http/https) or `folder` (inside the app folder, created if needed). `secret` encrypts and masks it, `required` insists on a value, `default` pre-fills it. A key cannot be the same as an event field.

**hooks** (1 to 6):

| Field | Meaning |
|---|---|
| `event` | `on_download_complete`, `on_download_failed` or `on_queue_finished` |
| `action` | `webhook` (send a web request) or `copy_to_folder` (only with `on_download_complete`) |
| `target` | The address or folder. May use `{setting_key}` |
| `body` | Optional. Text, or an object of text values. Without it, the whole event is sent as JSON |
| `format` | `json` (default) or `text`. A text `body` implies `text` |
| `headers` | Optional, up to 5 simple name/value pairs |

**Placeholders.** `{name}` is replaced by a setting or an event field. Only plain names work (`{title.upper}` is left as written), and unknown names are rejected when the pack is added.

| Event | Fields |
|---|---|
| `on_download_complete`, `on_download_failed` | `track_id`, `title`, `artists`, `album`, `provider`, `output_path`, `error` |
| `on_queue_finished` | `completed`, `failed`, `skipped`, `cancelled`, `total` |

`on_queue_finished` fires when a stretch of downloading ends with at least one download finished or failed.

## Limits worth knowing

- Packs cannot run scripts or programs. If you need that, have a pack send a web request to something you run yourself (n8n, Home Assistant, a small local service) and do the work there.
- A failing pack never stops a download or another pack; it just shows its last error.
- The same hooks can still be added by hand under **Automation → Rules & hooks**; packs are the managed, shareable form.

## Other tools that work well with the app

These are separate programs the app does not install or control, but they fit its folders and its webhooks:

- **Library servers** for playing your downloads on any device: Jellyfin, Plex, Navidrome, Emby. Point them at the Downloads folder.
- **Syncthing** (or a phone's own sync app) with the *Copy finished tracks to a folder* pack, to get new tracks onto a phone.
- **MusicBrainz Picard** for deep manual tag work and album matching; the app's own *Fix tags & artwork automatically* covers the common gaps.
- **ntfy**, **Discord**, **Slack**, **Home Assistant**: through the bundled packs.
- **Task Scheduler** (Windows) to open the app at logon or at a fixed time, with the download schedule in Preferences controlling when downloads run.
- **rclone** or your backup tool of choice, to copy the `data/backups` folder (database backups) off the machine.
