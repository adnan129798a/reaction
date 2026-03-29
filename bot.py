import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_DIR = Path("data")
BASE_DIR.mkdir(exist_ok=True)

# =========================
# إعدادات افتراضية
# =========================
DEFAULT_TEMPLATE = "stack"
DEFAULT_AUDIO_MODE = "mix"
DEFAULT_REACTION_VOLUME = 1.0
DEFAULT_MAIN_VOLUME = 1.0
DEFAULT_EXPORT_WIDTH = 720
DEFAULT_EXPORT_HEIGHT = 1280
DEFAULT_WATERMARK = os.getenv("WATERMARK_TEXT", "")
DEFAULT_BRAND = os.getenv("BRAND_TEXT", "")
DEFAULT_PRESET = os.getenv("FFMPEG_PRESET", "veryfast")
DEFAULT_CRF = os.getenv("FFMPEG_CRF", "24")

# =========================
# تخزين حالة المستخدم
# =========================
user_data_store: Dict[int, Dict[str, Optional[str]]] = {}


# =========================
# أدوات مساعدة
# =========================
def ensure_user_session(user_id: int) -> None:
    if user_id not in user_data_store:
        user_data_store[user_id] = {
            "main": None,
            "reaction": None,
            "template": DEFAULT_TEMPLATE,
            "audio_mode": DEFAULT_AUDIO_MODE,
            "reaction_volume": DEFAULT_REACTION_VOLUME,
            "main_volume": DEFAULT_MAIN_VOLUME,
            "watermark": DEFAULT_WATERMARK,
            "brand": DEFAULT_BRAND,
        }



def get_user_dir(user_id: int) -> Path:
    user_dir = BASE_DIR / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir



def reset_user_files(user_id: int) -> None:
    user_dir = BASE_DIR / str(user_id)
    if user_dir.exists():
        shutil.rmtree(user_dir, ignore_errors=True)



def reset_user_session(user_id: int) -> None:
    user_data_store[user_id] = {
        "main": None,
        "reaction": None,
        "template": DEFAULT_TEMPLATE,
        "audio_mode": DEFAULT_AUDIO_MODE,
        "reaction_volume": DEFAULT_REACTION_VOLUME,
        "main_volume": DEFAULT_MAIN_VOLUME,
        "watermark": DEFAULT_WATERMARK,
        "brand": DEFAULT_BRAND,
    }



def run_ffmpeg(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or "FFmpeg failed")



def escape_ffmpeg_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("[", r"\[")
        .replace("]", r"\]")
        .replace(",", r"\,")
        .replace("%", r"\%")
    )



def build_video_filter(template: str, watermark: str, brand: str) -> str:
    width = DEFAULT_EXPORT_WIDTH
    height = DEFAULT_EXPORT_HEIGHT

    if template == "stack":
        base_filter = (
            "[1:v]scale={w}:420:force_original_aspect_ratio=increase,"
            "crop={w}:420[top];"
            "[0:v]scale={w}:860:force_original_aspect_ratio=increase,"
            "crop={w}:860[bottom];"
            "[top][bottom]vstack=inputs=2[vbase]"
        ).format(w=width)
    elif template == "pip":
        base_filter = (
            "[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            "pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black[bg];"
            "[1:v]scale=260:-1[react];"
            "[bg][react]overlay=(W-w)/2:40[vbase]"
        ).format(w=width, h=height)
    elif template == "side":
        base_filter = (
            "[1:v]scale=240:{h}:force_original_aspect_ratio=increase,"
            "crop=240:{h}[left];"
            "[0:v]scale=480:{h}:force_original_aspect_ratio=increase,"
            "crop=480:{h}[right];"
            "[left][right]hstack=inputs=2[vbase]"
        ).format(h=height)
    else:
        raise ValueError("Unknown template")

    draw_parts = []
    if brand:
        safe_brand = escape_ffmpeg_text(brand)
        draw_parts.append(
            "drawtext=text='{}':x=24:y=24:fontsize=32:fontcolor=white:box=1:boxcolor=black@0.35:boxborderw=12".format(safe_brand)
        )
    if watermark:
        safe_watermark = escape_ffmpeg_text(watermark)
        draw_parts.append(
            "drawtext=text='{}':x=w-tw-24:y=h-th-24:fontsize=26:fontcolor=white@0.85:box=1:boxcolor=black@0.25:boxborderw=10".format(safe_watermark)
        )

    if draw_parts:
        return base_filter + ";[vbase]" + ",".join(draw_parts) + "[v]"

    return base_filter.replace("[vbase]", "[v]")



