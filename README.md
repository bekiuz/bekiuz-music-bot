# Bekiuz Media Bot 🎵📥

Telegram bot for public media links, song search, and audio effects.

## Features

- **Public media links:** attempts to download one media item from supported public Instagram, TikTok, YouTube, Facebook, X/Twitter, Pinterest, Reddit, Vimeo, SoundCloud, Snapchat, Threads and CapCut links.
- **Song search:** send a song title or a short remembered lyric fragment; the bot returns up to five YouTube search results.
- **BEKIUZ intro + outro:** adds a spoken “Bekiuz” tag at the start and end of an uploaded audio file.
- **Echo** and **Reverb-like** effects for audio uploaded directly to Telegram.
- Temporary processing only; files are not saved as a permanent library.

## Limits and important notes

- Media links must be publicly accessible without login. The bot does not use account cookies, access private posts, bypass paywalls, or bypass platform access restrictions.
- A link may fail if its platform changes its site, asks for login, or does not expose the media to unauthenticated requests.
- Suno share links are not downloaded directly. Use a download/export option that Suno makes available for that track, then upload the exported audio file.
- Public media link downloads are limited to 35 MB by default (MAX_DOWNLOAD_MB); audio uploaded for effects is limited to 8 MB by default (MAX_INPUT_MB). Raising limits may exceed the memory/storage limits of free hosting.
- Song search uses YouTube search results. It does not guarantee acoustic identification from a recording. Exact audio recognition would need a separate music-recognition API.
- Audio effects use FFmpeg and edge-tts for the spoken Bekiuz tag. The first request can take time on a small host.

## Run locally

Use Python 3.12+ and FFmpeg. Install dependencies:

```bash
pip install -r requirements.txt
```

Set the Telegram token as an environment variable named BOT_TOKEN. Do not hard-code it in the source code or commit it to GitHub. Then run:

```bash
python main.py
```

## Deploy to Bot-Hosting.net

1. Sync or upload the repository files from https://github.com/bekiuz/bekiuz-music-bot.
2. Confirm these files are present in the hosting file manager: main.py, bot.py, and requirements.txt.
3. Set the user environment variable BOT_TOKEN to a newly generated token from BotFather.
4. Keep the system startup setting STARTUP_FILE=main.py and start the deployment.
5. Check the console for “Bekiuz Music Bot ishga tushmoqda.”, then test /start in Telegram.

The main.py file is a compatibility entry point for hosts that automatically start main.py.

## Telegram usage

- Send /start to view instructions.
- Send a supported public media URL to attempt a download.
- Send a song name or a short lyric fragment to search.
- Upload an audio file and choose BEKIUZ intro + outro, Echo, or Reverb.
- Use /search <song title or lyric fragment> to search explicitly.
- Use /cancel to clear the pending audio selection.
