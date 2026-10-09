# YT Downloader Pro

A Tkinter desktop application for managing yt-dlp downloads, with an optional Chrome/Chromium extension that sends the current page to the desktop app.

## Features

- Queue multiple video, playlist, or channel URLs and monitor download progress.
- Choose video quality or extract MP3 audio; optionally save subtitles, thumbnails, and metadata.
- Organize downloads by channel and playlist, and skip previously downloaded videos.
- Search download history, reopen files, and queue downloads again.
- Send the current browser tab to the app with the companion extension.

## Requirements

- Python 3.10 or newer with Tkinter available.
- FFmpeg for merging separate audio/video streams and extracting MP3 audio.
- Deno is recommended for YouTube JavaScript challenge support. The app can attempt to retrieve yt-dlp's EJS component if it is not installed locally.
- Chrome or another Chromium-based browser is only needed for the optional extension.

## Install and run on Windows

Open PowerShell in the project directory and create a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Install FFmpeg and Deno if they are not already available:

```powershell
winget install ffmpeg
winget install DenoLand.Deno
```

Open a new terminal after installing command-line tools, then launch the app:

```powershell
python yt_downloader_pro.py
```

The application stores its settings, download history, and extension-received cookies under `~/.yt_downloader_pro` in your user profile.

## Install the browser extension

1. Start the desktop app.
2. Open `chrome://extensions` (or the equivalent page in your Chromium browser) and enable **Developer mode**.
3. Select **Load unpacked** and choose `yt-downloader-extension/yt-downloader-extension` from this project.
4. Open the extension popup, copy the token from the desktop app into the token field, and save it.
5. Use the popup, the context menu, or `Alt+Shift+D` to queue the current page.

The extension connects to the desktop app over loopback on ports 8765-8774. To use browser cookies, select **YouTube cookies app ko bhejein** in the extension while signed in to YouTube. The app stores those cookies locally; protect your Windows user account and do not share the resulting cookie file or app data directory.

## Notes

- Without FFmpeg, downloads are limited to formats that do not require merging; MP3 extraction is unavailable.
- YouTube may require a signed-in account or reject automated requests. Use only your own account's cookies and keep yt-dlp updated.
- Only download content you are authorized to access and save, and follow the applicable platform terms and copyright rules.