def build_audio_filter(audio_mode: str, main_volume: float, reaction_volume: float) -> str:
    if audio_mode == "main_only":
        return "[0:a]volume={}[a]".format(main_volume)
    if audio_mode == "reaction_only":
        return "[1:a]volume={}[a]".format(reaction_volume)
    if audio_mode == "mix":
        return (
            "[0:a]volume={}[a0];"
            "[1:a]volume={}[a1];"
            "[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[a]"
        ).format(main_volume, reaction_volume)

    raise ValueError("Unknown audio mode")



def probe_has_audio(path: str) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and bool(result.stdout.strip())



def merge_videos(
    main_video: str,
    reaction_video: str,
    output_video: str,
    template: str,
    audio_mode: str,
    main_volume: float,
    reaction_volume: float,
    watermark: str,
    brand: str,
) -> None:
    has_main_audio = probe_has_audio(main_video)
    has_reaction_audio = probe_has_audio(reaction_video)

    if audio_mode == "mix" and not (has_main_audio and has_reaction_audio):
        if has_main_audio:
            audio_mode = "main_only"
        elif has_reaction_audio:
            audio_mode = "reaction_only"
        else:
            audio_mode = "none"

    if audio_mode == "main_only" and not has_main_audio:
        audio_mode = "reaction_only" if has_reaction_audio else "none"

    if audio_mode == "reaction_only" and not has_reaction_audio:
        audio_mode = "main_only" if has_main_audio else "none"

    video_filter = build_video_filter(template, watermark, brand)
    filter_parts = [video_filter]

    map_args = ["-map", "[v]"]

    if audio_mode != "none":
        filter_parts.append(build_audio_filter(audio_mode, main_volume, reaction_volume))
        map_args.extend(["-map", "[a]"])

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        main_video,
        "-i",
        reaction_video,
        "-filter_complex",
        ";".join(filter_parts),
        *map_args,
        "-c:v",
        "libx264",
        "-preset",
        DEFAULT_PRESET,
        "-crf",
        DEFAULT_CRF,
        "-pix_fmt",
        "yuv420p",
    ]

    if audio_mode != "none":
        cmd.extend([
            "-c:a",
            "aac",
            "-b:a",
            "192k",
        ])

    cmd.extend([
        "-shortest",
        output_video,
    ])

    run_ffmpeg(cmd)



def settings_text(session: Dict[str, Optional[str]]) -> str:
    template_name = {
        "stack": "فوق / تحت",
        "pip": "نافذة صغيرة",
        "side": "يمين / يسار",
    }.get(session["template"], session["template"])

    audio_name = {
        "mix": "الصوتان معًا",
        "main_only": "صوت الأساسي فقط",
        "reaction_only": "صوت الرياكشن فقط",
    }.get(session["audio_mode"], session["audio_mode"])

    return (
        "الإعدادات الحالية:\n"
        f"- القالب: {template_name}\n"
        f"- الصوت: {audio_name}\n"
        f"- صوت الأساسي: {session['main_volume']}\n"
        f"- صوت الرياكشن: {session['reaction_volume']}\n"
        f"- البراند: {session['brand'] or 'غير مفعّل'}\n"
        f"- العلامة المائية: {session['watermark'] or 'غير مفعّلة'}"
    )



