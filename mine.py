import asyncio
import os
import zipfile
import io
import json
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, BufferedInputFile
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from PIL import Image

import firebase_admin
from firebase_admin import credentials, db as firebase_db

# ─── CONFIG FROM ENV ───────────────────────────────────────────────────────────
BOT_TOKEN        = os.environ["BOT_TOKEN"]
ADMIN_IDS        = list(map(int, os.environ.get("ADMIN_IDS", "0").split(",")))
CRYPTO_BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN", "")
FIREBASE_KEY     = os.environ["FIREBASE_KEY"]          # JSON-строка из env
FIREBASE_URL     = os.environ["FIREBASE_URL"]          # https://mcpackcraft-3337f-default-rtdb.europe-west1.firebasedatabase.app

WEEK_STARS       = 50
FOREVER_STARS    = 150
WEEK_CRYPTO_USD  = 1
FOREVER_CRYPTO_USD = 3

# ─── FIREBASE INIT ─────────────────────────────────────────────────────────────
def init_firebase():
    cred = credentials.Certificate(json.loads(FIREBASE_KEY))
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})

# ─── FIREBASE HELPERS ──────────────────────────────────────────────────────────
def _uref(user_id: int):
    return firebase_db.reference(f"users/{user_id}")

def get_user(user_id: int) -> dict | None:
    return _uref(user_id).get()

def upsert_user(user_id: int, username: str):
    ref = _uref(user_id)
    if ref.get() is None:
        ref.set({
            "username": username or "",
            "packs_created": 0,
            "sub_type": "free",
            "sub_until": None,
            "created_at": datetime.now().isoformat(),
        })
    else:
        ref.update({"username": username or ""})

def is_subscribed(user_id: int) -> bool:
    u = get_user(user_id)
    if not u:
        return False
    if u.get("sub_type") == "forever":
        return True
    if u.get("sub_type") == "week" and u.get("sub_until"):
        return datetime.fromisoformat(u["sub_until"]) > datetime.now()
    return False

def has_free_pack(user_id: int) -> bool:
    u = get_user(user_id)
    return u is not None and u.get("packs_created", 0) == 0

def increment_packs(user_id: int):
    u = get_user(user_id)
    cur = u.get("packs_created", 0) if u else 0
    _uref(user_id).update({"packs_created": cur + 1})

def give_sub(user_id: int, sub_type: str):
    if sub_type == "forever":
        _uref(user_id).update({"sub_type": "forever", "sub_until": None})
    elif sub_type == "week":
        until = (datetime.now() + timedelta(days=7)).isoformat()
        _uref(user_id).update({"sub_type": "week", "sub_until": until})

def log_payment(user_id, method, amount, payload):
    firebase_db.reference("payments").push({
        "user_id": user_id,
        "method": method,
        "amount": str(amount),
        "payload": payload,
        "created_at": datetime.now().isoformat(),
    })

def all_users_count() -> int:
    data = firebase_db.reference("users").get()
    return len(data) if data else 0

def paid_users_count() -> int:
    data = firebase_db.reference("users").get()
    if not data:
        return 0
    return sum(1 for u in data.values() if u.get("sub_type") != "free")

