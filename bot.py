from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from html import escape
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import edge_tts
import imageio_ffmpeg
import yt_dlp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("bekiuz_music_bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = ""
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
YOUTUBE_REGION = os.getenv("YOUTUBE_REGION", "UZ").strip().upper()[:2] or "UZ"
BOT_TIMEZONE = ZoneInfo("Asia/Tashkent")
TTS_VOICE = os.getenv("TTS_VOICE", "uz-UZ-SardorNeural").strip()
MAX_INPUT_BYTES = int(os.getenv("MAX_INPUT_MB", "8")) * 1024 * 1024
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "35")) * 1024 * 1024
SUPPORTED_DOMAINS = (
    "instagram.com", "tiktok.com", "youtube.com", "youtu.be",
    "facebook.com", "fb.watch", "x.com", "twitter.com", "pinterest.com",
    "reddit.com", "redd.it", "vimeo.com", "soundcloud.com", "snapchat.com",
    "threads.net", "capcut.com",
)
configured_ffmpeg = os.getenv("FFMPEG_BIN", "").strip()
# Prefer a system FFmpeg where available; fall back to the portable FFmpeg
# binary shipped by imageio-ffmpeg for hosts without system packages.
FFMPEG_BIN = configured_ffmpeg or shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()

router = Router()

# One pending upload per user; it is held only in memory and is not stored permanently.
pending_media: dict[int, tuple[str, str]] = {}
pending_video: dict[int, tuple[str, str]] = {}
pending_media_titles: dict[tuple[int, int], str] = {}
search_sessions: dict[int, tuple[str, list[tuple[str, str]], int]] = {}
last_request_at: dict[int, float] = {}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm", ".avi", ".mkv"}
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_MB", "18")) * 1024 * 1024
HISTORY_DB = Path(os.getenv("HISTORY_DB", "history.sqlite3"))
USER_COOLDOWN_SECONDS = max(2, int(os.getenv("USER_COOLDOWN_SECONDS", "3")))


ALLOWED_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm"}


def get_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎙️ BEKIUZ intro + outro", callback_data="fx:bekiuz")],
            [
                InlineKeyboardButton(text="🌌 Echo", callback_data="fx:echo"),
                InlineKeyboardButton(text="🏛️ Reverb", callback_data="fx:reverb"),
            ],
        ]
    )


def get_promo_keyboard() -> InlineKeyboardMarkup:
    """Share the bot or open Telegram's add-to-group/channel flow."""
    if not BOT_USERNAME:
        share_url = "https://t.me/share/url?" + urlencode(
            {"text": "Bekiuz Media Bot — media va qo‘shiqlar qidiruvi"}
        )
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="↗️ Botni ulashish", url=share_url)]]
        )

    bot_url = f"https://t.me/{BOT_USERNAME}"
    share_url = "https://t.me/share/url?" + urlencode({
        "url": bot_url,
        "text": "Bekiuz Media Bot — YouTube, Instagram media va qo‘shiqlar qidiruvi",
    })
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↗️ Botni ulashish", url=share_url)],
            [
                InlineKeyboardButton(
                    text="➕ Guruhga qo‘shish",
                    url=f"{bot_url}?startgroup=true",
                ),
                InlineKeyboardButton(
                    text="📢 Kanalga qo‘shish",
                    url=f"{bot_url}?startchannel=true",
                ),
            ],
        ]
    )


def get_media_actions_keyboard() -> InlineKeyboardMarkup:
    promo_rows = get_promo_keyboard().inline_keyboard
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎵 Musiqasini qidirish", callback_data="media:music")],
            *promo_rows,
        ]
    )


def get_search_keyboard(user_id: int) -> InlineKeyboardMarkup:
    session = search_sessions.get(user_id)
    if session is None:
        return InlineKeyboardMarkup(inline_keyboard=[])

    _, results, offset = session
    rows: list[list[InlineKeyboardButton]] = []
    page_end = min(offset + 10, len(results))
    for index in range(offset, page_end, 5):
        rows.append([
            InlineKeyboardButton(
                text=str(item_index - offset + 1),
                callback_data=f"song:{item_index}",
            )
            for item_index in range(index, min(index + 5, page_end))
        ])

    navigation: list[InlineKeyboardButton] = []
    if offset > 0:
        navigation.append(
            InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"songpage:{max(0, offset - 10)}")
        )
    if page_end < len(results):
        navigation.append(
            InlineKeyboardButton(text="Keyingisi ➡️", callback_data=f"songpage:{page_end}")
        )
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    previous = last_request_at.get(user_id, 0.0)
    if now - previous < USER_COOLDOWN_SECONDS:
        return True
    last_request_at[user_id] = now
    return False


def record_history(user_id: int, action: str, title: str) -> None:
    """Keep metadata only; downloaded files themselves are not stored in the history."""
    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, action TEXT NOT NULL, "
            "title TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO history(user_id, action, title, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, title[:240], time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())),
        )
        connection.execute(
            "DELETE FROM history WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 50)",
            (user_id, user_id),
        )


