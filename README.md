# Bekiuz Music Bot 🎵

Telegram bot that processes **audio files uploaded by the user** and returns an MP3 with echo/reverb effects or a spoken BEKIUZ intro/outro.

## Features

- **BEKIUZ intro + outro** — generates a spoken “Bekiuz” tag and adds a long echo at the beginning and end.
- **Echo** — adds several delayed repeats to the uploaded audio.
- **Reverb-like effect** — creates a wider, more spacious sound with layered echoes.
- Returns the result as an MP3 file in Telegram.
- Processes audio in a temporary directory; uploaded tracks are not saved by the bot as a permanent library.

## Important limitations

- This first version processes audio files that users upload to the bot. It does **not** download tracks from Suno share links or bypass any platform's download restrictions.
- Maximum input file size is 20 MB with the standard Telegram Bot API download endpoint.
- The BEKIUZ spoken tag uses Microsoft Edge text-to-speech through `edge-tts`. It needs internet access. The default voice is `uz-UZ-SardorNeural`; if that voice is unavailable in the runtime, set `TTS_VOICE` to an available Edge TTS voice.
- Audio quality and the exact pronunciation/echo tail can vary depending on the source track and selected voice.

## 1. Create the Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot` and complete the prompts.
3. Copy the token privately.
4. **Do not place the token in source code, README, issues, or commits.** Add it as an environment variable named `BOT_TOKEN` in the hosting provider.

If a token is ever posted publicly, revoke it through BotFather and create a new one.

## 2. Run with Docker (recommended)

Install Docker, then create a local `.env` file using `.env.example` as a template. Fill in your token only in that local file:

```env
BOT_TOKEN=put_your_telegram_bot_token_here
TTS_VOICE=uz-UZ-SardorNeural
MAX_INPUT_MB=20
FFMPEG_BIN=ffmpeg
LOG_LEVEL=INFO
```

Build and run from the repository folder:

```bash
docker build -t bekiuz-music-bot .
docker run --rm --env-file .env bekiuz-music-bot
```

## 3. Run locally without Docker

Install Python 3.12+ and FFmpeg, then run:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and fill in `BOT_TOKEN`, then run the bot. Set environment variables in your terminal or load the local `.env` using your preferred environment manager. The code intentionally does not auto-load `.env` to avoid adding another dependency.

To load `.env` without changing the code, install `python-dotenv` locally and run with your environment manager, or set the variables directly in PowerShell:

```powershell
$env:BOT_TOKEN = "YOUR_TOKEN_HERE"
python bot.py
```

Keep the token private; do not paste the example command containing your real token into public chats or screenshots.

## Usage

1. Send `/start` to the bot.
2. Upload an MP3/WAV/M4A/OGG/FLAC audio file or a Telegram voice message.
3. Choose **BEKIUZ intro + outro**, **Echo**, or **Reverb**.
4. Download the returned MP3.

## Hosting

Deploy the repository as a Docker service and add `BOT_TOKEN` (plus optional voice/settings) in the host's secret environment-variable settings. Use a host that supports a persistent, always-running worker; a static website host cannot run this bot.