# ─── TEXTURE MAPPINGS ──────────────────────────────────────────────────────────
# Java Edition paths
JAVA_PATHS = {
    # ── Weapons ──────────────────────────────────────────────────────────────
    "sword_wood":      "assets/minecraft/textures/item/wooden_sword.png",
    "sword_stone":     "assets/minecraft/textures/item/stone_sword.png",
    "sword_iron":      "assets/minecraft/textures/item/iron_sword.png",
    "sword_gold":      "assets/minecraft/textures/item/golden_sword.png",
    "sword_diamond":   "assets/minecraft/textures/item/diamond_sword.png",
    "sword_netherite": "assets/minecraft/textures/item/netherite_sword.png",
    "bow":             "assets/minecraft/textures/item/bow.png",
    "crossbow":        "assets/minecraft/textures/item/crossbow_standby.png",
    "trident":         "assets/minecraft/textures/item/trident.png",
    "axe_wood":        "assets/minecraft/textures/item/wooden_axe.png",
    "axe_stone":       "assets/minecraft/textures/item/stone_axe.png",
    "axe_iron":        "assets/minecraft/textures/item/iron_axe.png",
    "axe_gold":        "assets/minecraft/textures/item/golden_axe.png",
    "axe_diamond":     "assets/minecraft/textures/item/diamond_axe.png",
    "axe_netherite":   "assets/minecraft/textures/item/netherite_axe.png",
    # ── Tools ────────────────────────────────────────────────────────────────
    "pickaxe_wood":      "assets/minecraft/textures/item/wooden_pickaxe.png",
    "pickaxe_stone":     "assets/minecraft/textures/item/stone_pickaxe.png",
    "pickaxe_iron":      "assets/minecraft/textures/item/iron_pickaxe.png",
    "pickaxe_gold":      "assets/minecraft/textures/item/golden_pickaxe.png",
    "pickaxe_diamond":   "assets/minecraft/textures/item/diamond_pickaxe.png",
    "pickaxe_netherite": "assets/minecraft/textures/item/netherite_pickaxe.png",
    "shovel_wood":       "assets/minecraft/textures/item/wooden_shovel.png",
    "shovel_stone":      "assets/minecraft/textures/item/stone_shovel.png",
    "shovel_iron":       "assets/minecraft/textures/item/iron_shovel.png",
    "shovel_gold":       "assets/minecraft/textures/item/golden_shovel.png",
    "shovel_diamond":    "assets/minecraft/textures/item/diamond_shovel.png",
    "shovel_netherite":  "assets/minecraft/textures/item/netherite_shovel.png",
    "hoe_wood":          "assets/minecraft/textures/item/wooden_hoe.png",
    "hoe_stone":         "assets/minecraft/textures/item/stone_hoe.png",
    "hoe_iron":          "assets/minecraft/textures/item/iron_hoe.png",
    "hoe_gold":          "assets/minecraft/textures/item/golden_hoe.png",
    "hoe_diamond":       "assets/minecraft/textures/item/diamond_hoe.png",
    "hoe_netherite":     "assets/minecraft/textures/item/netherite_hoe.png",
    # ── Armor — layer_1 = helmet+chestplate+boots, layer_2 = leggings ────────
    "leather_helmet":       "assets/minecraft/textures/models/armor/leather_layer_1.png",
    "leather_chestplate":   "assets/minecraft/textures/models/armor/leather_layer_1.png",
    "leather_leggings":     "assets/minecraft/textures/models/armor/leather_layer_2.png",
    "leather_boots":        "assets/minecraft/textures/models/armor/leather_layer_1.png",
    "chainmail_helmet":     "assets/minecraft/textures/models/armor/chainmail_layer_1.png",
    "chainmail_chestplate": "assets/minecraft/textures/models/armor/chainmail_layer_1.png",
    "chainmail_leggings":   "assets/minecraft/textures/models/armor/chainmail_layer_2.png",
    "chainmail_boots":      "assets/minecraft/textures/models/armor/chainmail_layer_1.png",
    "iron_helmet":          "assets/minecraft/textures/models/armor/iron_layer_1.png",
    "iron_chestplate":      "assets/minecraft/textures/models/armor/iron_layer_1.png",
    "iron_leggings":        "assets/minecraft/textures/models/armor/iron_layer_2.png",
    "iron_boots":           "assets/minecraft/textures/models/armor/iron_layer_1.png",
    "gold_helmet":          "assets/minecraft/textures/models/armor/gold_layer_1.png",
    "gold_chestplate":      "assets/minecraft/textures/models/armor/gold_layer_1.png",
    "gold_leggings":        "assets/minecraft/textures/models/armor/gold_layer_2.png",
    "gold_boots":           "assets/minecraft/textures/models/armor/gold_layer_1.png",
    "diamond_helmet":       "assets/minecraft/textures/models/armor/diamond_layer_1.png",
    "diamond_chestplate":   "assets/minecraft/textures/models/armor/diamond_layer_1.png",
    "diamond_leggings":     "assets/minecraft/textures/models/armor/diamond_layer_2.png",
    "diamond_boots":        "assets/minecraft/textures/models/armor/diamond_layer_1.png",
    "netherite_helmet":     "assets/minecraft/textures/models/armor/netherite_layer_1.png",
    "netherite_chestplate": "assets/minecraft/textures/models/armor/netherite_layer_1.png",
    "netherite_leggings":   "assets/minecraft/textures/models/armor/netherite_layer_2.png",
    "netherite_boots":      "assets/minecraft/textures/models/armor/netherite_layer_1.png",
    # ── Blocks ───────────────────────────────────────────────────────────────
    "grass_top":     "assets/minecraft/textures/block/grass_block_top.png",
    "grass_side":    "assets/minecraft/textures/block/grass_block_side.png",
    "dirt":          "assets/minecraft/textures/block/dirt.png",
    "stone":         "assets/minecraft/textures/block/stone.png",
    "cobblestone":   "assets/minecraft/textures/block/cobblestone.png",
    "sand":          "assets/minecraft/textures/block/sand.png",
    "gravel":        "assets/minecraft/textures/block/gravel.png",
    "oak_log":       "assets/minecraft/textures/block/oak_log.png",
    "oak_planks":    "assets/minecraft/textures/block/oak_planks.png",
    "oak_leaves":    "assets/minecraft/textures/block/oak_leaves.png",
    "birch_log":     "assets/minecraft/textures/block/birch_log.png",
    "birch_planks":  "assets/minecraft/textures/block/birch_planks.png",
    "spruce_log":    "assets/minecraft/textures/block/spruce_log.png",
    "spruce_planks": "assets/minecraft/textures/block/spruce_planks.png",
    "netherrack":    "assets/minecraft/textures/block/netherrack.png",
    "obsidian":      "assets/minecraft/textures/block/obsidian.png",
    "bedrock":       "assets/minecraft/textures/block/bedrock.png",
    "tnt_top":       "assets/minecraft/textures/block/tnt_top.png",
    "tnt_side":      "assets/minecraft/textures/block/tnt_side.png",
    "crafting_table_top":  "assets/minecraft/textures/block/crafting_table_top.png",
    "crafting_table_side": "assets/minecraft/textures/block/crafting_table_side.png",
    "furnace_front": "assets/minecraft/textures/block/furnace_front.png",
    "chest_front":   "assets/minecraft/textures/block/chest_front.png",
    "bookshelf":     "assets/minecraft/textures/block/bookshelf.png",
    "diamond_ore":   "assets/minecraft/textures/block/diamond_ore.png",
    "iron_ore":      "assets/minecraft/textures/block/iron_ore.png",
    "gold_ore":      "assets/minecraft/textures/block/gold_ore.png",
    "coal_ore":      "assets/minecraft/textures/block/coal_ore.png",
    "emerald_ore":   "assets/minecraft/textures/block/emerald_ore.png",
    "redstone_ore":  "assets/minecraft/textures/block/redstone_ore.png",
    "lapis_ore":     "assets/minecraft/textures/block/lapis_ore.png",
    "ancient_debris":"assets/minecraft/textures/block/ancient_debris_top.png",
    "crying_obsidian":"assets/minecraft/textures/block/crying_obsidian.png",
    "glowstone":     "assets/minecraft/textures/block/glowstone.png",
    "soul_sand":     "assets/minecraft/textures/block/soul_sand.png",
    "end_stone":     "assets/minecraft/textures/block/end_stone.png",
    "purpur_block":  "assets/minecraft/textures/block/purpur_block.png",
    # ── Items ────────────────────────────────────────────────────────────────
    "apple":           "assets/minecraft/textures/item/apple.png",
    "golden_apple":    "assets/minecraft/textures/item/golden_apple.png",
    "enchanted_apple": "assets/minecraft/textures/item/enchanted_golden_apple.png",
    "bread":           "assets/minecraft/textures/item/bread.png",
    "cooked_beef":     "assets/minecraft/textures/item/cooked_beef.png",
    "beef":            "assets/minecraft/textures/item/beef.png",
    "cooked_chicken":  "assets/minecraft/textures/item/cooked_chicken.png",
    "diamond":         "assets/minecraft/textures/item/diamond.png",
    "emerald":         "assets/minecraft/textures/item/emerald.png",
    "iron_ingot":      "assets/minecraft/textures/item/iron_ingot.png",
    "gold_ingot":      "assets/minecraft/textures/item/gold_ingot.png",
    "netherite_ingot": "assets/minecraft/textures/item/netherite_ingot.png",
    "coal":            "assets/minecraft/textures/item/coal.png",
    "arrow":           "assets/minecraft/textures/item/arrow.png",
    "spectral_arrow":  "assets/minecraft/textures/item/spectral_arrow.png",
    "shield":          "assets/minecraft/textures/item/shield_base.png",
    "totem":           "assets/minecraft/textures/item/totem_of_undying.png",
    "ender_pearl":     "assets/minecraft/textures/item/ender_pearl.png",
    "ender_eye":       "assets/minecraft/textures/item/ender_eye.png",
    "blaze_rod":       "assets/minecraft/textures/item/blaze_rod.png",
    "nether_star":     "assets/minecraft/textures/item/nether_star.png",
    "heart_of_sea":    "assets/minecraft/textures/item/heart_of_the_sea.png",
    "elytra":          "assets/minecraft/textures/models/armor/elytra.png",
    # ── Mobs ─────────────────────────────────────────────────────────────────
    "zombie":          "assets/minecraft/textures/entity/zombie/zombie.png",
    "skeleton":        "assets/minecraft/textures/entity/skeleton/skeleton.png",
    "creeper":         "assets/minecraft/textures/entity/creeper/creeper.png",
    "enderman":        "assets/minecraft/textures/entity/enderman/enderman.png",
    "pig":             "assets/minecraft/textures/entity/pig/pig.png",
    "cow":             "assets/minecraft/textures/entity/cow/cow.png",
    "sheep":           "assets/minecraft/textures/entity/sheep/sheep.png",
    "chicken":         "assets/minecraft/textures/entity/chicken.png",
    "spider":          "assets/minecraft/textures/entity/spider/spider.png",
    "blaze":           "assets/minecraft/textures/entity/blaze.png",
    "ghast":           "assets/minecraft/textures/entity/ghast/ghast.png",
    "wither":          "assets/minecraft/textures/entity/wither/wither.png",
    "ender_dragon":    "assets/minecraft/textures/entity/enderdragon/dragon.png",
    "villager":        "assets/minecraft/textures/entity/villager/villager.png",
    "iron_golem":      "assets/minecraft/textures/entity/iron_golem/iron_golem.png",
    "wolf":            "assets/minecraft/textures/entity/wolf/wolf.png",
    "cat":             "assets/minecraft/textures/entity/cat/tabby.png",
    "horse":           "assets/minecraft/textures/entity/horse/horse_brown.png",
    "phantom":         "assets/minecraft/textures/entity/phantom.png",
    # ── GUI / HUD ─────────────────────────────────────────────────────────────
    "hotbar":          "assets/minecraft/textures/gui/widgets.png",
    "icons":           "assets/minecraft/textures/gui/icons.png",
    "crosshair":       "assets/minecraft/textures/gui/icons.png",
}

