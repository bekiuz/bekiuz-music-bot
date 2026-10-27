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
- The default input file size is limited to 8 MB to reduce out-of-memory risk on small free hosts. You can change `MAX_INPUT_MB`, but raising it may exceed the host's memory limit.
- FFmpeg uses the system binary when available and otherwise the portable binary included by `imageio-ffmpeg`. Audio processing can be slow on small free instances.
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

Edit `.env` and fill in `BOT_TOKEN`, then run the bot with `python bot.py`. The code loads this local file automatically. The `.env` file is ignored by Git and is not copied into the Docker image.

You can also set `BOT_TOKEN` directly as an environment variable if you prefer. Keep the token private; do not paste it into public chats or screenshots.

## Usage

1. Send `/start` to the bot.
2. Upload an MP3/WAV/M4A/OGG/FLAC audio file or a Telegram voice message.
3. Choose **BEKIUZ intro + outro**, **Echo**, or **Reverb**.
4. Download the returned MP3.

## Deploy to FadeHost (free first bot)

[![Deploy to FadeHost](https://fadehost.com/deploy-button.svg)](https://laplace.fadehost.com/register?intent=bot&repo=https://github.com/bekiuz/bekiuz-music-bot&name=bekiuz-music-bot)

FadeHost currently advertises one free bot per verified account, 256 MB RAM, 0.25 vCPU, 3 GB disk, and no credit card. A Telegram bot using polling and no web address stays awake on the free plan. The account may need an occasional confirmation in the hosting panel, and the free plan has a 1 GB/day traffic limit.

1. Open the deploy button above and verify your email if asked.
2. Choose your own repository: `https://github.com/bekiuz/bekiuz-music-bot`, branch `main`.
3. Set the start command to `python bot.py` if the panel does not detect it automatically. Build command: `pip install -r requirements.txt`.
4. In the environment variables/secret field, set `BOT_TOKEN` to a fresh token from BotFather. Do not use the token previously pasted into chat; revoke it first in BotFather and create a new one.
5. Deploy, then inspect the live console for `Bekiuz Music Bot ishga tushmoqda.` and send `/start` in Telegram.

The bot processes user-uploaded audio files (8 MB default) and returns MP3 results. FFmpeg is bundled through `imageio-ffmpeg`. This is a small free instance, so audio effects may take time or hit memory limits for larger files. Keep your code on GitHub and treat the hosting plan's resources and uptime as limited.
