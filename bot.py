import os
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
user_data_store = {}


def run_ffmpeg(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def merge_videos(main_video, reaction_video, output_video):
    cmd = [
        "ffmpeg",
        "-y",
        "-i", main_video,
        "-i", reaction_video,
        "-filter_complex",
        (
            # الفيديو الأساسي: نجعله خلفية 720x1280
            "[0:v]scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black[bg];"

            # فيديو الرياكشن: نصغره ونضعه بالأعلى
            "[1:v]scale=260:-1[react];"

            # تركيب الرياكشن فوق الخلفية
            "[bg][react]overlay=(W-w)/2:40[v]"
        ),
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        output_video
    ]

    run_ffmpeg(cmd)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store[user_id] = {"main": None, "reaction": None}
    await update.message.reply_text("أرسل الفيديو الأساسي")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in user_data_store:
        user_data_store[user_id] = {"main": None, "reaction": None}

    tg_file = None

    if update.message.video:
        tg_file = await update.message.video.get_file()
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("video/"):
        tg_file = await update.message.document.get_file()
    else:
        await update.message.reply_text("أرسل ملف فيديو صحيح")
        return

    if user_data_store[user_id]["main"] is None:
        main_path = f"{user_id}_main.mp4"
        await tg_file.download_to_drive(main_path)
        user_data_store[user_id]["main"] = main_path
        await update.message.reply_text("أرسل فيديو الرياكشن")
        return

    if user_data_store[user_id]["reaction"] is None:
        reaction_path = f"{user_id}_reaction.mp4"
        await tg_file.download_to_drive(reaction_path)
        user_data_store[user_id]["reaction"] = reaction_path

        await update.message.reply_text("جاري المعالجة...")

        output_path = f"{user_id}_output.mp4"

        try:
            merge_videos(
                user_data_store[user_id]["main"],
                user_data_store[user_id]["reaction"],
                output_path
            )

            if not os.path.exists(output_path):
                await update.message.reply_text("فشل إنشاء الفيديو النهائي")
                return

            with open(output_path, "rb") as f:
                await update.message.reply_video(video=f)

        except Exception as e:
            await update.message.reply_text(f"حدث خطأ أثناء المعالجة:\n{e}")

        finally:
            for path in [
                user_data_store[user_id]["main"],
                user_data_store[user_id]["reaction"],
                output_path
            ]:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except:
                        pass

            user_data_store[user_id] = {"main": None, "reaction": None}


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not found")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    app.run_polling()


if __name__ == "__main__":
    main()