# Bedrock Edition paths
BEDROCK_PATHS = {
    "sword_wood":      "textures/items/wood_sword.png",
    "sword_stone":     "textures/items/stone_sword.png",
    "sword_iron":      "textures/items/iron_sword.png",
    "sword_gold":      "textures/items/gold_sword.png",
    "sword_diamond":   "textures/items/diamond_sword.png",
    "sword_netherite": "textures/items/netherite_sword.png",
    "bow":             "textures/items/bow_standby.png",
    "crossbow":        "textures/items/crossbow_standby.png",
    "trident":         "textures/items/trident.png",
    "axe_wood":        "textures/items/wood_axe.png",
    "axe_stone":       "textures/items/stone_axe.png",
    "axe_iron":        "textures/items/iron_axe.png",
    "axe_gold":        "textures/items/gold_axe.png",
    "axe_diamond":     "textures/items/diamond_axe.png",
    "axe_netherite":   "textures/items/netherite_axe.png",
    "pickaxe_wood":      "textures/items/wood_pickaxe.png",
    "pickaxe_stone":     "textures/items/stone_pickaxe.png",
    "pickaxe_iron":      "textures/items/iron_pickaxe.png",
    "pickaxe_gold":      "textures/items/gold_pickaxe.png",
    "pickaxe_diamond":   "textures/items/diamond_pickaxe.png",
    "pickaxe_netherite": "textures/items/netherite_pickaxe.png",
    "shovel_wood":       "textures/items/wood_shovel.png",
    "shovel_stone":      "textures/items/stone_shovel.png",
    "shovel_iron":       "textures/items/iron_shovel.png",
    "shovel_gold":       "textures/items/gold_shovel.png",
    "shovel_diamond":    "textures/items/diamond_shovel.png",
    "shovel_netherite":  "textures/items/netherite_shovel.png",
    "hoe_wood":          "textures/items/wood_hoe.png",
    "hoe_stone":         "textures/items/stone_hoe.png",
    "hoe_iron":          "textures/items/iron_hoe.png",
    "hoe_gold":          "textures/items/gold_hoe.png",
    "hoe_diamond":       "textures/items/diamond_hoe.png",
    "hoe_netherite":     "textures/items/netherite_hoe.png",
    "leather_helmet":       "textures/models/armor/leather_1.png",
    "leather_chestplate":   "textures/models/armor/leather_1.png",
    "leather_leggings":     "textures/models/armor/leather_2.png",
    "leather_boots":        "textures/models/armor/leather_1.png",
    "chainmail_helmet":     "textures/models/armor/chainmail_1.png",
    "chainmail_chestplate": "textures/models/armor/chainmail_1.png",
    "chainmail_leggings":   "textures/models/armor/chainmail_2.png",
    "chainmail_boots":      "textures/models/armor/chainmail_1.png",
    "iron_helmet":          "textures/models/armor/iron_1.png",
    "iron_chestplate":      "textures/models/armor/iron_1.png",
    "iron_leggings":        "textures/models/armor/iron_2.png",
    "iron_boots":           "textures/models/armor/iron_1.png",
    "gold_helmet":          "textures/models/armor/gold_1.png",
    "gold_chestplate":      "textures/models/armor/gold_1.png",
    "gold_leggings":        "textures/models/armor/gold_2.png",
    "gold_boots":           "textures/models/armor/gold_1.png",
    "diamond_helmet":       "textures/models/armor/diamond_1.png",
    "diamond_chestplate":   "textures/models/armor/diamond_1.png",
    "diamond_leggings":     "textures/models/armor/diamond_2.png",
    "diamond_boots":        "textures/models/armor/diamond_1.png",
    "netherite_helmet":     "textures/models/armor/netherite_1.png",
    "netherite_chestplate": "textures/models/armor/netherite_1.png",
    "netherite_leggings":   "textures/models/armor/netherite_2.png",
    "netherite_boots":      "textures/models/armor/netherite_1.png",
    "grass_top":     "textures/blocks/grass_top.png",
    "grass_side":    "textures/blocks/grass_side.png",
    "dirt":          "textures/blocks/dirt.png",
    "stone":         "textures/blocks/stone.png",
    "cobblestone":   "textures/blocks/cobblestone.png",
    "sand":          "textures/blocks/sand.png",
    "gravel":        "textures/blocks/gravel.png",
    "oak_log":       "textures/blocks/log_oak.png",
    "oak_planks":    "textures/blocks/planks_oak.png",
    "oak_leaves":    "textures/blocks/leaves_oak.png",
    "birch_log":     "textures/blocks/log_birch.png",
    "birch_planks":  "textures/blocks/planks_birch.png",
    "spruce_log":    "textures/blocks/log_spruce.png",
    "spruce_planks": "textures/blocks/planks_spruce.png",
    "netherrack":    "textures/blocks/netherrack.png",
    "obsidian":      "textures/blocks/obsidian.png",
    "bedrock":       "textures/blocks/bedrock.png",
    "tnt_top":       "textures/blocks/tnt_top.png",
    "tnt_side":      "textures/blocks/tnt_side.png",
    "crafting_table_top":  "textures/blocks/crafting_table_top.png",
    "crafting_table_side": "textures/blocks/crafting_table_side.png",
    "furnace_front": "textures/blocks/furnace_front_off.png",
    "chest_front":   "textures/blocks/chest_front.png",
    "bookshelf":     "textures/blocks/bookshelf.png",
    "diamond_ore":   "textures/blocks/diamond_ore.png",
    "iron_ore":      "textures/blocks/iron_ore.png",
    "gold_ore":      "textures/blocks/gold_ore.png",
    "coal_ore":      "textures/blocks/coal_ore.png",
    "emerald_ore":   "textures/blocks/emerald_ore.png",
    "redstone_ore":  "textures/blocks/redstone_ore.png",
    "lapis_ore":     "textures/blocks/lapis_ore.png",
    "ancient_debris":"textures/blocks/ancient_debris_top.png",
    "crying_obsidian":"textures/blocks/crying_obsidian.png",
    "glowstone":     "textures/blocks/glowstone.png",
    "soul_sand":     "textures/blocks/soul_sand.png",
    "end_stone":     "textures/blocks/end_stone.png",
    "purpur_block":  "textures/blocks/purpur_block.png",
    "apple":           "textures/items/apple.png",
    "golden_apple":    "textures/items/apple_golden.png",
    "enchanted_apple": "textures/items/apple_golden_enchanted.png",
    "bread":           "textures/items/bread.png",
    "cooked_beef":     "textures/items/beef_cooked.png",
    "beef":            "textures/items/beef_raw.png",
    "cooked_chicken":  "textures/items/chicken_cooked.png",
    "diamond":         "textures/items/diamond.png",
    "emerald":         "textures/items/emerald.png",
    "iron_ingot":      "textures/items/iron_ingot.png",
    "gold_ingot":      "textures/items/gold_ingot.png",
    "netherite_ingot": "textures/items/netherite_ingot.png",
    "coal":            "textures/items/coal.png",
    "arrow":           "textures/items/arrow.png",
    "spectral_arrow":  "textures/items/arrow_spectral.png",
    "shield":          "textures/models/shield/shield_base.png",
    "totem":           "textures/items/totem.png",
    "ender_pearl":     "textures/items/ender_pearl.png",
    "ender_eye":       "textures/items/ender_eye.png",
    "blaze_rod":       "textures/items/blaze_rod.png",
    "nether_star":     "textures/items/nether_star.png",
    "heart_of_sea":    "textures/items/heartofthesea_closed.png",
    "elytra":          "textures/models/armor/elytra.png",
    "zombie":          "textures/entity/zombie/zombie.png",
    "skeleton":        "textures/entity/skeleton/skeleton.png",
    "creeper":         "textures/entity/creeper/creeper.png",
    "enderman":        "textures/entity/enderman/enderman.png",
    "pig":             "textures/entity/pig/pig.png",
    "cow":             "textures/entity/cow/cow.png",
    "sheep":           "textures/entity/sheep/sheep.png",
    "chicken":         "textures/entity/chicken.png",
    "spider":          "textures/entity/spider/spider.png",
    "blaze":           "textures/entity/blaze.png",
    "ghast":           "textures/entity/ghast/ghast.png",
    "wither":          "textures/entity/wither/wither.png",
    "ender_dragon":    "textures/entity/enderdragon/dragon.png",
    "villager":        "textures/entity/villager/villager.png",
    "iron_golem":      "textures/entity/iron_golem/iron_golem.png",
    "wolf":            "textures/entity/wolf/wolf.png",
    "cat":             "textures/entity/cat/tabby.png",
    "horse":           "textures/entity/horse/horse_brown.png",
    "phantom":         "textures/entity/phantom.png",
    "hotbar":          "textures/ui/widgets.png",
    "icons":           "textures/ui/icons.png",
    "crosshair":       "textures/ui/icons.png",
}