def read_history(user_id: int, limit: int = 10) -> list[tuple[str, str, str]]:
    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, action TEXT NOT NULL, "
            "title TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        return connection.execute(
            "SELECT action, title, created_at FROM history "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()


def set_daily_subscription(user_id: int, enabled: bool) -> None:
    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS daily_subscribers ("
            "user_id INTEGER PRIMARY KEY, subscribed_at TEXT NOT NULL)"
        )
        if enabled:
            connection.execute(
                "INSERT OR IGNORE INTO daily_subscribers(user_id, subscribed_at) VALUES (?, ?)",
                (user_id, datetime.now(BOT_TIMEZONE).isoformat(timespec="seconds")),
            )
        else:
            connection.execute("DELETE FROM daily_subscribers WHERE user_id = ?", (user_id,))


def get_daily_subscribers() -> list[int]:
    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS daily_subscribers ("
            "user_id INTEGER PRIMARY KEY, subscribed_at TEXT NOT NULL)"
        )
        return [row[0] for row in connection.execute("SELECT user_id FROM daily_subscribers").fetchall()]


def fetch_youtube_top10() -> list[dict[str, str]]:
    """Return the current popular music-category videos in the configured country."""
    if not YOUTUBE_API_KEY:
        raise RuntimeError(
            "YouTube API kaliti sozlanmagan. Hostingga YOUTUBE_API_KEY o‘zgaruvchisini qo‘shish kerak."
        )
    params = urlencode({
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": YOUTUBE_REGION,
        "videoCategoryId": "10",
        "maxResults": "10",
        "key": YOUTUBE_API_KEY,
    })
    request = Request(
        f"https://www.googleapis.com/youtube/v3/videos?{params}",
        headers={"Accept": "application/json", "User-Agent": "BekiuzMediaBot/1.0"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("YouTube chart request failed: %s", exc)
        raise RuntimeError(
            "YouTube TOP 10 ma’lumotini olib bo‘lmadi. API kaliti va limitini tekshiring."
        ) from exc

    result: list[dict[str, str]] = []
    for item in payload.get("items", []):
        video_id = item.get("id")
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        stats = item.get("statistics") or {}
        result.append({
            "title": str(snippet.get("title") or "Nomsiz video"),
            "channel": str(snippet.get("channelTitle") or "Noma’lum kanal"),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "views": str(stats.get("viewCount") or ""),
        })
    if not result:
        raise RuntimeError("O‘zbekiston uchun YouTube musiqa chartidan hozir natija kelmadi.")
    return result[:10]


def format_youtube_top10(items: list[dict[str, str]]) -> str:
    today = datetime.now(BOT_TIMEZONE).strftime("%d.%m.%Y")
    lines = [
        f"🎵 <b>YouTube musiqa TOP 10 — O‘zbekiston</b>",
        f"📅 {today}",
        "",
    ]
    for index, item in enumerate(items, 1):
        title = escape(item["title"])
        channel = escape(item["channel"])
        url = escape(item["url"], quote=True)
        lines.append(f'{index}. <a href="{url}">{title}</a>')
        lines.append(f"   🎤 {channel}")
    lines.extend([
        "",
        "🎧 Tinglash uchun nomini bosing.",
        "ℹ️ Bu YouTube’ning musiqa toifasidagi mashhur videolari; reyting o‘zgarib turadi.",
        "📥 Yuklab olishni faqat muallif yoki platforma ruxsat bergan usulda bajaring.",
    ])
    return "\n".join(lines)


def get_video_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎵 Videodan MP3 ajratish", callback_data="vid:mp3")],
            [InlineKeyboardButton(text="🗜️ Videoni siqish", callback_data="vid:compress")],
        ]
    )


async def process_video_file(source: Path, output: Path, mode: str) -> None:
    if mode == "mp3":
        args = [
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:a:0", "-vn",
            "-codec:a", "libmp3lame", "-b:a", "192k",
            str(output),
        ]
    elif mode == "compress":
        args = [
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
            "-vf", "scale='min(1280,iw)':-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
            str(output),
        ]
    else:
        raise RuntimeError("Noma'lum video amali.")

    await run_ffmpeg(args)


