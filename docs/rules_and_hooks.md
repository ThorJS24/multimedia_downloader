# Rules & hooks

Open **Automation → Rules & hooks**.

## Organising rules
A rule says: *when a file ... then ...*.

| When a file | Value |
|---|---|
| is in the library | (every file) |
| has a genre / artist / album containing | text, not case-sensitive |
| is in the format | e.g. `mp3`, `flac` |
| has a bitrate below | kbps, e.g. `192` |
| is missing the tag | `title`, `artist`, `album`, `genre` or `year` |

| Then | Pattern |
|---|---|
| move it to the folder pattern | e.g. `{artist}/{album}/{title}.{ext}`, relative to your downloads folder |
| rename it using the pattern | a file name only, e.g. `{artist} - {title}.{ext}` |
| flag it for review | any note; nothing moves |

Rules are checked by priority (lowest number first) and the first match wins. With no enabled rules, files are sorted
into `Artist / Album` folders.

Patterns must be relative: drive letters, leading slashes and `..` are rejected.

## Preview, apply, undo
1. **Preview changes** shows exactly what would move, with name clashes marked. Nothing on disk changes.
2. **Apply** asks for confirmation. Only files the app has indexed can be moved, and only into the downloads folder.
   Name clashes follow the policy you pick: skip, keep both, or replace (the replaced file is kept in a recovery buffer).
3. Every apply is a *batch*. **Undo** on a batch moves all its files back.

## Hooks
Run something after a download **finishes** or **fails**, or when the queue **finishes a batch**:
- **Call a web address** (http/https): receives JSON with the track title, artists, album, output path and error.
- **Copy the file**: copies the finished file into a folder. The folder must be inside the application directory.

## Download archive
A read-only list of tracks the app already downloaded, so importing the same playlist again doesn't fetch them twice.