def build_settings_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("فوق / تحت", callback_data="template:stack"),
            InlineKeyboardButton("نافذة صغيرة", callback_data="template:pip"),
            InlineKeyboardButton("يمين / يسار", callback_data="template:side"),
        ],
        [
            InlineKeyboardButton("الصوتان", callback_data="audio:mix"),
            InlineKeyboardButton("الأساسي فقط", callback_data="audio:main_only"),
            InlineKeyboardButton("الرياكشن فقط", callback_data="audio:reaction_only"),
        ],
        [
            InlineKeyboardButton("خفض صوت الرياكشن", callback_data="rv:down"),
            InlineKeyboardButton("رفع صوت الرياكشن", callback_data="rv:up"),
        ],
        [
            InlineKeyboardButton("خفض صوت الأساسي", callback_data="mv:down"),
            InlineKeyboardButton("رفع صوت الأساسي", callback_data="mv:up"),
        ],
        [
            InlineKeyboardButton("إزالة العلامة المائية", callback_data="watermark:clear"),
            InlineKeyboardButton("إزالة البراند", callback_data="brand:clear"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    reset_user_files(user_id)
    reset_user_session(user_id)

    await update.message.reply_text(
        "أهلًا.\n"
        "أرسل الفيديو الأساسي أولًا، ثم أرسل فيديو الرياكشن.\n"
        "يمكنك أيضًا استخدام /settings لتغيير شكل الفيديو والصوت."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return

    await update.message.reply_text(
        "الأوامر المتاحة:\n"
        "/start - بدء من جديد\n"
        "/settings - إعدادات القالب والصوت\n"
        "/reset - مسح الملفات الحالية\n"
        "/watermark نص - إضافة علامة مائية\n"
        "/brand نص - إضافة اسم حساب أو براند بالأعلى\n"
        "/status - عرض الإعدادات الحالية"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    ensure_user_session(user_id)
    await update.message.reply_text(settings_text(user_data_store[user_id]))


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    reset_user_files(user_id)
    reset_user_session(user_id)
    await update.message.reply_text("تمت إعادة التعيين. أرسل الفيديو الأساسي من جديد.")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    ensure_user_session(user_id)
    await update.message.reply_text(
        settings_text(user_data_store[user_id]),
        reply_markup=build_settings_keyboard(),
    )


async def watermark_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    ensure_user_session(user_id)
    text = " ".join(context.args).strip()
    user_data_store[user_id]["watermark"] = text
    await update.message.reply_text("تم تحديث العلامة المائية." if text else "تم حذف العلامة المائية.")


async def brand_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return

    user_id = update.effective_user.id
    ensure_user_session(user_id)
    text = " ".join(context.args).strip()
    user_data_store[user_id]["brand"] = text
    await update.message.reply_text("تم تحديث البراند." if text else "تم حذف البراند.")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query is None or update.effective_user is None:
        return

    query = update.callback_query
    user_id = update.effective_user.id
    ensure_user_session(user_id)
    session = user_data_store[user_id]

    data = query.data or ""

    if data.startswith("template:"):
        session["template"] = data.split(":", 1)[1]
    elif data.startswith("audio:"):
        session["audio_mode"] = data.split(":", 1)[1]
    elif data == "rv:down":
        session["reaction_volume"] = max(0.0, round(float(session["reaction_volume"]) - 0.1, 2))
    elif data == "rv:up":
        session["reaction_volume"] = min(2.0, round(float(session["reaction_volume"]) + 0.1, 2))
    elif data == "mv:down":
        session["main_volume"] = max(0.0, round(float(session["main_volume"]) - 0.1, 2))
    elif data == "mv:up":
        session["main_volume"] = min(2.0, round(float(session["main_volume"]) + 0.1, 2))
    elif data == "watermark:clear":
        session["watermark"] = ""
    elif data == "brand:clear":
        session["brand"] = ""

    await query.answer("تم التحديث")
    await query.edit_message_text(
        settings_text(session),
        reply_markup=build_settings_keyboard(),
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    ensure_user_session(user_id)
    session = user_data_store[user_id]
    user_dir = get_user_dir(user_id)

    tg_file = None
    extension = ".mp4"

    if update.message.video:
        tg_file = await update.message.video.get_file()
    elif (
        update.message.document
        and update.message.document.mime_type
        and update.message.document.mime_type.startswith("video/")
    ):
        tg_file = await update.message.document.get_file()
        original_name = update.message.document.file_name or "video.mp4"
        extension = Path(original_name).suffix or ".mp4"
    else:
        await update.message.reply_text("أرسل ملف فيديو صحيح")
        return

    if session["main"] is None:
        main_path = str(user_dir / f"main_{uuid.uuid4().hex}{extension}")
        await tg_file.download_to_drive(main_path)
        session["main"] = main_path
        await update.message.reply_text("تم حفظ الفيديو الأساسي. الآن أرسل فيديو الرياكشن.")
        return

    if session["reaction"] is None:
        reaction_path = str(user_dir / f"reaction_{uuid.uuid4().hex}{extension}")
        await tg_file.download_to_drive(reaction_path)
        session["reaction"] = reaction_path

        await update.message.reply_text("جاري المعالجة...")

        output_path = str(user_dir / f"output_{uuid.uuid4().hex}.mp4")

        try:
            merge_videos(
                main_video=session["main"],
                reaction_video=session["reaction"],
                output_video=output_path,
                template=str(session["template"]),
                audio_mode=str(session["audio_mode"]),
                main_volume=float(session["main_volume"]),
                reaction_volume=float(session["reaction_volume"]),
                watermark=str(session["watermark"]),
                brand=str(session["brand"]),
            )

            if not os.path.exists(output_path):
                await update.message.reply_text("فشل إنشاء الفيديو النهائي")
                return

            with open(output_path, "rb") as f:
                await update.message.reply_video(video=f)

        except Exception as e:
            await update.message.reply_text(f"حدث خطأ أثناء المعالجة:\n{e}")

        finally:
            reset_user_files(user_id)
            reset_user_session(user_id)
        return

    await update.message.reply_text("استخدم /reset للبدء من جديد.")



def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not found")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("watermark", watermark_command))
    app.add_handler(CommandHandler("brand", brand_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    app.run_polling()


if __name__ == "__main__":
    main()