async def make_video_output(bot: Bot, file_id: str, filename: str, mode: str) -> tuple[bytes, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        suffix = ".mp4"
    with tempfile.TemporaryDirectory(prefix="bekiuz-video-") as temp_dir:
        folder = Path(temp_dir)
        source = folder / f"source{suffix}"
        output = folder / ("bekiuz-audio.mp3" if mode == "mp3" else "bekiuz-compressed.mp4")

        telegram_file = await bot.get_file(file_id)
        if not telegram_file.file_path:
            raise RuntimeError("Telegram video fayl manzilini qaytarmadi.")
        await bot.download_file(telegram_file.file_path, destination=source)

        if not source.exists() or source.stat().st_size == 0:
            raise RuntimeError("Yuklangan video bo‘sh chiqdi.")
        if source.stat().st_size > MAX_VIDEO_BYTES:
            raise RuntimeError(
                f"Video {MAX_VIDEO_BYTES // (1024 * 1024)} MB limitidan katta."
            )

        await process_video_file(source, output, mode)
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("Natija fayli yaratilmadi.")
        if output.stat().st_size > 45 * 1024 * 1024:
            raise RuntimeError("Natija Telegram yuborish limiti uchun juda katta.")
        return output.read_bytes(), output.name


def get_audio_details(message: Message) -> tuple[str, str, int] | None:
    """Return (Telegram file id, suggested filename, size) for supported audio."""
    if message.audio:
        item = message.audio
        return item.file_id, item.file_name or "audio.mp3", item.file_size or 0

    if message.voice:
        item = message.voice
        return item.file_id, "voice.ogg", item.file_size or 0

    if message.document:
        item = message.document
        name = item.file_name or "audio"
        mime = (item.mime_type or "").lower()
        if Path(name).suffix.lower() in ALLOWED_SUFFIXES or mime.startswith("audio/"):
            return item.file_id, name, item.file_size or 0

    return None


async def create_bekiuz_tag(destination: Path) -> None:
    """Create the short spoken Bekiuz tag using the configured neural voice."""
    voice = TTS_VOICE or "uz-UZ-SardorNeural"
    speech = edge_tts.Communicate(
        text="Bekiuz",
        voice=voice,
        rate="-10%",
        volume="+12%",
        pitch="-10Hz",
    )
    await speech.save(str(destination))


async def run_ffmpeg(args: list[str]) -> None:
    if not shutil.which(FFMPEG_BIN):
        raise RuntimeError("FFmpeg topilmadi. imageio-ffmpeg paketi o‘rnatilganini tekshiring.")

    process = await asyncio.create_subprocess_exec(
        FFMPEG_BIN,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        details = stderr.decode("utf-8", errors="replace")[-1200:]
        logger.error("FFmpeg failed: %s", details)
        raise RuntimeError("Audio qayta ishlanmadi. Fayl formati yoki audio trekni tekshiring.")


async def process_audio(source: Path, output: Path, mode: str, tag_path: Path | None = None) -> None:
    common = (
        "aresample=44100,"
        "aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo"
    )

    if mode == "bekiuz":
        if tag_path is None:
            raise RuntimeError("BEKIUZ ovozi tayyorlanmadi.")

        graph = (
            f"[1:a]{common},asplit=2[tag_a][tag_b];"
            "[tag_a]highpass=f=90,lowpass=f=9000,volume=1.2,"
            "aecho=0.8:0.88:100|210|330|460:0.45|0.34|0.23|0.14,"
            "apad=pad_dur=4,atrim=duration=5,"
            "afade=t=out:st=3.3:d=1.7,asetpts=PTS-STARTPTS[intro];"
            "[tag_b]highpass=f=90,lowpass=f=9000,volume=1.2,"
            "aecho=0.8:0.88:100|210|330|460:0.45|0.34|0.23|0.14,"
            "apad=pad_dur=4,atrim=duration=5,"
            "afade=t=out:st=3.3:d=1.7,asetpts=PTS-STARTPTS[outro];"
            f"[0:a]{common},loudnorm=I=-16:TP=-1.5:LRA=11,"
            "asetpts=PTS-STARTPTS[song];"
            "[intro][song][outro]concat=n=3:v=0:a=1[out]"
        )
        args = [
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-i", str(tag_path),
            "-filter_complex", graph,
            "-map", "[out]", "-vn",
            "-codec:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            str(output),
        ]
    else:
        if mode == "echo":
            effect = "aecho=0.8:0.88:110|220|330:0.35|0.26|0.18,alimiter=limit=0.95"
        elif mode == "reverb":
            # A layered echo filter provides a spacious, reverb-like effect without an extra plugin.
            effect = "aecho=0.75:0.72:45|90|135|180:0.26|0.21|0.16|0.12,alimiter=limit=0.95"
        else:
            raise RuntimeError("Noma'lum effekt tanlandi.")

        audio_filter = f"{common},loudnorm=I=-16:TP=-1.5:LRA=11,{effect}"
        args = [
            "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-vn",
            "-af", audio_filter,
            "-codec:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            str(output),
        ]

    await run_ffmpeg(args)


async def make_processed_audio(bot: Bot, file_id: str, filename: str, mode: str) -> bytes:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        suffix = ".audio"

    with tempfile.TemporaryDirectory(prefix="bekiuz-music-") as temp_dir:
        folder = Path(temp_dir)
        source = folder / f"source{suffix}"
        output = folder / "bekiuz-output.mp3"
        tag = folder / "bekiuz-tag.mp3"

        telegram_file = await bot.get_file(file_id)
        if not telegram_file.file_path:
            raise RuntimeError("Telegram audio fayl manzilini qaytarmadi.")

        await bot.download_file(telegram_file.file_path, destination=source)

        if not source.exists() or source.stat().st_size == 0:
            raise RuntimeError("Yuklangan audio bo'sh chiqdi.")
        if source.stat().st_size > MAX_INPUT_BYTES:
            raise RuntimeError(f"Audio {MAX_INPUT_BYTES // (1024 * 1024)} MB limitidan katta.")

        tag_path: Path | None = None
        if mode == "bekiuz":
            await create_bekiuz_tag(tag)
            tag_path = tag

        await process_audio(source, output, mode, tag_path)
        if not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("MP3 natija yaratilmadi.")

        return output.read_bytes()


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await message.answer(
        "🎵 <b>Bekiuz Media Bot</b> ga xush kelibsiz!\n\n"
        "📥 YouTube va ochiq Instagram havolasini yuboring — video, rasm yoki audio yuklab olaman.\n"
        "🎶 Qo‘shiq nomini yozing — 1–10 natijadan birini tanlab MP3 yuklab oling.\n"
        "🎧 Audio yuboring — BEKIUZ intro + outro, Echo yoki Reverb.\n"
        "🎬 Video yuboring — MP3 ajratish yoki videoni siqish.\n"
        "🕘 /history — oxirgi ishlaringiz.\n"
        "🔥 /top10 — YouTube musiqa TOP 10.\n"
        "🔔 /daily_on — kunlik TOP 10 xabarlariga obuna.\n"
        "🔕 /daily_off — kunlik xabarlarni o‘chirish.\n\n"
        "Havola limiti: 35 MB; audio: 8 MB; video: 18 MB.\n"
        "Faqat ochiq va yuklab olishga ruxsat berilgan kontent.\n",
        parse_mode="HTML",
        reply_markup=get_promo_keyboard(),
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Foydalanish:\n"
        "• Ochiq YouTube yoki Instagram havolasini yuboring — video/rasm yuklab olaman.\n"
        "• Qo‘shiq nomini yozing — 1–10 natijadan raqamni bosing, MP3 yuboriladi.\n"
        "• Keyingi 10 ta natija uchun “Keyingisi” tugmasini bosing.\n"
        "• Audio yuboring va BEKIUZ, Echo yoki Reverb tanlang.\n"
        "• Video yuboring va MP3 ajratish yoki siqishni tanlang.\n\n"
        "/search nom yoki qisqa matn — qidiruv.\n"
        "/history — oxirgi 10 ta amal.\n"
        "/top10 — hozirgi YouTube musiqa TOP 10.\n"
        "/daily_on — har kungi TOP 10 xabarlari.\n"
        "/daily_off — kunlik xabarlarni o‘chirish.\n"
        "/cancel — tanlangan faylni bekor qilish.",
        reply_markup=get_promo_keyboard(),
    )


@router.message(Command("top10"))
async def top10_handler(message: Message) -> None:
    status = await message.answer("🎵 O‘zbekiston bo‘yicha YouTube musiqa TOP 10 olinmoqda…")
    try:
        items = await asyncio.to_thread(fetch_youtube_top10)
        await status.edit_text(
            format_youtube_top10(items),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        if message.from_user:
            await asyncio.to_thread(record_history, message.from_user.id, "top10", "YouTube music chart")
    except Exception as exc:
        await status.edit_text(f"❌ {escape(str(exc)[:350])}")


@router.message(Command("daily_on"))
async def daily_on_handler(message: Message) -> None:
    if not message.from_user:
        return
    if not YOUTUBE_API_KEY:
        await message.answer(
            "Kunlik TOP 10 uchun hostingda YOUTUBE_API_KEY sozlanishi kerak. "
            "API kalitini menga yubormang — faqat hostingdagi maxfiy Environment o‘zgaruvchisiga kiriting."
        )
        return
    await asyncio.to_thread(set_daily_subscription, message.from_user.id, True)
    await message.answer(
        "✅ Obuna yoqildi! Har kuni soat 09:00 da (Toshkent vaqti) YouTube musiqa TOP 10 havolalarini yuboraman.\n"
        "To‘xtatish: /daily_off"
    )


@router.message(Command("daily_off"))
async def daily_off_handler(message: Message) -> None:
    if not message.from_user:
        return
    await asyncio.to_thread(set_daily_subscription, message.from_user.id, False)
    await message.answer("🔕 Kunlik TOP 10 xabarlari o‘chirildi.")


@router.message(Command("history"))
async def history_handler(message: Message) -> None:
    if not message.from_user:
        return
    rows = await asyncio.to_thread(read_history, message.from_user.id, 10)
    if not rows:
        await message.answer("🕘 Hali tarix bo‘sh. Fayl yuklang yoki qidiruv qiling.")
        return
    labels = {
        "download": "📥 Yuklab olindi",
        "video_mp3": "🎵 Videodan MP3",
        "compress": "🗜️ Video siqildi",
        "audio_bekiuz": "🎙️ BEKIUZ intro/outro",
        "audio_echo": "🌌 Echo",
        "audio_reverb": "🏛️ Reverb",
        "search": "🔎 Qidiruv",
    }
    lines = ["🕘 <b>Oxirgi amallaringiz</b>", ""]
    for action, title, created_at in rows:
        lines.append(f"{labels.get(action, action)} — {escape(title)}")
        lines.append(f"<i>{created_at} UTC</i>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("cancel"))
async def cancel_handler(message: Message) -> None:
    if message.from_user:
        pending_media.pop(message.from_user.id, None)
        pending_video.pop(message.from_user.id, None)
    await message.answer("Bekor qilindi. Yangi audio yuborishingiz mumkin.")


def extract_url(text: str) -> str | None:
    match = re.search(r"https?://\S+", text)
    if not match:
        return None
    return match.group(0).rstrip(".,!?)]}>")


def is_supported_public_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in SUPPORTED_DOMAINS)


def download_public_media(url: str) -> tuple[str, bytes, str]:
    """Download one publicly accessible media item, without cookies or login bypass."""
    with tempfile.TemporaryDirectory(prefix="bekiuz-link-") as temp_dir:
        folder = Path(temp_dir)
        options = {
            "outtmpl": str(folder / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "retries": 1,
            "extractor_retries": 1,
            "max_filesize": MAX_DOWNLOAD_BYTES,
            "format": f"best[filesize<{MAX_DOWNLOAD_BYTES}]/best",
            "ffmpeg_location": FFMPEG_BIN,
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
        except Exception as exc:
            logger.info("Public media download failed: %s", exc)
            raise RuntimeError(
                "Havola ochilmadi. Post ochiq ekanini va platforma login talab qilmasligini tekshiring."
            ) from exc

        allowed = {".mp4", ".m4v", ".webm", ".mov", ".mp3", ".m4a", ".wav", ".ogg", ".jpg", ".jpeg", ".png", ".webp"}
        candidates = [
            item for item in folder.iterdir()
            if item.is_file() and item.suffix.lower() in allowed and item.stat().st_size > 0
        ]
        if not candidates:
            raise RuntimeError("Havoladan qo‘llab-quvvatlanadigan media fayli topilmadi.")

        media_file = max(candidates, key=lambda item: item.stat().st_mtime)
        if media_file.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise RuntimeError(f"Fayl {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB limitidan katta.")

        title = str((info or {}).get("title") or media_file.stem)
        return media_file.name, media_file.read_bytes(), title


async def send_downloaded_media(message: Message, filename: str, media_bytes: bytes, title: str) -> None:
    """Send images, videos, or audio in their native Telegram media type where possible."""
    suffix = Path(filename).suffix.lower()
    caption = f"📥 {title[:700]}\n\n✅ Bekiuz Bot orqali yuklab olindi"
    is_video = suffix in VIDEO_SUFFIXES
    markup = get_media_actions_keyboard() if is_video else get_promo_keyboard()
    upload = BufferedInputFile(media_bytes, filename=filename)

    if suffix in {".jpg", ".jpeg", ".png", ".webp"} and len(media_bytes) <= 10 * 1024 * 1024:
        sent = await message.answer_photo(photo=upload, caption=caption, reply_markup=markup)
    elif suffix in {".mp4", ".m4v"}:
        sent = await message.answer_video(
            video=upload,
            caption=caption,
            reply_markup=markup,
            supports_streaming=True,
        )
    elif suffix in {".mp3", ".m4a", ".ogg", ".wav"}:
        sent = await message.answer_audio(
            audio=upload,
            title=title[:64],
            caption=caption,
            reply_markup=markup,
        )
    else:
        sent = await message.answer_document(
            document=upload,
            caption=caption,
            reply_markup=markup,
        )

    if is_video:
        pending_media_titles[(sent.chat.id, sent.message_id)] = title[:180]
        if len(pending_media_titles) > 500:
            pending_media_titles.pop(next(iter(pending_media_titles)))


def download_song_mp3(url: str) -> tuple[bytes, str]:
    """Download a selected public YouTube result and convert its audio to MP3."""
    with tempfile.TemporaryDirectory(prefix="bekiuz-song-") as temp_dir:
        folder = Path(temp_dir)
        options = {
            "outtmpl": str(folder / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 1,
            "extractor_retries": 1,
            "max_filesize": MAX_DOWNLOAD_BYTES,
            "format": "bestaudio/best",
            "ffmpeg_location": FFMPEG_BIN,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(url, download=True)
        except Exception as exc:
            logger.info("Song MP3 download failed: %s", exc)
            raise RuntimeError(
                "Bu qo‘shiqni MP3 qilib olib bo‘lmadi. Boshqa natijani tanlang yoki keyinroq urinib ko‘ring."
            ) from exc

        candidates = [
            item for item in folder.iterdir()
            if item.is_file() and item.suffix.lower() == ".mp3" and item.stat().st_size > 0
        ]
        if not candidates:
            raise RuntimeError("MP3 fayl yaratilmadi. Serverda FFmpeg borligini tekshiring.")
        audio_file = max(candidates, key=lambda item: item.stat().st_mtime)
        if audio_file.stat().st_size > 45 * 1024 * 1024:
            raise RuntimeError("MP3 Telegram yuborish limiti uchun juda katta.")
        title = str((info or {}).get("title") or audio_file.stem)
        return audio_file.read_bytes(), title


def search_music(query: str, limit: int = 20) -> list[tuple[str, str]]:
    """Search up to 20 YouTube results; display the results ten at a time."""
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "playlistend": limit,
        "socket_timeout": 20,
    }
    with yt_dlp.YoutubeDL(options) as searcher:
        result = searcher.extract_info(f"ytsearch{limit}:{query}", download=False)

    matches: list[tuple[str, str]] = []
    for entry in (result or {}).get("entries") or []:
        if not entry:
            continue
        title = str(entry.get("title") or "Nomsiz video")
        link = entry.get("webpage_url") or entry.get("original_url") or entry.get("url") or ""
        if link and not str(link).startswith(("http://", "https://")):
            link = f"https://www.youtube.com/watch?v={link}"
        if link:
            matches.append((title, str(link)))
    return matches


async def send_search_results(message: Message, query: str, user_id: int | None = None) -> None:
    query = query.strip()
    if not query:
        await message.answer("Qidirish uchun qo‘shiq nomi yoki esingizda qolgan qisqa so‘zlarni yozing.")
        return
    query = query[:180]

    if user_id is None and message.from_user:
        user_id = message.from_user.id
    if user_id is None:
        await message.answer("Qidiruvni shaxsiy chatda boshlang.")
        return

    status = await message.answer("🔎 YouTube’dan qo‘shiqlar qidirilyapti…")
    try:
        results = await asyncio.to_thread(search_music, query)
        if not results:
            await status.edit_text("Mos natija topilmadi. Boshqa so‘zlar bilan urinib ko‘ring.")
            return

        search_sessions[user_id] = (query, results, 0)
        if len(search_sessions) > 500:
            search_sessions.pop(next(iter(search_sessions)))
        if message.from_user and message.from_user.id == user_id:
            await asyncio.to_thread(record_history, user_id, "search", query)

        lines = [f"🎵 Qo‘shiq qidiruvi: {query}", ""]
        for index, (title, _) in enumerate(results[:10], 1):
            lines.append(f"{index}. {title[:150]}")
        lines.extend(["", "⬇️ MP3 yuklash uchun raqamni bosing."])
        await status.edit_text(
            "\n".join(lines),
            reply_markup=get_search_keyboard(user_id),
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Song search failed")
        await status.edit_text("Qidiruv vaqtincha ishlamadi. Birozdan keyin yana urinib ko‘ring.")


@router.callback_query(F.data == "media:music")
async def media_music_search_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    title = pending_media_titles.get((callback.message.chat.id, callback.message.message_id))
    if not title:
        await callback.answer("Bu video uchun qidiruv ma’lumoti topilmadi.", show_alert=True)
        return
    await callback.answer("Video nomi asosida musiqa qidiriladi")
    await send_search_results(callback.message, title, user_id=callback.from_user.id)


@router.callback_query(F.data.startswith("songpage:"))
async def song_page_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    session = search_sessions.get(callback.from_user.id)
    if not session:
        await callback.answer("Avval qo‘shiq qidiring.", show_alert=True)
        return

    query, results, _ = session
    try:
        offset = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Sahifa raqami noto‘g‘ri.", show_alert=True)
        return
    if offset < 0 or offset >= len(results):
        await callback.answer("Boshqa natija yo‘q.", show_alert=True)
        return

    search_sessions[callback.from_user.id] = (query, results, offset)
    lines = [f"🎵 Qo‘shiq qidiruvi: {query}", ""]
    page = results[offset:offset + 10]
    for index, (title, _) in enumerate(page, 1):
        lines.append(f"{index}. {title[:150]}")
    lines.extend(["", "⬇️ MP3 yuklash uchun raqamni bosing."])
    await callback.answer()
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=get_search_keyboard(callback.from_user.id),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("song:"))
async def song_download_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    session = search_sessions.get(callback.from_user.id)
    if not session:
        await callback.answer("Avval qo‘shiq qidiring.", show_alert=True)
        return

    _, results, offset = session
    try:
        index = int((callback.data or "").split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.answer("Natija raqami noto‘g‘ri.", show_alert=True)
        return
    if index < offset or index >= min(offset + 10, len(results)):
        await callback.answer("Bu tugma eskirgan. Qayta qidiring.", show_alert=True)
        return

    title, url = results[index]
    await callback.answer("MP3 tayyorlanmoqda…")
    await callback.message.edit_text(f"⏳ MP3 tayyorlanmoqda: {title[:180]}")
    try:
        audio_bytes, downloaded_title = await asyncio.to_thread(download_song_mp3, url)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", downloaded_title).strip("._-")[:60]
        filename = (safe_name or "bekiuz-song") + ".mp3"
        await callback.message.answer_audio(
            audio=BufferedInputFile(audio_bytes, filename=filename),
            title=downloaded_title[:64],
            caption=f"🎵 {downloaded_title[:700]}\n✅ Bekiuz Bot orqali yuklab olindi",
            reply_markup=get_promo_keyboard(),
        )
        await asyncio.to_thread(record_history, callback.from_user.id, "download", downloaded_title)
        await callback.message.edit_text(f"✅ MP3 yuborildi: {downloaded_title[:180]}")
    except Exception as exc:
        logger.exception("Song MP3 download failed for user %s", callback.from_user.id)
        await callback.message.edit_text(f"❌ {str(exc)[:400]}")


@router.message(Command("search", "song"))
async def search_command(message: Message) -> None:
    if message.from_user and is_rate_limited(message.from_user.id):
        await message.answer("Biroz kuting — so‘rovlar orasida 3 soniya tanaffus bo‘lishi kerak.")
        return
    query = (message.text or "").partition(" ")[2].strip()
    await send_search_results(message, query)


@router.message(F.text)
async def text_handler(message: Message) -> None:
    text = (message.text or "").strip()
    if not text or text.startswith("/"):
        return
    if message.from_user and is_rate_limited(message.from_user.id):
        await message.answer("Biroz kuting — so‘rovlar orasida 3 soniya tanaffus bo‘lishi kerak.")
        return

    url = extract_url(text)
    if url:
        if "suno.com/" in url.lower():
            await message.answer(
                "Suno havolasidan to‘g‘ridan-to‘g‘ri olish bu botda qo‘llanmaydi. "
                "Suno ruxsat bergan Download → MP3 usulidan foydalaning."
            )
            return
        if not is_supported_public_url(url):
            await message.answer(
                "Bu platforma hozir qo‘llab-quvvatlanmaydi. Ochiq Instagram, TikTok, YouTube, "
                "Facebook, X, Pinterest, Reddit, Vimeo yoki SoundCloud havolasini yuboring."
            )
            return

        status = await message.answer("📥 Ochiq havoladagi media tekshirilmoqda…")
        try:
            filename, media_bytes, title = await asyncio.to_thread(download_public_media, url)
            await status.edit_text(f"✅ Topildi: {title[:180]}\nMedia yuborilyapti…")
            await send_downloaded_media(message, filename, media_bytes, title)
            if message.from_user:
                await asyncio.to_thread(record_history, message.from_user.id, "download", title)
            await status.delete()
        except Exception as exc:
            logger.exception("Link download failed")
            await status.edit_text(f"❌ {str(exc)[:500]}")
        return

    await send_search_results(message, text)


@router.message(F.audio | F.voice | F.video | F.document)
async def audio_handler(message: Message) -> None:
    if not message.from_user:
        return
    if is_rate_limited(message.from_user.id):
        await message.answer("Biroz kuting — so‘rovlar orasida 3 soniya tanaffus bo‘lishi kerak.")
        return

    video_file_id: str | None = None
    video_filename = "video.mp4"
    video_size = 0

    if message.video:
        video_file_id = message.video.file_id
        video_filename = getattr(message.video, "file_name", None) or "video.mp4"
        video_size = message.video.file_size or 0
    elif message.document:
        item = message.document
        candidate_name = item.file_name or "file"
        if Path(candidate_name).suffix.lower() in VIDEO_SUFFIXES or (item.mime_type or "").lower().startswith("video/"):
            video_file_id = item.file_id
            video_filename = candidate_name
            video_size = item.file_size or 0

    if video_file_id:
        if video_size and video_size > MAX_VIDEO_BYTES:
            await message.answer(f"Video katta. Hozircha {MAX_VIDEO_BYTES // (1024 * 1024)} MB gacha video yuboring.")
            return
        pending_video[message.from_user.id] = (video_file_id, video_filename)
        await message.answer(
            f"✅ <b>{escape(video_filename)}</b> qabul qilindi. Nima qilay?",
            reply_markup=get_video_keyboard(),
            parse_mode="HTML",
        )
        return

    details = get_audio_details(message)
    if details is None:
        await message.answer("Audio yoki video fayl yuboring. Qo‘llab-quvvatlanadigan formatlarni /help orqali ko‘ring.")
        return

    file_id, filename, file_size = details
    if file_size and file_size > MAX_INPUT_BYTES:
        await message.answer(f"Audio katta. Hozircha {MAX_INPUT_BYTES // (1024 * 1024)} MB gacha audio yuboring.")
        return

    pending_media[message.from_user.id] = (file_id, filename)
    await message.answer(
        f"✅ <b>{escape(filename)}</b> qabul qilindi. Qaysi effektni qo‘shay?",
        reply_markup=get_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("vid:"))
async def video_action_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    stored = pending_video.get(callback.from_user.id)
    if stored is None:
        await callback.answer("Avval video yuboring.", show_alert=True)
        return

    mode = (callback.data or "").split(":", 1)[1]
    if mode not in {"mp3", "compress"}:
        await callback.answer("Noma‘lum video amali.", show_alert=True)
        return

    file_id, filename = stored
    await callback.answer("Qayta ishlash boshlandi")
    await callback.message.edit_text("⏳ Video qayta ishlanmoqda…")
    try:
        output_bytes, output_name = await make_video_output(callback.bot, file_id, filename, mode)
        if mode == "mp3":
            await callback.message.answer_audio(
                audio=BufferedInputFile(output_bytes, filename=output_name),
                title=Path(filename).stem[:64] or "Audio",
                caption="🎵 Videodan MP3 ajratildi.\n✅ Bekiuz Bot orqali tayyorlandi",
            )
            history_action = "video_mp3"
        else:
            await callback.message.answer_document(
                document=BufferedInputFile(output_bytes, filename=output_name),
                caption="🗜️ Video siqildi.\n✅ Bekiuz Bot orqali tayyorlandi",
            )
            history_action = "compress"
        await asyncio.to_thread(record_history, callback.from_user.id, history_action, filename)
    except Exception as exc:
        logger.exception("Video processing failed for user %s", callback.from_user.id)
        await callback.message.answer(f"❌ Video qayta ishlanmadi: {str(exc)[:350]}")
    finally:
        pending_video.pop(callback.from_user.id, None)


@router.callback_query(F.data.startswith("fx:"))
async def effect_handler(callback: CallbackQuery) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    user_id = callback.from_user.id
    stored = pending_media.get(user_id)
    if stored is None:
        await callback.answer("Avval audio fayl yuboring.", show_alert=True)
        return

    mode = (callback.data or "").split(":", 1)[1]
    if mode not in {"bekiuz", "echo", "reverb"}:
        await callback.answer("Noma‘lum effekt.", show_alert=True)
        return

    file_id, filename = stored
    await callback.answer("Qayta ishlash boshlandi")
    await callback.message.edit_text("⏳ Audio qayta ishlanmoqda. Tayyor bo‘lgach MP3 yuboraman.")

    try:
        audio_bytes = await make_processed_audio(callback.bot, file_id, filename, mode)
        if mode == "bekiuz":
            caption = "🎙️ BEKIUZ intro + outro tayyor. Aks-sado bilan MP3."
        elif mode == "echo":
            caption = "🌌 Echo effekti tayyor. MP3 fayl."
        else:
            caption = "🏛️ Reverb effekti tayyor. MP3 fayl."

        await callback.message.answer_audio(
            audio=BufferedInputFile(audio_bytes, filename="bekiuz-mix.mp3"),
            title="BEKIUZ Mix",
            caption=f"{caption}\n✅ Bekiuz Bot orqali tayyorlandi",
        )
        await asyncio.to_thread(record_history, user_id, f"audio_{mode}", filename)
    except Exception as exc:
        logger.exception("Audio processing failed for user %s", user_id)
        await callback.message.answer(
            "❌ Audio tayyorlashda xatolik bo‘ldi. Faylni yana yuborib ko‘ring.\n"
            f"Sabab: {str(exc)[:300]}"
        )
    finally:
        pending_media.pop(user_id, None)


async def daily_top10_loop(bot: Bot) -> None:
    """Send the chart to users who explicitly opted in, every day at 09:00 Tashkent."""
    while True:
        now = datetime.now(BOT_TIMEZONE)
        next_run = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep(max(1, (next_run - now).total_seconds()))

        try:
            subscribers = await asyncio.to_thread(get_daily_subscribers)
            if not subscribers:
                continue
            items = await asyncio.to_thread(fetch_youtube_top10)
            text = format_youtube_top10(items)
            for user_id in subscribers:
                try:
                    await bot.send_message(
                        user_id,
                        text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except Exception:
                    logger.exception("Daily chart send failed for user %s", user_id)
        except Exception:
            logger.exception("Daily YouTube top 10 job failed")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. Hosting sozlamalarida BOT_TOKEN environment variable kiriting."
        )
    if not shutil.which(FFMPEG_BIN):
        raise RuntimeError("FFmpeg topilmadi. Dockerfile orqali ishga tushiring yoki FFmpeg o'rnating.")

    bot = Bot(token=BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username or ""
    logger.info("Bekiuz Music Bot ishga tushmoqda (@%s).", BOT_USERNAME or "username-yo‘q")
    scheduler_task = asyncio.create_task(daily_top10_loop(bot))
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
