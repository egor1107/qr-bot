import os
import asyncio
import logging
import segno
import cv2
import numpy as np
import quopri
import requests
import sqlite3
from io import BytesIO
from PIL import Image, ImageDraw
from urllib.parse import urlparse
from geopy.geocoders import Nominatim
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.getenv("BOT_TOKEN")
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
geolocator = Nominatim(user_agent="qr_final_presentation")

class QRStates(StatesGroup):
    entering_data = State()

def init_db():
    conn = sqlite3.connect('qr_history.db')
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, qr_type TEXT, content TEXT)')
    conn.commit()
    conn.close()

def save_to_history(user_id, qr_type, content):
    conn = sqlite3.connect('qr_history.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO history (user_id, qr_type, content) VALUES (?, ?, ?)', (user_id, qr_type, content))
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect('qr_history.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, qr_type, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 5', (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_qr_from_history(qr_id):
    conn = sqlite3.connect('qr_history.db')
    cursor = conn.cursor()
    cursor.execute('SELECT qr_type, content FROM history WHERE id = ?', (qr_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_favicon(url):
    try:
        domain = urlparse(url).netloc
        res = requests.get(f"https://www.google.com/s2/favicons?sz=128&domain={domain}", timeout=3)
        return BytesIO(res.content) if res.status_code == 200 else None
    except: return None

def create_qr_image(qr_type, content):
    final_content, logo_data, qr_color = content, None, "black"
    if qr_type == "url":
        logo_data = get_favicon(content)
    elif qr_type == "geo":
        try:
            loc = geolocator.geocode(content)
            if loc: lat, lon = loc.latitude, loc.longitude
            else:
                coords = [c.strip() for c in content.split(",")]
                lat, lon = coords[0], coords[1]
            final_content = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            qr_color, logo_url = "#1a73e8", "https://cdn-icons-png.flaticon.com/128/684/684908.png"
            logo_data = BytesIO(requests.get(logo_url).content)
        except: raise ValueError()
    elif qr_type == "vcard":
        name, phone = [c.strip() for c in content.split(",", 1)]
        name_enc = quopri.encodestring(name.encode('utf-8')).decode('utf-8').replace('\n', '')
        final_content = f"BEGIN:VCARD\nVERSION:2.1\nFN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:{name_enc}\nTEL;CELL:{phone}\nEND:VCARD"
        qr_color = "#2e7d32"

    qr = segno.make(final_content, error='h')
    out = BytesIO()
    qr.save(out, kind='png', scale=15, dark=qr_color)
    out.seek(0)

    if logo_data:
        try:
            img, logo = Image.open(out).convert("RGBA"), Image.open(logo_data).convert("RGBA")
            size = int(img.width * 0.22)
            logo = logo.resize((size, size), Image.Resampling.LANCZOS)
            bg_s = size + 8
            bg = Image.new("RGBA", (bg_s, bg_s), (255, 255, 255, 0))
            ImageDraw.Draw(bg).ellipse([0, 0, bg_s-1, bg_s-1], fill="white")
            img.paste(bg, ((img.width-bg_s)//2, (img.height-bg_s)//2), bg)
            img.paste(logo, ((img.width-size)//2, (img.height-size)//2), logo)
            out = BytesIO(); img.save(out, format="PNG"); out.seek(0)
        except: pass
    return out

def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔗 Ссылка"), KeyboardButton(text="📍 Локация")],
        [KeyboardButton(text="📇 Визитка"), KeyboardButton(text="📜 История")]
    ], resize_keyboard=True)

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("QR Master готов.", reply_markup=main_menu())

@dp.message(F.text == "📜 История")
async def history(message: types.Message):
    h = get_history(message.from_user.id)
    if not h: return await message.answer("Пусто.")
    kb = [[InlineKeyboardButton(text=f"{q[1].upper()}: {q[2][:15]}...", callback_data=f"hist_{q[0]}")] for q in h]
    await message.answer("История:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("hist_"))
async def recall(callback: types.CallbackQuery):
    r = get_qr_from_history(callback.data.split("_")[1])
    if r:
        p = create_qr_image(r[0], r[1])
        await callback.message.answer_photo(photo=BufferedInputFile(p.read(), filename="qr.png"))
    await callback.answer()

@dp.message(F.text.in_(["🔗 Ссылка", "📍 Локация", "📇 Визитка"]))
async def menu_choice(message: types.Message, state: FSMContext):
    await state.set_state(QRStates.entering_data)
    t_map = {"🔗 Ссылка": "url", "📍 Локация": "geo", "📇 Визитка": "vcard"}
    await state.update_data(qr_type=t_map[message.text])
    await message.answer("Введите данные:")

@dp.message(QRStates.entering_data)
async def process_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        p = create_qr_image(data['qr_type'], message.text)
        save_to_history(message.from_user.id, data['qr_type'], message.text)
        await message.answer_photo(photo=BufferedInputFile(p.read(), filename="qr.png"))
    except: await message.answer("Ошибка.")
    await state.clear()

@dp.message(F.text.startswith("http"))
async def fast_url(message: types.Message):
    p = create_qr_image("url", message.text)
    save_to_history(message.from_user.id, "url", message.text)
    await message.answer_photo(photo=BufferedInputFile(p.read(), filename="qr.png"))

@dp.message(F.photo)
async def scan_photo(message: types.Message):
    f = BytesIO(); await bot.download(message.photo[-1], destination=f)
    img = cv2.imdecode(np.frombuffer(f.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    v, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    await message.answer(f"Результат: `{v}`" if v else "Не найдено.")

async def run():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(run())