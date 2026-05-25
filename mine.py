import asyncio
import os
import zipfile
import io
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, FSInputFile, BufferedInputFile
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from PIL import Image

# ─── CONFIG FROM ENV ───────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = list(map(int, os.environ.get("ADMIN_IDS", "0").split(",")))
CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")

WEEK_STARS = 50       # ~1$
FOREVER_STARS = 150   # ~3$
WEEK_CRYPTO_USD = 1
FOREVER_CRYPTO_USD = 3

# ─── DATABASE ──────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            packs_created INTEGER DEFAULT 0,
            sub_type TEXT DEFAULT 'free',
            sub_until TEXT DEFAULT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,
            amount TEXT,
            payload TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

def get_user(user_id: int):
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    return row

def upsert_user(user_id: int, username: str):
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)",
        (user_id, username or "")
    )
    cur.execute("UPDATE users SET username=? WHERE user_id=?", (username or "", user_id))
    con.commit()
    con.close()

def is_subscribed(user_id: int) -> bool:
    row = get_user(user_id)
    if not row:
        return False
    sub_type = row[3]
    sub_until = row[4]
    if sub_type == "forever":
        return True
    if sub_type == "week" and sub_until:
        return datetime.fromisoformat(sub_until) > datetime.now()
    return False

def has_free_pack(user_id: int) -> bool:
    row = get_user(user_id)
    return row and row[2] == 0