# ─── SOUND MAPPINGS ────────────────────────────────────────────────────────────
# Внутреннее имя → (java_path, bedrock_path, sounds.json event)
SOUNDS = {
    "hurt":        ("assets/minecraft/sounds/damage/hit1.ogg",          "sounds/damage/hit1.ogg",         "entity.player.hurt"),
    "death":       ("assets/minecraft/sounds/damage/hit3.ogg",          "sounds/damage/hit3.ogg",         "entity.player.death"),
    "explosion":   ("assets/minecraft/sounds/random/explode1.ogg",      "sounds/random/explode1.ogg",     "entity.generic.explode"),
    "eat":         ("assets/minecraft/sounds/random/eat1.ogg",          "sounds/random/eat1.ogg",         "entity.player.burp"),
    "levelup":     ("assets/minecraft/sounds/random/levelup.ogg",       "sounds/random/levelup.ogg",      "entity.player.levelup"),
    "click":       ("assets/minecraft/sounds/random/click.ogg",         "sounds/random/click.ogg",        "ui.button.click"),
    "swim":        ("assets/minecraft/sounds/liquid/swim1.ogg",         "sounds/liquid/swim1.ogg",        "entity.player.swim"),
    "anvil":       ("assets/minecraft/sounds/random/anvil_use.ogg",     "sounds/random/anvil_use.ogg",    "block.anvil.use"),
    "chest_open":  ("assets/minecraft/sounds/random/chestopen.ogg",     "sounds/random/chestopen.ogg",    "block.chest.open"),
    "chest_close": ("assets/minecraft/sounds/random/chestclosed.ogg",   "sounds/random/chestclosed.ogg",  "block.chest.close"),
    "bow_shoot":   ("assets/minecraft/sounds/random/bow.ogg",           "sounds/random/bow.ogg",          "entity.arrow.shoot"),
    "sword_hit":   ("assets/minecraft/sounds/random/classic_hurt.ogg",  "sounds/random/classic_hurt.ogg", "entity.player.attack.strong"),
    "portal":      ("assets/minecraft/sounds/portal/portal.ogg",        "sounds/portal/portal.ogg",       "block.portal.ambient"),
    "enderman_scream": ("assets/minecraft/sounds/mob/endermen/scream1.ogg", "sounds/mob/endermen/scream1.ogg", "entity.enderman.scream"),
    "creeper_hiss":    ("assets/minecraft/sounds/mob/creeper/say1.ogg", "sounds/mob/creeper/say1.ogg",    "entity.creeper.primed"),
    "villager":        ("assets/minecraft/sounds/mob/villager/idle1.ogg","sounds/mob/villager/idle1.ogg",  "entity.villager.ambient"),
    "thunder":         ("assets/minecraft/sounds/ambient/weather/thunder1.ogg","sounds/ambient/weather/thunder1.ogg","entity.lightning_bolt.thunder"),
    "rain":            ("assets/minecraft/sounds/ambient/weather/rain.ogg","sounds/ambient/weather/rain.ogg","weather.rain"),
    "fire":            ("assets/minecraft/sounds/fire/fire.ogg",        "sounds/fire/fire.ogg",           "block.fire.ambient"),
    "splash":          ("assets/minecraft/sounds/liquid/splash.ogg",    "sounds/liquid/splash.ogg",       "entity.splash_potion.throw"),
}

