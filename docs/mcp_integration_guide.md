# Model Context Protocol (MCP) Server Integration Guide

This guide explains how to connect **ChatGPT Desktop**, **Claude Desktop**, or other AI agents and IDEs to **Spotify Downloader Studio** using the Model Context Protocol (MCP).

---

## 1. Overview & Capabilities

The built-in MCP server exposes live read and query capabilities directly to your AI assistant:

| Tool Name | Description | Key Parameters |
|---|---|---|
| `search_music` | Searches across Spotify, YouTube Music, YouTube, and local library. | `query`, `scope` (`all`/`spotify`/`ytmusic`/`youtube`/`local`), `category`, `limit` |
| `resolve_music_url` | Resolves exact metadata for any Spotify, YouTube Music, or YouTube URL without silent substitution (Rule A). | `url` |
| `find_cross_source_alternatives` | Discovers and conservatively scores alternatives on YouTube Music & YouTube (Rule B/C), explaining variance (studio vs live, remix, acoustic). | `title`, `artist`, `spotify_id`, `duration_ms`, `isrc` |
| `get_library_summary` | Returns local library statistics: total tracks, distinct artists, albums, and recent tracks on D: drive. | None |
| `get_queue_status` | Returns active download queue statistics, counts (queued, active, completed, failed), and recent jobs. | None |
| `get_provider_capabilities` | Inspects current capability matrix (`can_download`, `can_embed`, licensing disclosures) per provider. | None |

---

## 2. Configuration for ChatGPT & Claude Desktop

### ChatGPT Desktop / Custom Agents Configuration
Add the following to your MCP client configuration (e.g. `mcp_config.json` or your MCP client's settings):

```json
{
  "mcpServers": {
    "spotify-downloader-studio": {
      "command": "d:\\mini-projects\\Spotify_playlist_downloader\\.venv\\Scripts\\python.exe",
      "args": [
        "d:\\mini-projects\\Spotify_playlist_downloader\\main.py",
        "mcp"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

---

## 3. Running the MCP Server Manually

### Stdio Mode (Default for AI Clients)
```powershell
.\.venv\Scripts\python.exe main.py mcp
```

### Server-Sent Events (SSE) Mode (For Web/Network Clients)
```powershell
.\.venv\Scripts\python.exe main.py mcp --transport sse --port 8000
```

---

## 4. Example Prompts for ChatGPT / Claude

Once connected, you can ask ChatGPT natural language queries such as:
1. *"What tracks are currently downloaded in my local library?"* -> invokes `get_library_summary`
2. *"Search for Bohemian Rhapsody across Spotify and YouTube Music."* -> invokes `search_music`
3. *"Check what's currently in my download queue."* -> invokes `get_queue_status`
4. *"Can you resolve this Spotify playlist URL: https://open.spotify.com/playlist/...?"* -> invokes `resolve_music_url`
5. *"Find YouTube alternatives for the track 'Hotel California' by Eagles."* -> invokes `find_cross_source_alternatives`