def increment_packs(user_id: int):
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    cur.execute("UPDATE users SET packs_created=packs_created+1 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()

def give_sub(user_id: int, sub_type: str):
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    if sub_type == "forever":
        cur.execute("UPDATE users SET sub_type='forever', sub_until=NULL WHERE user_id=?", (user_id,))
    elif sub_type == "week":
        until = (datetime.now() + timedelta(days=7)).isoformat()
        cur.execute("UPDATE users SET sub_type='week', sub_until=? WHERE user_id=?", (until, user_id))
    con.commit()
    con.close()

def log_payment(user_id, method, amount, payload):
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    cur.execute("INSERT INTO payments (user_id,method,amount,payload) VALUES (?,?,?,?)",
                (user_id, method, str(amount), payload))
    con.commit()
    con.close()

def all_users_count():
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    n = cur.fetchone()[0]
    con.close()
    return n

def paid_users_count():
    con = sqlite3.connect("users.db")
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE sub_type!='free'")
    n = cur.fetchone()[0]
    con.close()
    return n

# ─── TEXTURE MAPPINGS ──────────────────────────────────────────────────────────
JAVA_PATHS = {
    # Weapons
    "sword_wood":    "assets/minecraft/textures/item/wooden_sword.png",
    "sword_stone":   "assets/minecraft/textures/item/stone_sword.png",
    "sword_iron":    "assets/minecraft/textures/item/iron_sword.png",
    "sword_gold":    "assets/minecraft/textures/item/golden_sword.png",
    "sword_diamond": "assets/minecraft/textures/item/diamond_sword.png",
    "sword_netherite":"assets/minecraft/textures/item/netherite_sword.png",
    "bow":           "assets/minecraft/textures/item/bow.png",
    "crossbow":      "assets/minecraft/textures/item/crossbow.png",
    "trident":       "assets/minecraft/textures/item/trident.png",
    # Blocks
    "grass_top":     "assets/minecraft/textures/block/grass_block_top.png",
    "dirt":          "assets/minecraft/textures/block/dirt.png",
    "stone":         "assets/minecraft/textures/block/stone.png",
    "sand":          "assets/minecraft/textures/block/sand.png",
    "gravel":        "assets/minecraft/textures/block/gravel.png",
    "oak_log":       "assets/minecraft/textures/block/oak_log.png",
    "diamond_ore":   "assets/minecraft/textures/block/diamond_ore.png",
    "iron_ore":      "assets/minecraft/textures/block/iron_ore.png",
    "gold_ore":      "assets/minecraft/textures/block/gold_ore.png",
    "coal_ore":      "assets/minecraft/textures/block/coal_ore.png",
    "emerald_ore":   "assets/minecraft/textures/block/emerald_ore.png",
    # Items
    "apple":         "assets/minecraft/textures/item/apple.png",
    "bread":         "assets/minecraft/textures/item/bread.png",
    "diamond":       "assets/minecraft/textures/item/diamond.png",
    "iron_ingot":    "assets/minecraft/textures/item/iron_ingot.png",
    "gold_ingot":    "assets/minecraft/textures/item/gold_ingot.png",
    "arrow":         "assets/minecraft/textures/item/arrow.png",
    # Armor
    "iron_helmet":   "assets/minecraft/textures/models/armor/iron_layer_1.png",
    "diamond_helmet":"assets/minecraft/textures/models/armor/diamond_layer_1.png",
    # Mobs
    "zombie":        "assets/minecraft/textures/entity/zombie/zombie.png",
    "skeleton":      "assets/minecraft/textures/entity/skeleton/skeleton.png",
    "creeper":       "assets/minecraft/textures/entity/creeper/creeper.png",
    "enderman":      "assets/minecraft/textures/entity/enderman/enderman.png",
    "pig":           "assets/minecraft/textures/entity/pig/pig.png",
    "cow":           "assets/minecraft/textures/entity/cow/cow.png",
    # Tools
    "pickaxe_diamond":"assets/minecraft/textures/item/diamond_pickaxe.png",
    "pickaxe_iron":  "assets/minecraft/textures/item/iron_pickaxe.png",
    "axe_iron":      "assets/minecraft/textures/item/iron_axe.png",
    "shovel_iron":   "assets/minecraft/textures/item/iron_shovel.png",
}

BEDROCK_PATHS = {
    "sword_wood":    "textures/items/wood_sword.png",
    "sword_stone":   "textures/items/stone_sword.png",
    "sword_iron":    "textures/items/iron_sword.png",
    "sword_gold":    "textures/items/gold_sword.png",
    "sword_diamond": "textures/items/diamond_sword.png",
    "sword_netherite":"textures/items/netherite_sword.png",
    "bow":           "textures/items/bow_standby.png",
    "crossbow":      "textures/items/crossbow_standby.png",
    "trident":       "textures/items/trident.png",
    "grass_top":     "textures/blocks/grass_top.png",
    "dirt":          "textures/blocks/dirt.png",
    "stone":         "textures/blocks/stone.png",
    "sand":          "textures/blocks/sand.png",
    "gravel":        "textures/blocks/gravel.png",
    "oak_log":       "textures/blocks/log_oak.png",
    "diamond_ore":   "textures/blocks/diamond_ore.png",
    "iron_ore":      "textures/blocks/iron_ore.png",
    "gold_ore":      "textures/blocks/gold_ore.png",
    "coal_ore":      "textures/blocks/coal_ore.png",
    "emerald_ore":   "textures/blocks/emerald_ore.png",
    "apple":         "textures/items/apple.png",
    "bread":         "textures/items/bread.png",
    "diamond":       "textures/items/diamond.png",
    "iron_ingot":    "textures/items/iron_ingot.png",
    "gold_ingot":    "textures/items/gold_ingot.png",
    "arrow":         "textures/items/arrow.png",
    "iron_helmet":   "textures/models/armor/iron_1.png",
    "diamond_helmet":"textures/models/armor/diamond_1.png",
    "zombie":        "textures/entity/zombie/zombie.png",
    "skeleton":      "textures/entity/skeleton/skeleton.png",
    "creeper":       "textures/entity/creeper/creeper.png",
    "enderman":      "textures/entity/enderman/enderman.png",
    "pig":           "textures/entity/pig/pig.png",
    "cow":           "textures/entity/cow/cow.png",
    "pickaxe_diamond":"textures/items/diamond_pickaxe.png",
    "pickaxe_iron":  "textures/items/iron_pickaxe.png",
    "axe_iron":      "textures/items/iron_axe.png",
    "shovel_iron":   "textures/items/iron_shovel.png",
}

SOUND_JAVA_PATHS = {
    "hurt":       "assets/minecraft/sounds/damage/hit1.ogg",
    "death":      "assets/minecraft/sounds/damage/hit3.ogg",
    "explosion":  "assets/minecraft/sounds/random/explode1.ogg",
    "eat":        "assets/minecraft/sounds/random/eat1.ogg",
    "levelup":    "assets/minecraft/sounds/random/levelup.ogg",
    "click":      "assets/minecraft/sounds/random/click.ogg",
    "swim":       "assets/minecraft/sounds/liquid/swim1.ogg",
    "anvil":      "assets/minecraft/sounds/random/anvil_use.ogg",
    "chest_open": "assets/minecraft/sounds/random/chestopen.ogg",
    "chest_close":"assets/minecraft/sounds/random/chestclosed.ogg",
}

SOUND_BEDROCK_PATHS = {
    "hurt":       "sounds/damage/hit1.ogg",
    "death":      "sounds/damage/hit3.ogg",
    "explosion":  "sounds/random/explode1.ogg",
    "eat":        "sounds/random/eat1.ogg",
    "levelup":    "sounds/random/levelup.ogg",
    "click":      "sounds/random/click.ogg",
    "swim":       "sounds/liquid/swim1.ogg",
    "anvil":      "sounds/random/anvil_use.ogg",
    "chest_open": "sounds/random/chestopen.ogg",
    "chest_close":"sounds/random/chestclosed.ogg",
}

ITEM_LABELS = {
    "sword_wood": "⚔️ Деревянный меч",
    "sword_stone": "⚔️ Каменный меч",
    "sword_iron": "⚔️ Железный меч",
    "sword_gold": "⚔️ Золотой меч",
    "sword_diamond": "⚔️ Алмазный меч",
    "sword_netherite": "⚔️ Незеритовый меч",
    "bow": "🏹 Лук",
    "crossbow": "🏹 Арбалет",
    "trident": "🔱 Трезубец",
    "grass_top": "🌿 Трава (верх)",
    "dirt": "🟤 Земля",
    "stone": "🪨 Камень",
    "sand": "🏜 Песок",
    "gravel": "⬜ Гравий",
    "oak_log": "🪵 Дубовое бревно",
    "diamond_ore": "💎 Алмазная руда",
    "iron_ore": "⛏ Железная руда",
    "gold_ore": "🟡 Золотая руда",
    "coal_ore": "⬛ Угольная руда",
    "emerald_ore": "💚 Изумрудная руда",
    "apple": "🍎 Яблоко",
    "bread": "🍞 Хлеб",
    "diamond": "💎 Алмаз",
    "iron_ingot": "🔩 Железный слиток",
    "gold_ingot": "🟡 Золотой слиток",
    "arrow": "➡️ Стрела",
    "iron_helmet": "⛑ Железный шлем",
    "diamond_helmet": "💎 Алмазный шлем",
    "zombie": "🧟 Зомби",
    "skeleton": "💀 Скелет",
    "creeper": "💚 Крипер",
    "enderman": "🕴 Эндермен",
    "pig": "🐷 Свинья",
    "cow": "🐄 Корова",
    "pickaxe_diamond": "⛏ Алмазная кирка",
    "pickaxe_iron": "⛏ Железная кирка",
    "axe_iron": "🪓 Железный топор",
    "shovel_iron": "🪣 Железная лопата",
}

SOUND_LABELS = {
    "hurt": "💥 Урон",
    "death": "💀 Смерть",
    "explosion": "💣 Взрыв",
    "eat": "🍖 Еда",
    "levelup": "⬆️ Повышение уровня",
    "click": "🖱 Клик",
    "swim": "🏊 Плавание",
    "anvil": "⚒ Наковальня",
    "chest_open": "📦 Открытие сундука",
    "chest_close": "📦 Закрытие сундука",
}

CATEGORIES = {
    "weapons": ("⚔️ Оружие", ["sword_wood","sword_stone","sword_iron","sword_gold","sword_diamond","sword_netherite","bow","crossbow","trident"]),
    "blocks":  ("🧱 Блоки",  ["grass_top","dirt","stone","sand","gravel","oak_log","diamond_ore","iron_ore","gold_ore","coal_ore","emerald_ore"]),
    "items":   ("🎒 Предметы",["apple","bread","diamond","iron_ingot","gold_ingot","arrow"]),
    "armor":   ("🛡 Броня",   ["iron_helmet","diamond_helmet"]),
    "mobs":    ("🐷 Мобы",   ["zombie","skeleton","creeper","enderman","pig","cow"]),
    "tools":   ("🔨 Инструменты",["pickaxe_diamond","pickaxe_iron","axe_iron","shovel_iron"]),
}

# ─── FSM STATES ────────────────────────────────────────────────────────────────
class PackStates(StatesGroup):
    choose_type = State()       # texture / sound
    choose_version = State()
    choose_category = State()
    choose_item = State()
    upload_file = State()
    add_more = State()

class AdminStates(StatesGroup):
    give_sub_id = State()
    give_sub_type = State()

# ─── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
         InlineKeyboardButton(text="ℹ️ О боте", callback_data="about")],
        [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_sub")],
        [InlineKeyboardButton(text="🎨 Создать ресурс-пак", callback_data="create_pack")],
    ])