ITEM_LABELS = {
    # Weapons
    "sword_wood":      "⚔️ Деревянный меч",
    "sword_stone":     "⚔️ Каменный меч",
    "sword_iron":      "⚔️ Железный меч",
    "sword_gold":      "⚔️ Золотой меч",
    "sword_diamond":   "⚔️ Алмазный меч",
    "sword_netherite": "⚔️ Незеритовый меч",
    "bow":             "🏹 Лук",
    "crossbow":        "🏹 Арбалет",
    "trident":         "🔱 Трезубец",
    "axe_wood":        "🪓 Деревянный топор",
    "axe_stone":       "🪓 Каменный топор",
    "axe_iron":        "🪓 Железный топор",
    "axe_gold":        "🪓 Золотой топор",
    "axe_diamond":     "🪓 Алмазный топор",
    "axe_netherite":   "🪓 Незеритовый топор",
    # Tools
    "pickaxe_wood":      "⛏ Деревянная кирка",
    "pickaxe_stone":     "⛏ Каменная кирка",
    "pickaxe_iron":      "⛏ Железная кирка",
    "pickaxe_gold":      "⛏ Золотая кирка",
    "pickaxe_diamond":   "⛏ Алмазная кирка",
    "pickaxe_netherite": "⛏ Незеритовая кирка",
    "shovel_wood":       "🪣 Деревянная лопата",
    "shovel_stone":      "🪣 Каменная лопата",
    "shovel_iron":       "🪣 Железная лопата",
    "shovel_gold":       "🪣 Золотая лопата",
    "shovel_diamond":    "🪣 Алмазная лопата",
    "shovel_netherite":  "🪣 Незеритовая лопата",
    "hoe_wood":          "🌾 Деревянная мотыга",
    "hoe_stone":         "🌾 Каменная мотыга",
    "hoe_iron":          "🌾 Железная мотыга",
    "hoe_gold":          "🌾 Золотая мотыга",
    "hoe_diamond":       "🌾 Алмазная мотыга",
    "hoe_netherite":     "🌾 Незеритовая мотыга",
    # Armor
    "leather_helmet":       "🪖 Кожаный шлем",
    "leather_chestplate":   "👕 Кожаный нагрудник",
    "leather_leggings":     "👖 Кожаные поножи",
    "leather_boots":        "👟 Кожаные ботинки",
    "chainmail_helmet":     "🪖 Кольчужный шлем",
    "chainmail_chestplate": "👕 Кольчужный нагрудник",
    "chainmail_leggings":   "👖 Кольчужные поножи",
    "chainmail_boots":      "👟 Кольчужные ботинки",
    "iron_helmet":          "🪖 Железный шлем",
    "iron_chestplate":      "👕 Железный нагрудник",
    "iron_leggings":        "👖 Железные поножи",
    "iron_boots":           "👟 Железные ботинки",
    "gold_helmet":          "🪖 Золотой шлем",
    "gold_chestplate":      "👕 Золотой нагрудник",
    "gold_leggings":        "👖 Золотые поножи",
    "gold_boots":           "👟 Золотые ботинки",
    "diamond_helmet":       "🪖 Алмазный шлем",
    "diamond_chestplate":   "👕 Алмазный нагрудник",
    "diamond_leggings":     "👖 Алмазные поножи",
    "diamond_boots":        "👟 Алмазные ботинки",
    "netherite_helmet":     "🪖 Незеритовый шлем",
    "netherite_chestplate": "👕 Незеритовый нагрудник",
    "netherite_leggings":   "👖 Незеритовые поножи",
    "netherite_boots":      "👟 Незеритовые ботинки",
    # Blocks
    "grass_top":     "🌿 Трава (верх)",
    "grass_side":    "🌿 Трава (сторона)",
    "dirt":          "🟤 Земля",
    "stone":         "🪨 Камень",
    "cobblestone":   "🪨 Булыжник",
    "sand":          "🏜 Песок",
    "gravel":        "⬜ Гравий",
    "oak_log":       "🪵 Дубовое бревно",
    "oak_planks":    "🪵 Дубовые доски",
    "oak_leaves":    "🍃 Дубовые листья",
    "birch_log":     "🪵 Берёзовое бревно",
    "birch_planks":  "🪵 Берёзовые доски",
    "spruce_log":    "🪵 Еловое бревно",
    "spruce_planks": "🪵 Еловые доски",
    "netherrack":    "🔴 Незерак",
    "obsidian":      "⬛ Обсидиан",
    "bedrock":       "⬛ Бедрок",
    "tnt_top":       "💣 TNT (верх)",
    "tnt_side":      "💣 TNT (сторона)",
    "crafting_table_top":  "🔨 Верстак (верх)",
    "crafting_table_side": "🔨 Верстак (сторона)",
    "furnace_front": "🔥 Печь (фронт)",
    "chest_front":   "📦 Сундук",
    "bookshelf":     "📚 Книжная полка",
    "diamond_ore":   "💎 Алмазная руда",
    "iron_ore":      "⛏ Железная руда",
    "gold_ore":      "🟡 Золотая руда",
    "coal_ore":      "⬛ Угольная руда",
    "emerald_ore":   "💚 Изумрудная руда",
    "redstone_ore":  "🔴 Редстоун руда",
    "lapis_ore":     "🔵 Лазурит руда",
    "ancient_debris":"🟫 Древние обломки",
    "crying_obsidian":"💜 Плачущий обсидиан",
    "glowstone":     "💛 Светящийся камень",
    "soul_sand":     "💀 Песок душ",
    "end_stone":     "🟨 Камень края",
    "purpur_block":  "🟣 Пурпурный блок",
    # Items
    "apple":           "🍎 Яблоко",
    "golden_apple":    "🍎 Золотое яблоко",
    "enchanted_apple": "✨ Зачарованное яблоко",
    "bread":           "🍞 Хлеб",
    "cooked_beef":     "🥩 Жареная говядина",
    "beef":            "🥩 Сырая говядина",
    "cooked_chicken":  "🍗 Жареная курица",
    "diamond":         "💎 Алмаз",
    "emerald":         "💚 Изумруд",
    "iron_ingot":      "🔩 Железный слиток",
    "gold_ingot":      "🟡 Золотой слиток",
    "netherite_ingot": "⬛ Незеритовый слиток",
    "coal":            "⬛ Уголь",
    "arrow":           "➡️ Стрела",
    "spectral_arrow":  "✨ Призрачная стрела",
    "shield":          "🛡 Щит",
    "totem":           "🗿 Тотем бессмертия",
    "ender_pearl":     "🟢 Жемчуг эндера",
    "ender_eye":       "👁 Глаз эндера",
    "blaze_rod":       "🔥 Стержень Иблиса",
    "nether_star":     "⭐ Звезда Незера",
    "heart_of_sea":    "💙 Сердце моря",
    "elytra":          "🦋 Элитры",
    # Mobs
    "zombie":          "🧟 Зомби",
    "skeleton":        "💀 Скелет",
    "creeper":         "💚 Крипер",
    "enderman":        "🕴 Эндермен",
    "pig":             "🐷 Свинья",
    "cow":             "🐄 Корова",
    "sheep":           "🐑 Овца",
    "chicken":         "🐔 Курица",
    "spider":          "🕷 Паук",
    "blaze":           "🔥 Иблис",
    "ghast":           "👻 Гаст",
    "wither":          "💀 Иссушитель",
    "ender_dragon":    "🐉 Дракон Края",
    "villager":        "👨‍🌾 Житель",
    "iron_golem":      "🤖 Железный голем",
    "wolf":            "🐺 Волк",
    "cat":             "🐱 Кошка",
    "horse":           "🐴 Лошадь",
    "phantom":         "👁 Фантом",
    # GUI
    "hotbar":   "🎮 Хотбар (панель предметов)",
    "icons":    "❤️ Иконки (здоровье/голод/броня)",
    "crosshair":"➕ Прицел",
}

SOUND_LABELS = {
    "hurt":            "💥 Урон игрока",
    "death":           "💀 Смерть игрока",
    "explosion":       "💣 Взрыв",
    "eat":             "🍖 Еда",
    "levelup":         "⬆️ Повышение уровня",
    "click":           "🖱 Клик кнопки",
    "swim":            "🏊 Плавание",
    "anvil":           "⚒ Наковальня",
    "chest_open":      "📦 Открытие сундука",
    "chest_close":     "📦 Закрытие сундука",
    "bow_shoot":       "🏹 Выстрел из лука",
    "sword_hit":       "⚔️ Удар мечом",
    "portal":          "🌀 Портал",
    "enderman_scream": "😱 Крик Эндермена",
    "creeper_hiss":    "💚 Шипение крипера",
    "villager":        "👨‍🌾 Житель (речь)",
    "thunder":         "⛈ Гром",
    "rain":            "🌧 Дождь",
    "fire":            "🔥 Огонь",
    "splash":          "💦 Всплеск",
}

CATEGORIES = {
    "weapons": ("⚔️ Оружие",      ["sword_wood","sword_stone","sword_iron","sword_gold","sword_diamond","sword_netherite","bow","crossbow","trident","axe_wood","axe_stone","axe_iron","axe_gold","axe_diamond","axe_netherite"]),
    "tools":   ("🔨 Инструменты", ["pickaxe_wood","pickaxe_stone","pickaxe_iron","pickaxe_gold","pickaxe_diamond","pickaxe_netherite","shovel_wood","shovel_stone","shovel_iron","shovel_gold","shovel_diamond","shovel_netherite","hoe_wood","hoe_stone","hoe_iron","hoe_gold","hoe_diamond","hoe_netherite"]),
    "armor":   ("🛡 Броня",        ["leather_helmet","leather_chestplate","leather_leggings","leather_boots","chainmail_helmet","chainmail_chestplate","chainmail_leggings","chainmail_boots","iron_helmet","iron_chestplate","iron_leggings","iron_boots","gold_helmet","gold_chestplate","gold_leggings","gold_boots","diamond_helmet","diamond_chestplate","diamond_leggings","diamond_boots","netherite_helmet","netherite_chestplate","netherite_leggings","netherite_boots"]),
    "blocks":  ("🧱 Блоки",        ["grass_top","grass_side","dirt","stone","cobblestone","sand","gravel","oak_log","oak_planks","oak_leaves","birch_log","birch_planks","spruce_log","spruce_planks","netherrack","obsidian","bedrock","tnt_top","tnt_side","crafting_table_top","crafting_table_side","furnace_front","chest_front","bookshelf","diamond_ore","iron_ore","gold_ore","coal_ore","emerald_ore","redstone_ore","lapis_ore","ancient_debris","crying_obsidian","glowstone","soul_sand","end_stone","purpur_block"]),
    "items":   ("🎒 Предметы",     ["apple","golden_apple","enchanted_apple","bread","cooked_beef","beef","cooked_chicken","diamond","emerald","iron_ingot","gold_ingot","netherite_ingot","coal","arrow","spectral_arrow","shield","totem","ender_pearl","ender_eye","blaze_rod","nether_star","heart_of_sea","elytra"]),
    "mobs":    ("🐷 Мобы",         ["zombie","skeleton","creeper","enderman","pig","cow","sheep","chicken","spider","blaze","ghast","wither","ender_dragon","villager","iron_golem","wolf","cat","horse","phantom"]),
    "gui":     ("🎮 Интерфейс",    ["hotbar","icons","crosshair"]),
}

