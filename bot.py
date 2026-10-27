from __future__ import annotations

import asyncio
import logging
from html import escape
import os
import shutil
import tempfile
from pathlib import Path

import edge_tts
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
TTS_VOICE = os.getenv("TTS_VOICE", "uz-UZ-SardorNeural").strip()
MAX_INPUT_BYTES = int(os.getenv("MAX_INPUT_MB", "20")) * 1024 * 1024
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

router = Router()

# One pending upload per user; it is held only in memory and is not stored permanently.
pending_media: dict[int, tuple[str, str]] = {}

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
        raise RuntimeError("FFmpeg topilmadi. Hosting Dockerfile orqali ishga tushirilganini tekshiring.")

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
        "🎵 <b>Bekiuz Music Bot</b> ga xush kelibsiz!\n\n"
        "MP3, WAV, M4A yoki ovozli xabar yuboring. Men uni qayta ishlash uchun "
        "sizga effekt tanlash imkonini beraman.\n\n"
        "• <b>BEKIUZ intro + outro</b> — boshida va oxirida chuqur ovoz hamda aks-sado.\n"
        "• <b>Echo</b> — musiqaga takroriy aks-sado.\n"
        "• <b>Reverb</b> — kengroq, fazoviy effekt.\n\n"
        "Hozirgi limit: bitta audio uchun 20 MB.\n"
        "Qo'shiq havolasini avtomatik yuklab olish bu versiyada yo'q; audio faylni o'zingiz yuklang.",
        parse_mode="HTML",
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Foydalanish:\n"
        "1. Audio faylni botga yuboring.\n"
        "2. Kerakli effekt tugmasini bosing.\n"
        "3. Tayyor MP3 faylni yuklab oling.\n\n"
        "Yangi audio yuborsangiz, boshqa effekt tanlashingiz mumkin. Bekor qilish uchun /cancel yozing."
    )


@router.message(Command("cancel"))
async def cancel_handler(message: Message) -> None:
    if message.from_user:
        pending_media.pop(message.from_user.id, None)
    await message.answer("Bekor qilindi. Yangi audio yuborishingiz mumkin.")


@router.message(F.audio | F.voice | F.document)
async def audio_handler(message: Message) -> None:
    if not message.from_user:
        return

    details = get_audio_details(message)
    if details is None:
        await message.answer("Bu hujjat audio faylga o'xshamaydi. MP3, WAV, M4A, OGG yoki FLAC yuboring.")
        return

    file_id, filename, file_size = details
    if file_size and file_size > MAX_INPUT_BYTES:
        await message.answer(f"Fayl katta. Hozircha {MAX_INPUT_BYTES // (1024 * 1024)} MB gacha audio yuboring.")
        return

    pending_media[message.from_user.id] = (file_id, filename)
    await message.answer(
        f"✅ <b>{escape(filename)}</b> qabul qilindi. Qaysi effektni qo'shay?",
        reply_markup=get_keyboard(),
        parse_mode="HTML",
    )


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
        await callback.answer("Noma'lum effekt.", show_alert=True)
        return

    file_id, filename = stored
    await callback.answer("Qayta ishlash boshlandi")
    await callback.message.edit_text("⏳ Audio qayta ishlanmoqda. Tayyor bo'lgach MP3 yuboraman.")

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
            caption=caption,
        )
    except Exception as exc:
        logger.exception("Audio processing failed for user %s", user_id)
        await callback.message.answer(
            "❌ Audio tayyorlashda xatolik bo'ldi. Faylni yana yuborib ko'ring.\n"
            f"Sabab: {str(exc)[:300]}"
        )
    finally:
        pending_media.pop(user_id, None)


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

    logger.info("Bekiuz Music Bot ishga tushmoqda.")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