def back_kb(cb="main_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb)]
    ])

def buy_sub_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Telegram Stars — Неделя (50⭐)", callback_data="pay_stars_week")],
        [InlineKeyboardButton(text="⭐ Telegram Stars — Навсегда (150⭐)", callback_data="pay_stars_forever")],
        [InlineKeyboardButton(text="💰 Крипто — Неделя ($1)", callback_data="pay_crypto_week")],
        [InlineKeyboardButton(text="💰 Крипто — Навсегда ($3)", callback_data="pay_crypto_forever")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])

def pack_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Текстуры", callback_data="ptype_texture")],
        [InlineKeyboardButton(text="🔊 Звуки", callback_data="ptype_sound")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])

def version_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☕ Java Edition", callback_data="ver_java")],
        [InlineKeyboardButton(text="📱 Bedrock Edition", callback_data="ver_bedrock")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="create_pack")],
    ])

def category_kb(pack_type):
    rows = []
    for key, (label, _) in CATEGORIES.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"cat_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="create_pack")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def sound_category_kb():
    rows = []
    for key, label in SOUND_LABELS.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"snd_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="create_pack")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def items_kb(items: list):
    rows = []
    for key in items:
        rows.append([InlineKeyboardButton(text=ITEM_LABELS.get(key, key), callback_data=f"item_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="choose_cat")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def add_more_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ещё текстуру", callback_data="add_more_yes")],
        [InlineKeyboardButton(text="📦 Скачать пак", callback_data="finish_pack")],
    ])