# ─── FSM STATES ────────────────────────────────────────────────────────────────
class PackStates(StatesGroup):
    choose_type     = State()
    choose_version  = State()
    enter_name      = State()
    enter_desc      = State()
    upload_icon     = State()
    choose_category = State()
    choose_item     = State()
    upload_file     = State()
    add_more        = State()

class AdminStates(StatesGroup):
    give_sub_id   = State()
    give_sub_type = State()

# ─── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль",          callback_data="profile"),
         InlineKeyboardButton(text="ℹ️ О боте",           callback_data="about")],
        [InlineKeyboardButton(text="💎 Купить подписку",  callback_data="buy_sub")],
        [InlineKeyboardButton(text="🎨 Создать ресурс-пак", callback_data="create_pack")],
    ])

def back_kb(cb="main_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb)]
    ])

def buy_sub_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Stars — Неделя (50⭐)",     callback_data="pay_stars_week")],
        [InlineKeyboardButton(text="⭐ Stars — Навсегда (150⭐)",  callback_data="pay_stars_forever")],
        [InlineKeyboardButton(text="💰 Крипто — Неделя ($1)",     callback_data="pay_crypto_week")],
        [InlineKeyboardButton(text="💰 Крипто — Навсегда ($3)",   callback_data="pay_crypto_forever")],
        [InlineKeyboardButton(text="⬅️ Назад",                    callback_data="main_menu")],
    ])

def pack_type_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Текстуры", callback_data="ptype_texture")],
        [InlineKeyboardButton(text="🔊 Звуки",    callback_data="ptype_sound")],
        [InlineKeyboardButton(text="⬅️ Назад",   callback_data="main_menu")],
    ])

def version_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☕ Java Edition",   callback_data="ver_java")],
        [InlineKeyboardButton(text="📱 Bedrock Edition", callback_data="ver_bedrock")],
        [InlineKeyboardButton(text="⬅️ Назад",          callback_data="create_pack")],
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
        [InlineKeyboardButton(text="📦 Скачать пак",           callback_data="finish_pack")],
    ])

def add_more_sound_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ещё звук", callback_data="add_more_yes")],
        [InlineKeyboardButton(text="📦 Скачать пак",       callback_data="finish_pack")],
    ])

def skip_icon_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_icon")],
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика",      callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Выдать подписку", callback_data="admin_give_sub")],
        [InlineKeyboardButton(text="⬅️ В меню",         callback_data="main_menu")],
    ])

# ─── PACK BUILDER ──────────────────────────────────────────────────────────────
def _make_sounds_json(sound_keys: list) -> str:
    """Генерирует sounds.json для Java Edition."""
    result = {}
    for key in sound_keys:
        event = SOUNDS[key][2]
        java_path = SOUNDS[key][0]
        # убираем префикс assets/minecraft/sounds/ и .ogg
        rel = java_path.replace("assets/minecraft/sounds/", "").replace(".ogg", "")
        result[event] = {
            "sounds": [{"name": rel, "stream": False}],
            "replace": True,
        }
    return json.dumps(result, indent=2, ensure_ascii=False)

def _make_sound_definitions_json(sound_keys: list) -> str:
    """Генерирует sound_definitions.json для Bedrock Edition."""
    result = {"format_version": "1.14.0", "sound_definitions": {}}
    for key in sound_keys:
        event = SOUNDS[key][2]
        bed_path = SOUNDS[key][1].replace(".ogg", "")
        result["sound_definitions"][event] = {
            "category": "neutral",
            "sounds": [{"name": bed_path}],
        }
    return json.dumps(result, indent=2, ensure_ascii=False)

def build_java_pack(texture_files: dict, sound_files: dict,
                    pack_name: str = "CustomPack", pack_desc: str = "Custom Pack by PackCraftBot",
                    pack_icon: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        meta = json.dumps({
            "pack": {"pack_format": 15, "description": f"§6{pack_name} §7— {pack_desc}"}
        }, indent=2)
        zf.writestr("pack.mcmeta", meta)

        if pack_icon:
            # pack.png должен быть 64x64 PNG
            icon_img = Image.open(io.BytesIO(pack_icon)).convert("RGBA").resize((64, 64), Image.LANCZOS)
            icon_buf = io.BytesIO()
            icon_img.save(icon_buf, format="PNG")
            zf.writestr("pack.png", icon_buf.getvalue())

        # Текстуры
        for key, data in texture_files.items():
            path = JAVA_PATHS.get(key)
            if path:
                zf.writestr(path, data)

        # Звуки + sounds.json
        if sound_files:
            for key, data in sound_files.items():
                path = SOUNDS[key][0]
                zf.writestr(path, data)
            zf.writestr("assets/minecraft/sounds.json", _make_sounds_json(list(sound_files.keys())))

    return buf.getvalue()

def build_bedrock_pack(texture_files: dict, sound_files: dict,
                       pack_name: str = "CustomPack", pack_desc: str = "Custom Pack by PackCraftBot",
                       pack_icon: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = json.dumps({
            "format_version": 2,
            "header": {
                "description": pack_desc,
                "name": pack_name,
                "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "version": [1, 0, 0],
                "min_engine_version": [1, 16, 0],
            },
            "modules": [{"description": "Resource pack", "type": "resources",
                          "uuid": "b2c3d4e5-f6a7-8901-bcde-f12345678901", "version": [1, 0, 0]}],
        }, indent=2)
        zf.writestr("manifest.json", manifest)

        if pack_icon:
            # pack_icon.png для Bedrock — 256x256
            icon_img = Image.open(io.BytesIO(pack_icon)).convert("RGBA").resize((256, 256), Image.LANCZOS)
            icon_buf = io.BytesIO()
            icon_img.save(icon_buf, format="PNG")
            zf.writestr("pack_icon.png", icon_buf.getvalue())

        # Текстуры
        for key, data in texture_files.items():
            path = BEDROCK_PATHS.get(key)
            if path:
                zf.writestr(path, data)

        # Звуки + sound_definitions.json
        if sound_files:
            for key, data in sound_files.items():
                path = SOUNDS[key][1]
                zf.writestr(path, data)
            zf.writestr("sounds/sound_definitions.json", _make_sound_definitions_json(list(sound_files.keys())))

    return buf.getvalue()

def resize_texture(img_bytes: bytes, size: int = 16) -> bytes:
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    # Сохраняем пропорции для entity-текстур (обычно прямоугольные)
    w, h = img.size
    if w == h:
        img = img.resize((size, size), Image.NEAREST)
    else:
        ratio = h / w
        img = img.resize((size, int(size * ratio)), Image.NEAREST)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

# ─── BOT ───────────────────────────────────────────────────────────────────────
bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# ─── /start ────────────────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    upsert_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "🎮 <b>Добро пожаловать в PackCraftBot!</b>\n\n"
        "Создай кастомный ресурс-пак для Minecraft прямо здесь:\n"
        "• 🖼 Текстуры — блоки, мобы, броня, инструменты, GUI\n"
        "• 🔊 Звуки — замени любой звук игры\n"
        "• ☕ Java Edition и 📱 Bedrock Edition\n\n"
        "🆓 <b>Бесплатно:</b> 1 пак\n"
        "💎 <b>Подписка:</b> безлимитные паки!\n\n"
        "Выбери действие:",
        reply_markup=main_menu_kb(), parse_mode="HTML"
    )

# ─── MAIN MENU CALLBACKS ───────────────────────────────────────────────────────
@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await cq.message.edit_text("🎮 <b>Главное меню</b>\n\nВыбери действие:",
                                reply_markup=main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "profile")
async def cb_profile(cq: CallbackQuery):
    uid = cq.from_user.id
    u   = get_user(uid)
    sub = is_subscribed(uid)
    sub_type  = u.get("sub_type", "free") if u else "free"
    sub_until = u.get("sub_until") if u else None
    packs     = u.get("packs_created", 0) if u else 0

    if sub_type == "forever":
        sub_text = "♾ Навсегда"
    elif sub_type == "week" and sub_until:
        sub_text = f"📅 До {sub_until[:10]}"
    else:
        sub_text = "❌ Нет подписки"

    await cq.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📦 Паков создано: <b>{packs}</b>\n"
        f"💎 Подписка: <b>{sub_text}</b>\n",
        reply_markup=back_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "about")
