# Bekiuz Media Bot 🎵📥

Telegram bot for public media links, song search, video tools, and audio effects.

## Features

- Downloads one publicly accessible media item from Instagram, YouTube, TikTok, Facebook, X/Twitter, Pinterest, Reddit, Vimeo, SoundCloud, Snapchat, Threads, and CapCut links.
- Sends images as photos and supported video/audio formats as native Telegram media when possible.
- Searches YouTube for up to 20 song results. The first 10 appear as numbered buttons; tapping a number downloads that result as MP3, and **Keyingisi** opens the next 10.
- Shows Uzbekistan's current popular YouTube music-category videos with `/top10`, using the official YouTube Data API.
- Users can opt in to a daily TOP 10 message at 09:00 Tashkent time with `/daily_on` and opt out with `/daily_off`.
- Extracts MP3 audio from a video uploaded to Telegram.
- Compresses uploaded videos with FFmpeg (resizes wide videos to at most 1280 pixels and re-encodes them).
- Adds a spoken BEKIUZ intro/outro, Echo, or a Reverb-like effect to uploaded audio.
- Adds “✅ Bekiuz Bot orqali yuklab olindi” to successful media downloads.
- Shows a **Musiqasini qidirish** button under downloaded videos, plus **Botni ulashish**, **Guruhga qo‘shish**, and **Kanalga qo‘shish** buttons.
- Retrieves its Telegram username at startup to build sharing and add-to-chat links.
- Saves metadata-only history (action, filename/title, and UTC time) for each user's last 50 actions. It does not store downloaded media in the history database.
- Adds a per-user request cooldown to reduce spam and protect small hosting instances.

## Limits and important notes

- Link downloads work only when the media is publicly accessible without login. The bot does not use account cookies, access private posts, bypass paywalls, or bypass platform access restrictions.
- A link may fail if a platform changes its site, asks for login, or does not expose the media to unauthenticated requests.
- Suno share links are not downloaded directly. Use a download/export option that Suno makes available for that track, then upload the exported audio file.
- Public link downloads are limited to 35 MB by default (`MAX_DOWNLOAD_MB`); selected MP3 uploads are capped at 45 MB; uploaded video inputs are limited to 18 MB (`MAX_VIDEO_MB`); audio inputs for effects are limited to 8 MB (`MAX_INPUT_MB`). Free host RAM and Telegram file limits may be lower.
- Song selection is based on YouTube search results. The video’s **Musiqasini qidirish** button searches using the video title; it does not perform audio fingerprint recognition. Exact identification from sound requires a separate music-recognition API.
- Downloads work only for media that is publicly accessible and that the user has permission to save. The bot does not bypass login or private-post restrictions.
- Daily TOP 10 requires a Google Cloud API key with YouTube Data API v3 enabled. The list contains popular music-category videos for Uzbekistan, not a guarantee of the official daily song chart. The bot sends links to listen and does not redistribute the songs as MP3 files.
- Video compression depends on the FFmpeg build supporting `libx264`. On small free hosts, processing can take time or hit memory limits.
- BEKIUZ spoken tag generation uses `edge-tts` and requires internet access.

## Environment variables

- `BOT_TOKEN` — required Telegram bot token.
- `YOUTUBE_API_KEY` — required for `/top10` and daily chart subscriptions.
- `YOUTUBE_REGION` — optional two-letter region code (default `UZ`).
- `TTS_VOICE` — optional voice (default `uz-UZ-SardorNeural`).
- `MAX_INPUT_MB` — optional audio processing input limit (default 8).
- `MAX_DOWNLOAD_MB` — optional public link download limit (default 35).
- `MAX_VIDEO_MB` — optional uploaded video limit (default 18).
- `USER_COOLDOWN_SECONDS` — optional per-user request delay (default 3).

Keep the token private. Never hard-code it into source code, README files, screenshots, or commits. If a token is exposed, revoke it through BotFather and create a new one.

## Run locally

Use Python 3.12+ and FFmpeg. Install dependencies:

```bash
pip install -r requirements.txt
```

Set `BOT_TOKEN` as an environment variable, then run:

```bash
python main.py
```

## Deploy to Bot-Hosting.net

1. Sync or upload the files from this repository.
2. Confirm `main.py`, `bot.py`, and `requirements.txt` exist in the host file manager.
3. Add `BOT_TOKEN` as a user environment variable using a fresh token from BotFather.
4. Leave `STARTUP_FILE=main.py` and start the deployment.
5. Read the console and test `/start` in Telegram.

## Telegram usage

- `/start` — overview.
- Send a public YouTube or Instagram media URL — download the video, photo, or audio when supported.
- Send a song name or short lyric fragment — choose one of the first 10 numbered results to receive an MP3, then tap **Keyingisi** for more.
- Upload a video — choose **Videodan MP3 ajratish** or **Videoni siqish**.
- Upload audio — choose **BEKIUZ intro + outro**, **Echo**, or **Reverb**.
- `/search <query>` — explicit song search with numbered MP3-download buttons.
- `/top10` — current YouTube popular music videos for Uzbekistan.
- `/daily_on` — opt in to daily TOP 10 at 09:00 Tashkent time.
- `/daily_off` — turn daily TOP 10 messages off.
- `/history` — last 10 personal actions.
- `/cancel` — clear the pending audio/video selection.