def add_more_sound_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ещё звук", callback_data="add_more_yes")],
        [InlineKeyboardButton(text="📦 Скачать пак", callback_data="finish_pack")],
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin_give_sub")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")],
    ])

# ─── PACK BUILDER ──────────────────────────────────────────────────────────────
def build_java_pack(files: dict) -> bytes:
    """files = {internal_key: bytes_data}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        meta = json.dumps({
            "pack": {
                "pack_format": 15,
                "description": "§6My Custom Pack §7by PackCraftBot"
            }
        }, indent=2)
        zf.writestr("pack.mcmeta", meta)
        for key, data in files.items():
            path = JAVA_PATHS.get(key) or SOUND_JAVA_PATHS.get(key)
            if path:
                zf.writestr(path, data)
    return buf.getvalue()

def build_bedrock_pack(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = json.dumps({
            "format_version": 2,
            "header": {
                "description": "My Custom Pack by PackCraftBot",
                "name": "CustomPack",
                "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "version": [1, 0, 0],
                "min_engine_version": [1, 16, 0]
            },
            "modules": [{
                "description": "Resource pack",
                "type": "resources",
                "uuid": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "version": [1, 0, 0]
            }]
        }, indent=2)
        zf.writestr("manifest.json", manifest)
        for key, data in files.items():
            path = BEDROCK_PATHS.get(key) or SOUND_BEDROCK_PATHS.get(key)
            if path:
                zf.writestr(path, data)
    return buf.getvalue()

def resize_texture(img_bytes: bytes, size: int = 16) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    img = img.resize((size, size), Image.NEAREST)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

# ─── BOT ───────────────────────────────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ─── /start ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    upsert_user(message.from_user.id, message.from_user.username)
    text = (
        "🎮 <b>Добро пожаловать в PackCraftBot!</b>\n\n"
        "Я помогу тебе создать кастомный ресурс-пак для Minecraft:\n"
        "• Заменяй <b>текстуры</b> блоков, мобов, оружия\n"
        "• Заменяй <b>звуки</b> игры\n"
        "• Поддержка <b>Java</b> и <b>Bedrock</b>\n\n"
        "🆓 <b>Бесплатно:</b> 1 ресурс-пак\n"
        "💎 <b>Подписка:</b> безлимитно!\n\n"
        "Выбери действие:"
    )
    await message.answer(text, reply_markup=main_menu_kb(), parse_mode="HTML")

# ─── CALLBACKS ─────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "🎮 <b>Главное меню</b>\n\n"
        "Выбери действие:"
    )
    await cq.message.edit_text(text, reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "profile")
async def cb_profile(cq: CallbackQuery):
    uid = cq.from_user.id
    row = get_user(uid)
    sub = is_subscribed(uid)
    sub_type = row[3] if row else "free"
    sub_until = row[4] if row else None
    packs = row[2] if row else 0

    if sub_type == "forever":
        sub_text = "♾ Навсегда"
    elif sub_type == "week" and sub_until:
        sub_text = f"📅 До {sub_until[:10]}"
    else:
        sub_text = "❌ Нет"

    text = (
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📦 Паков создано: {packs}\n"
        f"💎 Подписка: {sub_text}\n"
    )
    await cq.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "about")
async def cb_about(cq: CallbackQuery):
    text = (
        "ℹ️ <b>О боте</b>\n\n"
        "🎮 <b>PackCraftBot</b> — создай свой ресурс-пак для Minecraft прямо в Telegram!\n\n"
        "✅ Поддержка Java Edition и Bedrock Edition\n"
        "✅ Замена текстур: блоки, мобы, оружие, броня\n"
        "✅ Замена звуков\n"
        "✅ Автоматическая упаковка в .zip / .mcpack\n\n"
        "💎 <b>Тарифы:</b>\n"
        "• Бесплатно — 1 пак\n"
        "• Неделя — 50⭐ или $1\n"
        "• Навсегда — 150⭐ или $3\n\n"
        "@PackCraftBot"
    )
    await cq.message.edit_text(text, reply_markup=back_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "buy_sub")
async def cb_buy_sub(cq: CallbackQuery):
    await cq.message.edit_text(
        "💎 <b>Выбери способ оплаты и тариф:</b>",
        reply_markup=buy_sub_kb(), parse_mode="HTML"
    )

# ─── STARS PAYMENTS ────────────────────────────────────────────────────────────
@dp.callback_query(F.data.in_({"pay_stars_week", "pay_stars_forever"}))
async def cb_pay_stars(cq: CallbackQuery):
    if cq.data == "pay_stars_week":
        await bot.send_invoice(
            chat_id=cq.from_user.id,
            title="⭐ Подписка на неделю",
            description="Безлимитные ресурс-паки на 7 дней",
            payload="stars_week",
            currency="XTR",
            prices=[LabeledPrice(label="Неделя", amount=WEEK_STARS)]
        )
    else:
        await bot.send_invoice(
            chat_id=cq.from_user.id,
            title="⭐ Подписка навсегда",
            description="Безлимитные ресурс-паки навсегда",
            payload="stars_forever",
            currency="XTR",
            prices=[LabeledPrice(label="Навсегда", amount=FOREVER_STARS)]
        )
    await cq.answer()

@dp.pre_checkout_query()
async def pre_checkout(pcq: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pcq.id, ok=True)

@dp.message(F.successful_payment)
async def payment_done(message: Message):
    uid = message.from_user.id
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount
    log_payment(uid, "stars", amount, payload)
    if "forever" in payload:
        give_sub(uid, "forever")
        await message.answer("✅ <b>Подписка навсегда активирована!</b>\nТеперь создавай неограниченно паков 🎉", parse_mode="HTML", reply_markup=main_menu_kb())
    else:
        give_sub(uid, "week")
        await message.answer("✅ <b>Подписка на неделю активирована!</b>\nТеперь создавай неограниченно паков 🎉", parse_mode="HTML", reply_markup=main_menu_kb())

# ─── CRYPTO PAYMENTS ───────────────────────────────────────────────────────────
@dp.callback_query(F.data.in_({"pay_crypto_week", "pay_crypto_forever"}))
async def cb_pay_crypto(cq: CallbackQuery):
    if not CRYPTO_BOT_TOKEN:
        await cq.answer("Крипто-оплата временно недоступна", show_alert=True)
        return
    import aiohttp
    plan = "week" if "week" in cq.data else "forever"
    amount = WEEK_CRYPTO_USD if plan == "week" else FOREVER_CRYPTO_USD
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
            json={
                "asset": "USDT",
                "amount": str(amount),
                "description": f"PackCraftBot — подписка {'неделя' if plan=='week' else 'навсегда'}",
                "payload": f"crypto_{plan}_{cq.from_user.id}",
                "paid_btn_name": "callback",
                "paid_btn_url": f"https://t.me/{(await bot.get_me()).username}",
            }
        )
        data = await resp.json()
    if data.get("ok"):
        pay_url = data["result"]["pay_url"]
        invoice_id = data["result"]["invoice_id"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Оплатить", url=pay_url)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_crypto_{invoice_id}_{plan}")],
        ])
        await cq.message.edit_text(
            f"💰 Оплата через CryptoBot\n\nСумма: <b>${amount} USDT</b>\n\nНажми кнопку для оплаты, затем «Я оплатил»",
            reply_markup=kb, parse_mode="HTML"
        )
    else:
        await cq.answer("Ошибка создания счёта", show_alert=True)

@dp.callback_query(F.data.startswith("check_crypto_"))
async def cb_check_crypto(cq: CallbackQuery):
    parts = cq.data.split("_")
    invoice_id = parts[2]
    plan = parts[3]
    import aiohttp
    async with aiohttp.ClientSession() as session:
        resp = await session.get(
            "https://pay.crypt.bot/api/getInvoices",
            headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
            params={"invoice_ids": invoice_id}
        )
        data = await resp.json()
    if data.get("ok") and data["result"]["items"]:
        inv = data["result"]["items"][0]
        if inv["status"] == "paid":
            uid = cq.from_user.id
            log_payment(uid, "crypto", inv.get("amount"), f"crypto_{plan}")
            give_sub(uid, plan)
            await cq.message.edit_text(
                "✅ <b>Оплата получена! Подписка активирована.</b>",
                parse_mode="HTML", reply_markup=main_menu_kb()
            )
        else:
            await cq.answer("Оплата не найдена. Попробуй позже.", show_alert=True)
    else:
        await cq.answer("Ошибка проверки. Попробуй позже.", show_alert=True)

# ─── CREATE PACK ───────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "create_pack")
async def cb_create_pack(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    if not is_subscribed(uid) and not has_free_pack(uid):
        await cq.message.edit_text(
            "❌ <b>Лимит исчерпан</b>\n\nБесплатно можно создать только 1 пак.\nКупи подписку для безлимита!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_sub")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
            ]),
            parse_mode="HTML"
        )
        return
    await state.set_state(PackStates.choose_type)
    await state.update_data(files={})
    await cq.message.edit_text(
        "🎨 <b>Что хочешь заменить?</b>",
        reply_markup=pack_type_kb(), parse_mode="HTML"
    )

@dp.callback_query(PackStates.choose_type, F.data.startswith("ptype_"))
async def cb_pack_type(cq: CallbackQuery, state: FSMContext):
    ptype = cq.data.split("_")[1]
    await state.update_data(pack_type=ptype)
    await state.set_state(PackStates.choose_version)
    await cq.message.edit_text(
        "🌍 <b>Выбери версию Minecraft:</b>",
        reply_markup=version_kb(), parse_mode="HTML"
    )

@dp.callback_query(PackStates.choose_version, F.data.startswith("ver_"))
async def cb_version(cq: CallbackQuery, state: FSMContext):
    version = cq.data.split("_")[1]
    await state.update_data(version=version)
    data = await state.get_data()
    await state.set_state(PackStates.choose_category)
    if data["pack_type"] == "sound":
        await cq.message.edit_text(
            "🔊 <b>Выбери звук для замены:</b>",
            reply_markup=sound_category_kb(), parse_mode="HTML"
        )
    else:
        await cq.message.edit_text(
            "📂 <b>Выбери категорию:</b>",
            reply_markup=category_kb(data["pack_type"]), parse_mode="HTML"
        )

@dp.callback_query(PackStates.choose_category, F.data.startswith("cat_"))
async def cb_category(cq: CallbackQuery, state: FSMContext):
    cat_key = cq.data[4:]
    _, items = CATEGORIES[cat_key]
    await state.update_data(category=cat_key)
    await state.set_state(PackStates.choose_item)
    await cq.message.edit_text(
        "🔍 <b>Выбери что заменить:</b>",
        reply_markup=items_kb(items), parse_mode="HTML"
    )

@dp.callback_query(PackStates.choose_category, F.data.startswith("snd_"))
async def cb_sound_item(cq: CallbackQuery, state: FSMContext):
    snd_key = cq.data[4:]
    await state.update_data(current_item=snd_key)
    await state.set_state(PackStates.upload_file)
    label = SOUND_LABELS.get(snd_key, snd_key)
    await cq.message.edit_text(
        f"🔊 <b>{label}</b>\n\nОтправь файл звука в формате <b>.ogg</b>",
        reply_markup=back_kb("create_pack"), parse_mode="HTML"
    )

@dp.callback_query(PackStates.choose_item, F.data.startswith("item_"))
async def cb_item(cq: CallbackQuery, state: FSMContext):
    item_key = cq.data[5:]
    await state.update_data(current_item=item_key)
    await state.set_state(PackStates.upload_file)
    label = ITEM_LABELS.get(item_key, item_key)
    await cq.message.edit_text(
        f"🖼 <b>{label}</b>\n\nОтправь текстуру в формате <b>PNG</b>\n"
        "Поддерживаемые размеры: 16×16, 32×32, 64×64",
        reply_markup=back_kb("create_pack"), parse_mode="HTML"
    )

@dp.message(PackStates.upload_file, F.photo | F.document)
async def upload_file(message: Message, state: FSMContext):
    data = await state.get_data()
    pack_type = data.get("pack_type", "texture")
    item_key = data.get("current_item")
    files = data.get("files", {})

    if pack_type == "sound":
        if not message.document:
            await message.answer("❌ Отправь файл .ogg (не фото)")
            return
        doc = message.document
        if not doc.file_name.endswith(".ogg"):
            await message.answer("❌ Нужен файл .ogg")
            return
        file = await bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        files[item_key] = buf.getvalue()
        await state.update_data(files=files)
        label = SOUND_LABELS.get(item_key, item_key)
        await message.answer(
            f"✅ <b>{label}</b> добавлен!\n\nЧто дальше?",
            reply_markup=add_more_sound_kb(), parse_mode="HTML"
        )
    else:
        # texture
        if message.photo:
            photo = message.photo[-1]
            file = await bot.get_file(photo.file_id)
        elif message.document:
            file = await bot.get_file(message.document.file_id)
        else:
            await message.answer("❌ Отправь PNG файл")
            return
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        img_bytes = resize_texture(buf.getvalue(), 16)
        files[item_key] = img_bytes
        await state.update_data(files=files)
        label = ITEM_LABELS.get(item_key, item_key)
        await message.answer(
            f"✅ <b>{label}</b> добавлена!\n\nЧто дальше?",
            reply_markup=add_more_kb(), parse_mode="HTML"
        )
    await state.set_state(PackStates.add_more)

@dp.callback_query(PackStates.add_more, F.data == "add_more_yes")
async def cb_add_more(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(PackStates.choose_category)
    if data.get("pack_type") == "sound":
        await cq.message.edit_text(
            "🔊 <b>Выбери следующий звук:</b>",
            reply_markup=sound_category_kb(), parse_mode="HTML"
        )
    else:
        await cq.message.edit_text(
            "📂 <b>Выбери категорию:</b>",
            reply_markup=category_kb(data.get("pack_type")), parse_mode="HTML"
        )

@dp.callback_query(F.data == "choose_cat")
async def cb_choose_cat(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(PackStates.choose_category)
    await cq.message.edit_text(
        "📂 <b>Выбери категорию:</b>",
        reply_markup=category_kb(data.get("pack_type")), parse_mode="HTML"
    )

@dp.callback_query(F.data == "finish_pack")
async def cb_finish_pack(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    version = data.get("version", "java")
    files = data.get("files", {})
    uid = cq.from_user.id

    if not files:
        await cq.answer("Добавь хотя бы один файл!", show_alert=True)
        return

    await cq.message.edit_text("⏳ Собираю ресурс-пак...")

    if version == "java":
        pack_bytes = build_java_pack(files)
        filename = "CustomPack_Java.zip"
    else:
        pack_bytes = build_bedrock_pack(files)
        filename = "CustomPack_Bedrock.mcpack"

    increment_packs(uid)
    await state.clear()

    await bot.send_document(
        chat_id=uid,
        document=BufferedInputFile(pack_bytes, filename=filename),
        caption=(
            f"✅ <b>Твой ресурс-пак готов!</b>\n\n"
            f"📦 Версия: {'☕ Java' if version=='java' else '📱 Bedrock'}\n"
            f"🖼 Изменений: {len(files)}\n\n"
            f"📥 Как установить:\n"
            + ("• Помести .zip в папку resourcepacks\n• Зайди в игру → Настройки → Пакеты ресурсов" if version=="java"
               else "• Переименуй в .mcpack и открой\n• Bedrock установит автоматически")
        ),
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

# ─── ADMIN PANEL ───────────────────────────────────────────────────────────────
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Нет доступа")
        return
    await state.clear()
    await message.answer(
        "👑 <b>Админ-панель</b>",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        return
    total = all_users_count()
    paid = paid_users_count()
    await cq.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"💎 Платных: {paid}\n",
        reply_markup=admin_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "admin_give_sub")
async def cb_admin_give_sub(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminStates.give_sub_id)
    await cq.message.edit_text(
        "🎁 Введи ID пользователя для выдачи подписки:",
        reply_markup=back_kb("main_menu")
    )

@dp.message(AdminStates.give_sub_id)
async def admin_get_id(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        uid = int(message.text.strip())
        await state.update_data(target_id=uid)
        await state.set_state(AdminStates.give_sub_type)
        await message.answer(
            f"Выбери тип подписки для {uid}:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📅 Неделя", callback_data="admin_sub_week")],
                [InlineKeyboardButton(text="♾ Навсегда", callback_data="admin_sub_forever")],
            ])
        )
    except ValueError:
        await message.answer("❌ Введи числовой ID")

@dp.callback_query(AdminStates.give_sub_type, F.data.startswith("admin_sub_"))
async def admin_give_type(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in ADMIN_IDS:
        return
    sub_type = "week" if cq.data == "admin_sub_week" else "forever"
    data = await state.get_data()
    target_id = data["target_id"]
    upsert_user(target_id, "")
    give_sub(target_id, sub_type)
    await state.clear()
    await cq.message.edit_text(
        f"✅ Подписка <b>{'неделя' if sub_type=='week' else 'навсегда'}</b> выдана пользователю <code>{target_id}</code>",
        reply_markup=admin_kb(), parse_mode="HTML"
    )
    try:
        await bot.send_message(target_id, "🎁 <b>Вам выдана бесплатная подписка!</b>", parse_mode="HTML", reply_markup=main_menu_kb())
    except Exception:
        pass

# ─── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
