# Telegram Reaction Bot (Simple Version)

import os
import subprocess
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")

user_data_store = {}

# =========================
# دمج الفيديو
# =========================
def merge_videos(main, reaction, output):
    cmd = [
        "ffmpeg",
        "-y",
        "-i", main,
        "-i", reaction,
        "-filter_complex",
        "[1:v]scale=320:-1[rv];[0:v][rv]overlay=(W-w)/2:20[v];"
        "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[a]",
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-c:a", "aac",
        output
    ]

    subprocess.run(cmd)

# =========================
# أوامر البوت
# =========================
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
    elif update.message.document:
        file = await update.message.document.get_file()

    path = f"{user_id}_{len(user_data_store[user_id])}.mp4"
    await file.download_to_drive(path)

    if user_data_store[user_id]["main"] is None:
        user_data_store[user_id]["main"] = path
        await update.message.reply_text("أرسل فيديو الرياكشن")
        return

    if user_data_store[user_id]["reaction"] is None:
        user_data_store[user_id]["reaction"] = path
        await update.message.reply_text("جاري المعالجة...")

        output = f"output_{user_id}.mp4"

        merge_videos(
            user_data_store[user_id]["main"],
            user_data_store[user_id]["reaction"],
            output
        )

        await update.message.reply_video(video=open(output, "rb"))

        user_data_store[user_id] = {"main": None, "reaction": None}


# =========================
# تشغيل البوت
# =========================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))

    app.run_polling()


if __name__ == "__main__":
    main()