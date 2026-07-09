import asyncio
import logging
import time
import os
import random
import dotenv
import subprocess
import uuid
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientTimeout
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from aiogram.types import FSInputFile
from moviepy import VideoFileClip        

# cfg   
dotenv.load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

os.makedirs("bot", exist_ok=True)

bot = Bot(token=BOT_TOKEN)
router = Router()
dp = Dispatcher()
dp.include_router(router)
file_cache = {}

session = AiohttpSession(
    timeout=ClientTimeout(total=600, connect=30, sock_read=300) # 5 minuttes i think
)

logging.basicConfig(level=logging.INFO)

# hello message
@router.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Привет! Я бот для конвертации файлов.\n\n"
        "1. Отправьте файл (документ, видео или аудио).\n"
        "2. Выберите нужный формат.\n"
        "3. Получите результат.\n\n"
        "Поддерживаемые форматы для видео/анимаций: mp4, mp3.\n"
        "Поддерживаемые форматы для аудио: mp3, wav, ogg, flac.")

class ConvertCallback(CallbackData, prefix="convert"):
    action: str
    cache_key: str
    file_extension: str
    target_extension: str 
# why just not MAX_FILE_SIZE = 20 MiB?
MAX_FILE_SIZE = 20 * 1024 * 1024

# you should add some
CONVERSION_START_PHRASES = [
    "Начинаю конвертацию...",
    "Собираю файл и готовлюсь к работе...",
    "Обрабатываю ваш файл, подождите секунду...",
    "Запускаю преобразование формата...",
    "Файл принят, конвертация в процессе...",
    "Выполняю магию с вашим файлом..."
]

# sorted by file type
@router.message(F.document | F.video | F.audio)
async def handle_file(message: types.Message):
    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name
        file_extension = file_name.split('.')[-1].lower()
        file_size = message.document.file_size
    elif message.video:
        file_id = message.video.file_id
        file_name = f"video_{file_id}.mp4"
        file_extension = "mp4"
        file_size = message.video.file_size
    elif message.audio:
        file_id = message.audio.file_id
        file_name = f"audio_{file_id}.mp3"
        file_name = message.audio.file_name or "result.mp3" # <-- somebody obeously haven't imagination 
        file_extension = "mp3"
        file_size = message.audio.file_size
    else:
        await message.answer("Не удалось определить тип файла.")
        return

    if file_size > MAX_FILE_SIZE:
        await message.answer("Файл слишком большой. Максимальный размер файла: 20 МБ.")
        return

    clean_name = file_name.rsplit('.', 1)[0] if '.' in file_name else file_name

    cache_key = str(uuid.uuid4())[:8]
    file_cache[cache_key] = {
        "file_id": file_id,
        "file_name": file_name,
    }

    builder = InlineKeyboardBuilder()

    # the tab masterpiece
    if file_extension in ["mp4", "avi", "mov", "mkv", "gif"]:
        builder.button(text="Конвертировать в mp4", callback_data=ConvertCallback(action="convert", cache_key=cache_key, file_extension=file_extension, target_extension="mp4"))
        builder.button(text="Конвертировать в mp3", callback_data=ConvertCallback(action="convert", cache_key=cache_key, file_extension=file_extension, target_extension="mp3"))
    elif file_extension in ["mp3", "wav", "ogg", "flac"]:
        builder.button(text="Конвертировать в mp3", callback_data=ConvertCallback(action="convert", cache_key=cache_key, file_extension=file_extension, target_extension="mp3"))
        builder.button(text="Конвертировать в wav", callback_data=ConvertCallback(action="convert", cache_key=cache_key, file_extension=file_extension, target_extension="wav"))
        builder.button(text="Конвертировать в ogg", callback_data=ConvertCallback(action="convert", cache_key=cache_key, file_extension=file_extension, target_extension="ogg"))
        builder.button(text="Конвертировать в flac", callback_data=ConvertCallback(action="convert", cache_key=cache_key, file_extension=file_extension, target_extension="flac"))
    else:
        await message.answer("Формат файла не поддерживается.")
        return

    builder.adjust(2)

    await message.answer("Выберите формат для конвертации:", reply_markup=builder.as_markup())

@router.callback_query(ConvertCallback.filter(F.action == "convert"))
async def handle_conversion(callback_query: types.CallbackQuery, callback_data: ConvertCallback):
    await callback_query.answer(random.choice(CONVERSION_START_PHRASES))

    cache_key = callback_data.cache_key
    file_extension = callback_data.file_extension
    target_extension = callback_data.target_extension

    file_id = file_cache.get(cache_key)
    if not file_id:
        await callback_query.message.answer("Файл не найден в кэше. Пожалуйста, отправьте файл снова.")
        return
    
    file_id = file_id["file_id"]
    original_file_name = file_cache[cache_key]["file_name"]

    await callback_query.message.answer(f"Файл {file_id}.{file_extension} получен. Начинаю конвертацию в {target_extension}...")

    file_info = await bot.get_file(file_id)
    file_path = file_info.file_path
    input_path = f"bot/{file_id}.{file_extension}"
    output_path = f"bot/{file_id}.{target_extension}"

    await bot.download_file(file_path, input_path)

    # why I should to check it? are you deaf?
    async def has_audio_stream(path: str) -> bool:
        """Проверяет наличие аудиодорожки в файле через ffprobe."""
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        return bool(stdout.strip())

    async def run_ffmpeg(*args: str):
        """Запускает ffmpeg с переданными аргументами и бросает исключение при ошибке."""
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", *args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors='ignore'))
    
    # wtf is this? idk how it works but it /should/ works    
    try:
        if target_extension == "mp3":
            if not await has_audio_stream(input_path):
                await callback_query.message.answer("В видео нет аудиодорожки для конвертации в mp3.")
                return

            await run_ffmpeg(
                "-i", input_path,
                "-vn",
                "-acodec", "libmp3lame",
                "-q:a", "5",
                output_path,
            )

        elif target_extension == "wav":
            await run_ffmpeg(
                "-i", input_path,
                "-vn",
                "-acodec", "pcm_s16le",
                output_path,
            )

        elif target_extension == "ogg":
            await run_ffmpeg(
                "-i", input_path,
                "-vn",
                "-acodec", "libvorbis",
                output_path,
            )

        elif target_extension == "mp4":
            await run_ffmpeg(
                "-i", input_path,
                "-c:v", "libx264",
                "-c:a", "aac",
                output_path,
            )

        elif target_extension == "flac":
            await run_ffmpeg(
                "-i", input_path,
                "-vn",
                "-acodec", "flac",
                output_path,
            )

        else:
            await callback_query.message.answer("Неверный формат конвертации.")
            return
        
        # restore original file name
        clean_name = original_file_name.rsplit('.', 1)[0] if '.' in original_file_name else original_file_name
        document_to_send = FSInputFile(output_path, filename=f"{clean_name}.{target_extension}")

        await callback_query.message.answer_document(document_to_send, caption=f"Файл успешно конвертирован в {target_extension}!")

    except Exception as e:
        await callback_query.message.answer(f"Ошибка при конвертации: {e}")

    finally: # finally rm -rf /
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)
        if cache_key in file_cache:
            del file_cache[cache_key]
        await callback_query.message.answer("Файлы удалены с сервера.")
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())