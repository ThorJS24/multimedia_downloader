# Windows Installation Guide — Spotify Downloader Studio v2.0.0

## Overview
Spotify Downloader Studio provides two flexible distribution modes for Windows 10 and Windows 11 (x64):
1. **Windows Installer (`SpotifyDownloader-v2.0.0-Setup.exe`)**: Recommended for everyday desktop use. Standard per-user installation without requiring UAC administrator elevation.
2. **Portable Distribution (`SpotifyDownloader-v2.0.0-Windows-x64.zip`)**: Recommended for portable USB drives or environments requiring zero writes to the primary `C:` drive.

---

## 1. System Requirements
- **Operating System:** Windows 10 x64 (version 1903+) or Windows 11 x64
- **Processor:** 64-bit Intel / AMD processor
- **RAM:** Minimum 2 GB (4 GB recommended)
- **Disk Space:** 
  - Standard Installation: ~450 MB program files, dynamic storage for downloaded music
  - Portable Installation: ~450 MB standalone folder
- **Runtimes:** Microsoft Edge WebView2 (pre-installed on Windows 10/11)

---

## 2. Standard Windows Installation (Recommended)

### Interactive Setup
1. Download `SpotifyDownloader-v2.0.0-Setup.exe`.
2. Double-click the installer. No administrator prompt is required.
3. Select installation options:
   - Target Directory (defaults to `%LOCALAPPDATA%\Programs\Spotify Downloader Studio`).
   - Create Desktop shortcut (optional checkbox).
4. Click **Install**.
5. Once complete, click **Finish** to immediately launch the application.

### Silent / Unattended Installation
For automated deployments or enterprise scripting, run:
```powershell
.\SpotifyDownloader-v2.0.0-Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

To install silently with a custom destination:
```powershell
.\SpotifyDownloader-v2.0.0-Setup.exe /VERYSILENT /DIR="D:\Custom\SpotifyStudio"
```

---

## 3. Portable Mode (Zero C: Drive Writes)

If you need complete isolation or have constrained space on your `C:` drive:
1. Download `SpotifyDownloader-v2.0.0-Windows-x64.zip`.
2. Extract the archive anywhere on a secondary drive (e.g. `D:\SpotifyStudio`).
3. Notice the `portable.dat` file in the folder. This activates **Portable Mode**.
4. Double-click `SpotifyDownloader.exe`.
5. In Portable Mode:
   - All databases (`data/studio.db`)
   - Configuration files (`config/config.json`)
   - Temporary transcode buffers (`temp/`)
   - Session tokens and cache (`.cache/`)
   - Downloaded music (`downloads/`)
   remain strictly inside that directory, writing **0 bytes to the C: drive**.

---

## 4. Directory Structure Comparison

| Component | Standard Installed Mode | Portable Mode |
| :--- | :--- | :--- |
| **Executables & Binaries** | `%LOCALAPPDATA%\Programs\Spotify Downloader Studio` | `<PortableFolder>\` |
| **FFmpeg & Tools** | `%LOCALAPPDATA%\Programs\Spotify Downloader Studio\bin` | `<PortableFolder>\bin` |
| **SQLite Database** | `%LOCALAPPDATA%\SpotifyDownloaderStudio\data\studio.db` | `<PortableFolder>\data\studio.db` |
| **Configuration** | `%LOCALAPPDATA%\SpotifyDownloaderStudio\config\config.json` | `<PortableFolder>\config\config.json` |
| **Temporary Buffers** | `%LOCALAPPDATA%\SpotifyDownloaderStudio\temp\` | `<PortableFolder>\temp\` |
| **Local Cache** | `%LOCALAPPDATA%\SpotifyDownloaderStudio\cache\` | `<PortableFolder>\.cache\` |
| **Downloads** | User-configured (default: `%LOCALAPPDATA%\...\downloads`) | User-configured (default: `<PortableFolder>\downloads`) |

---

## 5. Troubleshooting & FAQ

### SmartScreen Warning
- When running newly downloaded binaries that are not yet widely signed, Windows SmartScreen may show "Windows protected your PC". Click **More info** and then **Run anyway**.

### WebView2 Runtime Missing
- If launching the GUI fails with a WebView2 runtime error, install the evergreen WebView2 runtime from Microsoft:  
  `https://developer.microsoft.com/en-us/microsoft-edge/webview2/`

### Running in Headless / Server Mode
- To launch without a native desktop window:
  ```powershell
  .\SpotifyDownloader.exe --no-browser
  ```
  Access the dashboard in any browser at `http://127.0.0.1:<PORT>`.