async def cb_about(cq: CallbackQuery):
    await cq.message.edit_text(
        "ℹ️ <b>О боте</b>\n\n"
        "🎮 <b>PackCraftBot</b> — создай ресурс-пак для Minecraft прямо в Telegram!\n\n"
        "✅ Java Edition и Bedrock Edition\n"
        "✅ Текстуры: броня, оружие, инструменты, блоки, мобы, GUI\n"
        "✅ Звуки: урон, взрывы, мобы, окружение и многое другое\n"
        "✅ Автоматическая сборка .zip / .mcpack\n"
        "✅ sounds.json генерируется автоматически\n\n"
        "💎 <b>Тарифы:</b>\n"
        "• Бесплатно — 1 пак\n"
        "• Неделя — 50⭐ или $1\n"
        "• Навсегда — 150⭐ или $3\n\n"
        "📩 Поддержка: @PackCraftBot",
        reply_markup=back_kb(), parse_mode="HTML"
    )

# ─── SUBSCRIPTION ──────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "buy_sub")
async def cb_buy_sub(cq: CallbackQuery):
    await cq.message.edit_text(
        "💎 <b>Выбери способ оплаты и тариф:</b>",
        reply_markup=buy_sub_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data.in_({"pay_stars_week", "pay_stars_forever"}))
async def cb_pay_stars(cq: CallbackQuery):
    week = cq.data == "pay_stars_week"
    await bot.send_invoice(
        chat_id=cq.from_user.id,
        title="⭐ Подписка на неделю" if week else "⭐ Подписка навсегда",
        description="Безлимитные ресурс-паки на 7 дней" if week else "Безлимитные ресурс-паки навсегда",
        payload="stars_week" if week else "stars_forever",
        currency="XTR",
        prices=[LabeledPrice(label="Неделя" if week else "Навсегда", amount=WEEK_STARS if week else FOREVER_STARS)]
    )
    await cq.answer()

@dp.pre_checkout_query()
async def pre_checkout(pcq: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pcq.id, ok=True)

@dp.message(F.successful_payment)
async def payment_done(message: Message):
    uid     = message.from_user.id
    payload = message.successful_payment.invoice_payload
    amount  = message.successful_payment.total_amount
    log_payment(uid, "stars", amount, payload)
    sub_type = "forever" if "forever" in payload else "week"
    give_sub(uid, sub_type)
    label = "навсегда ♾" if sub_type == "forever" else "на неделю 📅"
    await message.answer(
        f"✅ <b>Подписка {label} активирована!</b>\n\nТеперь создавай неограниченно паков 🎉",
        parse_mode="HTML", reply_markup=main_menu_kb()
    )

@dp.callback_query(F.data.in_({"pay_crypto_week", "pay_crypto_forever"}))
async def cb_pay_crypto(cq: CallbackQuery):
    if not CRYPTO_BOT_TOKEN:
        await cq.answer("Крипто-оплата временно недоступна", show_alert=True)
        return
    import aiohttp
    plan   = "week" if "week" in cq.data else "forever"
    amount = WEEK_CRYPTO_USD if plan == "week" else FOREVER_CRYPTO_USD
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
            json={
                "asset": "USDT", "amount": str(amount),
                "description": f"PackCraftBot — {'неделя' if plan=='week' else 'навсегда'}",
                "payload": f"crypto_{plan}_{cq.from_user.id}",
                "paid_btn_name": "callback",
                "paid_btn_url": f"https://t.me/{(await bot.get_me()).username}",
            }
        )
        data = await resp.json()
    if data.get("ok"):
        pay_url    = data["result"]["pay_url"]
        invoice_id = data["result"]["invoice_id"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Оплатить", url=pay_url)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_crypto_{invoice_id}_{plan}")],
        ])
        await cq.message.edit_text(
            f"💰 <b>Оплата через CryptoBot</b>\n\nСумма: <b>${amount} USDT</b>\n\n"
            "Нажми кнопку для оплаты, затем «Я оплатил»",
            reply_markup=kb, parse_mode="HTML"
        )
    else:
        await cq.answer("Ошибка создания счёта", show_alert=True)

@dp.callback_query(F.data.startswith("check_crypto_"))
async def cb_check_crypto(cq: CallbackQuery):
    parts      = cq.data.split("_")
    invoice_id = parts[2]
    plan       = parts[3]
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
            await cq.answer("Оплата ещё не прошла. Подожди немного.", show_alert=True)
    else:
        await cq.answer("Ошибка проверки. Попробуй позже.", show_alert=True)

