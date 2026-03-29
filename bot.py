import os
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")

user_data_store = {}


def merge_videos(main, reaction, output):
    cmd = [
        "ffmpeg",
        "-y",
        "-i", main,
        "-i", reaction,
        "-filter_complex",
        (
            "[1:v]scale=320:-1[rv];"
            "[0:v][rv]overlay=(W-w)/2:20[v];"
            "[0:a]volume=1.0[a0];"
            "[1:a]volume=0.35[a1];"
            "[a0][a1]amix=inputs=2:duration=shortest[a]"
        ),
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        output
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(result.stderr)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data_store[user_id] = {"main": None, "reaction": None}
    await update.message.reply_text("أرسل الفيديو الأساسي")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in user_data_store:
        user_data_store[user_id] = {"main": None, "reaction": None}

    file = None

    if update.message.video:
        file = await update.message.video.get_file()
    elif update.message.document and update.message.document.mime_type and update.message.document.mime_type.startswith("video/"):
        file = await update.message.document.get_file()
    else:
        await update.message.reply_text("أرسل ملف فيديو صحيح")
        return

    if user_data_store[user_id]["main"] is None:
        path = f"{user_id}_main.mp4"
        await file.download_to_drive(path)
        user_data_store[user_id]["main"] = path
        await update.message.reply_text("أرسل فيديو الرياكشن")
        return

    if user_data_store[user_id]["reaction"] is None:
        path = f"{user_id}_reaction.mp4"
        await file.download_to_drive(path)
        user_data_store[user_id]["reaction"] = path
        await update.message.reply_text("جاري المعالجة...")

        output = f"output_{user_id}.mp4"

        try:
            merge_videos(
                user_data_store[user_id]["main"],
                user_data_store[user_id]["reaction"],
                output
            )

            if not os.path.exists(output):
                await update.message.reply_text("فشل إنشاء الفيديو النهائي")
                return

            with open(output, "rb") as video_file:
                await update.message.reply_video(video=video_file)

        except Exception as e:
            await update.message.reply_text(f"حدث خطأ أثناء المعالجة:\n{e}")

        finally:
            for file_path in [
                user_data_store[user_id]["main"],
                user_data_store[user_id]["reaction"],
                output
            ]:
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass

            user_data_store[user_id] = {"main": None, "reaction": None}


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    app.run_polling()


if __name__ == "__main__":
    main()