# ─── CREATE PACK ───────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "create_pack")
async def cb_create_pack(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    if not is_subscribed(uid) and not has_free_pack(uid):
        await cq.message.edit_text(
            "❌ <b>Лимит исчерпан</b>\n\nБесплатно можно создать только 1 пак.\n"
            "Купи подписку для безлимита!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_sub")],
                [InlineKeyboardButton(text="⬅️ Назад",           callback_data="main_menu")],
            ]),
            parse_mode="HTML"
        )
        return
    await state.set_state(PackStates.choose_type)
    await state.update_data(texture_files={}, sound_files={})
    await cq.message.edit_text(
        "🎨 <b>Что хочешь заменить?</b>\n\n"
        "Можно добавить и текстуры, и звуки — они всё войдут в один пак.",
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
    await state.set_state(PackStates.enter_name)
    await cq.message.edit_text(
        "✏️ <b>Введи название ресурс-пака:</b>\n\n"
        "Например: <i>MyAwesomePack</i>\n"
        "(не более 40 символов)",
        parse_mode="HTML"
    )

@dp.message(PackStates.enter_name)
async def enter_name(message: Message, state: FSMContext):
    name = message.text.strip()[:40] if message.text else "CustomPack"
    await state.update_data(pack_name=name)
    await state.set_state(PackStates.enter_desc)
    await message.answer(
        "📝 <b>Введи описание ресурс-пака:</b>\n\n"
        "Например: <i>Крутые текстуры для PvP</i>\n"
        "(не более 100 символов)",
        parse_mode="HTML"
    )

@dp.message(PackStates.enter_desc)
async def enter_desc(message: Message, state: FSMContext):
    desc = message.text.strip()[:100] if message.text else "Custom Pack by PackCraftBot"
    await state.update_data(pack_desc=desc)
    await state.set_state(PackStates.upload_icon)
    await message.answer(
        "🖼 <b>Отправь аватарку ресурс-пака</b> (PNG или JPG)\n\n"
        "Это иконка, которая будет отображаться в списке пакетов в игре.\n"
        "Рекомендуемый размер: 64×64 (Java) или 256×256 (Bedrock).\n\n"
        "Можно пропустить:",
        reply_markup=skip_icon_kb(), parse_mode="HTML"
    )

@dp.message(PackStates.upload_icon, F.photo | F.document)
async def upload_icon(message: Message, state: FSMContext):
    if message.photo:
        file = await bot.get_file(message.photo[-1].file_id)
    elif message.document:
        fname = (message.document.file_name or "").lower()
        if not (fname.endswith(".png") or fname.endswith(".jpg") or fname.endswith(".jpeg")):
            await message.answer("❌ Отправь PNG или JPG файл, или нажми «Пропустить»",
                                  reply_markup=skip_icon_kb())
            return
        file = await bot.get_file(message.document.file_id)
    else:
        await message.answer("❌ Отправь изображение PNG/JPG, или нажми «Пропустить»",
                              reply_markup=skip_icon_kb())
        return

    buf = io.BytesIO()
    await bot.download_file(file.file_path, buf)
    await state.update_data(pack_icon=buf.getvalue())
    await _proceed_to_category(message, state)

@dp.callback_query(PackStates.upload_icon, F.data == "skip_icon")
async def cb_skip_icon(cq: CallbackQuery, state: FSMContext):
    await state.update_data(pack_icon=None)
    await _proceed_to_category(cq.message, state, edit=True)

async def _proceed_to_category(msg, state: FSMContext, edit: bool = False):
    data = await state.get_data()
    await state.set_state(PackStates.choose_category)
    text_sound = "🔊 <b>Выбери звук для замены:</b>"
    text_tex   = "📂 <b>Выбери категорию:</b>"
    if data["pack_type"] == "sound":
        if edit:
            await msg.edit_text(text_sound, reply_markup=sound_category_kb(), parse_mode="HTML")
        else:
            await msg.answer(text_sound, reply_markup=sound_category_kb(), parse_mode="HTML")
    else:
        if edit:
            await msg.edit_text(text_tex, reply_markup=category_kb(data["pack_type"]), parse_mode="HTML")
        else:
            await msg.answer(text_tex, reply_markup=category_kb(data["pack_type"]), parse_mode="HTML")

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
    await state.update_data(current_item=snd_key, pack_type="sound")
    await state.set_state(PackStates.upload_file)
    label = SOUND_LABELS.get(snd_key, snd_key)
    await cq.message.edit_text(
        f"🔊 <b>{label}</b>\n\nОтправь файл звука в формате <b>.ogg</b>\n\n"
        "💡 Конвертировать mp3→ogg можно на сайте <a href='https://audio.online-convert.com/ru/convert-to-ogg'>online-convert.com</a>",
        reply_markup=back_kb("create_pack"), parse_mode="HTML", disable_web_page_preview=True
    )

@dp.callback_query(PackStates.choose_item, F.data.startswith("item_"))
async def cb_item(cq: CallbackQuery, state: FSMContext):
    item_key = cq.data[5:]
    await state.update_data(current_item=item_key)
    await state.set_state(PackStates.upload_file)
    label = ITEM_LABELS.get(item_key, item_key)
    await cq.message.edit_text(
        f"🖼 <b>{label}</b>\n\nОтправь текстуру в формате <b>PNG</b>\n"
        "Поддерживаемые размеры: 16×16, 32×32, 64×64, 128×128",
        reply_markup=back_kb("create_pack"), parse_mode="HTML"
    )

@dp.message(PackStates.upload_file, F.photo | F.document)
async def upload_file(message: Message, state: FSMContext):
    data       = await state.get_data()
    pack_type  = data.get("pack_type", "texture")
    item_key   = data.get("current_item")
    tex_files  = data.get("texture_files", {})
    snd_files  = data.get("sound_files", {})

    if pack_type == "sound":
        if not message.document:
            await message.answer("❌ Отправь файл .ogg (не фото)")
            return
        doc = message.document
        if not doc.file_name.lower().endswith(".ogg"):
            await message.answer("❌ Нужен файл в формате .ogg\n\n"
                                  "Конвертировать можно на audio.online-convert.com")
            return
        file = await bot.get_file(doc.file_id)
        buf  = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        snd_files[item_key] = buf.getvalue()
        await state.update_data(sound_files=snd_files)
        label = SOUND_LABELS.get(item_key, item_key)
        await message.answer(
            f"✅ <b>{label}</b> добавлен!\n\nЧто дальше?",
            reply_markup=add_more_sound_kb(), parse_mode="HTML"
        )
    else:
        if message.photo:
            file = await bot.get_file(message.photo[-1].file_id)
        elif message.document:
            file = await bot.get_file(message.document.file_id)
        else:
            await message.answer("❌ Отправь PNG файл")
            return
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        img_bytes = resize_texture(buf.getvalue(), 16)
        tex_files[item_key] = img_bytes
        await state.update_data(texture_files=tex_files)
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
    data      = await state.get_data()
    version   = data.get("version", "java")
    tex_files = data.get("texture_files", {})
    snd_files = data.get("sound_files", {})
    pack_name = data.get("pack_name", "CustomPack")
    pack_desc = data.get("pack_desc", "Custom Pack by PackCraftBot")
    pack_icon = data.get("pack_icon", None)
    uid       = cq.from_user.id

    if not tex_files and not snd_files:
        await cq.answer("Добавь хотя бы один файл!", show_alert=True)
        return

    await cq.message.edit_text("⏳ Собираю ресурс-пак...")

    if version == "java":
        pack_bytes = build_java_pack(tex_files, snd_files, pack_name, pack_desc, pack_icon)
        filename   = f"{pack_name}_Java.zip"
    else:
        pack_bytes = build_bedrock_pack(tex_files, snd_files, pack_name, pack_desc, pack_icon)
        filename   = f"{pack_name}_Bedrock.mcpack"

    increment_packs(uid)
    await state.clear()

    total = len(tex_files) + len(snd_files)
    install_text = (
        "• Помести .zip в папку resourcepacks\n"
        "• Зайди в игру → Настройки → Пакеты ресурсов"
        if version == "java" else
        "• Переименуй файл в .mcpack и открой\n"
        "• Bedrock установит автоматически"
    )
    icon_note = " 🖼 Аватарка включена" if pack_icon else ""
    await bot.send_document(
        chat_id=uid,
        document=BufferedInputFile(pack_bytes, filename=filename),
        caption=(
            f"✅ <b>Ресурс-пак готов!</b>\n\n"
            f"📛 Название: <b>{pack_name}</b>\n"
            f"📝 Описание: <i>{pack_desc}</i>\n"
            f"📦 Версия: {'☕ Java' if version=='java' else '📱 Bedrock'}\n"
            f"🖼 Текстур: {len(tex_files)}\n"
            f"🔊 Звуков: {len(snd_files)}\n"
            f"📁 Всего изменений: {total}{icon_note}\n\n"
            f"📥 <b>Как установить:</b>\n{install_text}"
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
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        return
    total = all_users_count()
    paid  = paid_users_count()
    await cq.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"💎 Платных: <b>{paid}</b>\n"
        f"🆓 Бесплатных: <b>{total - paid}</b>",
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
            f"Выбери тип подписки для <code>{uid}</code>:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📅 Неделя",    callback_data="admin_sub_week")],
                [InlineKeyboardButton(text="♾ Навсегда",   callback_data="admin_sub_forever")],
            ])
        )
    except ValueError:
        await message.answer("❌ Введи числовой ID")

@dp.callback_query(AdminStates.give_sub_type, F.data.startswith("admin_sub_"))
async def admin_give_type(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in ADMIN_IDS:
        return
    sub_type  = "week" if cq.data == "admin_sub_week" else "forever"
    data      = await state.get_data()
    target_id = data["target_id"]
    upsert_user(target_id, "")
    give_sub(target_id, sub_type)
    await state.clear()
    label = "неделя" if sub_type == "week" else "навсегда"
    await cq.message.edit_text(
        f"✅ Подписка <b>{label}</b> выдана пользователю <code>{target_id}</code>",
        reply_markup=admin_kb(), parse_mode="HTML"
    )
    try:
        await bot.send_message(
            target_id,
            "🎁 <b>Вам выдана бесплатная подписка!</b>\nТеперь создавай паки без ограничений 🎉",
            parse_mode="HTML", reply_markup=main_menu_kb()
        )
    except Exception:
        pass

# ─── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    init_firebase()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
