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
    # ── Wool (all 16 colors) ─────────────────────────────────────────────────
    "wool_white":    "assets/minecraft/textures/block/white_wool.png",
    "wool_orange":   "assets/minecraft/textures/block/orange_wool.png",
    "wool_magenta":  "assets/minecraft/textures/block/magenta_wool.png",
    "wool_light_blue": "assets/minecraft/textures/block/light_blue_wool.png",
    "wool_yellow":   "assets/minecraft/textures/block/yellow_wool.png",
    "wool_lime":     "assets/minecraft/textures/block/lime_wool.png",
    "wool_pink":     "assets/minecraft/textures/block/pink_wool.png",
    "wool_gray":     "assets/minecraft/textures/block/gray_wool.png",
    "wool_light_gray": "assets/minecraft/textures/block/light_gray_wool.png",
    "wool_cyan":     "assets/minecraft/textures/block/cyan_wool.png",
    "wool_purple":   "assets/minecraft/textures/block/purple_wool.png",
    "wool_blue":     "assets/minecraft/textures/block/blue_wool.png",
    "wool_brown":    "assets/minecraft/textures/block/brown_wool.png",
    "wool_green":    "assets/minecraft/textures/block/green_wool.png",
    "wool_red":      "assets/minecraft/textures/block/red_wool.png",
    "wool_black":    "assets/minecraft/textures/block/black_wool.png",
    # ── Concrete (all 16 colors) ─────────────────────────────────────────────
    "concrete_white":  "assets/minecraft/textures/block/white_concrete.png",
    "concrete_orange": "assets/minecraft/textures/block/orange_concrete.png",
    "concrete_yellow": "assets/minecraft/textures/block/yellow_concrete.png",
    "concrete_lime":   "assets/minecraft/textures/block/lime_concrete.png",
    "concrete_red":    "assets/minecraft/textures/block/red_concrete.png",
    "concrete_blue":   "assets/minecraft/textures/block/blue_concrete.png",
    "concrete_black":  "assets/minecraft/textures/block/black_concrete.png",
    "concrete_green":  "assets/minecraft/textures/block/green_concrete.png",
    "concrete_cyan":   "assets/minecraft/textures/block/cyan_concrete.png",
    "concrete_purple": "assets/minecraft/textures/block/purple_concrete.png",
    "concrete_magenta":"assets/minecraft/textures/block/magenta_concrete.png",
    "concrete_pink":   "assets/minecraft/textures/block/pink_concrete.png",
    "concrete_gray":   "assets/minecraft/textures/block/gray_concrete.png",
    "concrete_brown":  "assets/minecraft/textures/block/brown_concrete.png",
    "concrete_light_blue": "assets/minecraft/textures/block/light_blue_concrete.png",
    "concrete_light_gray": "assets/minecraft/textures/block/light_gray_concrete.png",
    # ── Terracotta (all 16 colors) ───────────────────────────────────────────
    "terracotta":        "assets/minecraft/textures/block/terracotta.png",
    "terracotta_white":  "assets/minecraft/textures/block/white_terracotta.png",
    "terracotta_orange": "assets/minecraft/textures/block/orange_terracotta.png",
    "terracotta_yellow": "assets/minecraft/textures/block/yellow_terracotta.png",
    "terracotta_red":    "assets/minecraft/textures/block/red_terracotta.png",
    "terracotta_blue":   "assets/minecraft/textures/block/blue_terracotta.png",
    "terracotta_black":  "assets/minecraft/textures/block/black_terracotta.png",
    "terracotta_green":  "assets/minecraft/textures/block/green_terracotta.png",
    "terracotta_cyan":   "assets/minecraft/textures/block/cyan_terracotta.png",
    "terracotta_purple": "assets/minecraft/textures/block/purple_terracotta.png",
    "terracotta_brown":  "assets/minecraft/textures/block/brown_terracotta.png",
    # ── Glass ────────────────────────────────────────────────────────────────
    "glass":             "assets/minecraft/textures/block/glass.png",
    "glass_white":       "assets/minecraft/textures/block/white_stained_glass.png",
    "glass_orange":      "assets/minecraft/textures/block/orange_stained_glass.png",
    "glass_red":         "assets/minecraft/textures/block/red_stained_glass.png",
    "glass_blue":        "assets/minecraft/textures/block/blue_stained_glass.png",
    "glass_yellow":      "assets/minecraft/textures/block/yellow_stained_glass.png",
    "glass_green":       "assets/minecraft/textures/block/green_stained_glass.png",
    "glass_cyan":        "assets/minecraft/textures/block/cyan_stained_glass.png",
    "glass_purple":      "assets/minecraft/textures/block/purple_stained_glass.png",
    "glass_black":       "assets/minecraft/textures/block/black_stained_glass.png",
    # ── More wood types ──────────────────────────────────────────────────────
    "jungle_log":        "assets/minecraft/textures/block/jungle_log.png",
    "jungle_planks":     "assets/minecraft/textures/block/jungle_planks.png",
    "jungle_leaves":     "assets/minecraft/textures/block/jungle_leaves.png",
    "acacia_log":        "assets/minecraft/textures/block/acacia_log.png",
    "acacia_planks":     "assets/minecraft/textures/block/acacia_planks.png",
    "acacia_leaves":     "assets/minecraft/textures/block/acacia_leaves.png",
    "dark_oak_log":      "assets/minecraft/textures/block/dark_oak_log.png",
    "dark_oak_planks":   "assets/minecraft/textures/block/dark_oak_planks.png",
    "dark_oak_leaves":   "assets/minecraft/textures/block/dark_oak_leaves.png",
    "mangrove_log":      "assets/minecraft/textures/block/mangrove_log.png",
    "mangrove_planks":   "assets/minecraft/textures/block/mangrove_planks.png",
    "cherry_log":        "assets/minecraft/textures/block/cherry_log.png",
    "cherry_planks":     "assets/minecraft/textures/block/cherry_planks.png",
    "cherry_leaves":     "assets/minecraft/textures/block/cherry_leaves.png",
    # ── Stone variants ───────────────────────────────────────────────────────
    "stone_bricks":      "assets/minecraft/textures/block/stone_bricks.png",
    "cracked_stone_bricks": "assets/minecraft/textures/block/cracked_stone_bricks.png",
    "mossy_stone_bricks":"assets/minecraft/textures/block/mossy_stone_bricks.png",
    "deepslate":         "assets/minecraft/textures/block/deepslate.png",
    "deepslate_bricks":  "assets/minecraft/textures/block/deepslate_bricks.png",
    "deepslate_tiles":   "assets/minecraft/textures/block/deepslate_tiles.png",
    "polished_deepslate":"assets/minecraft/textures/block/polished_deepslate.png",
    "granite":           "assets/minecraft/textures/block/granite.png",
    "diorite":           "assets/minecraft/textures/block/diorite.png",
    "andesite":          "assets/minecraft/textures/block/andesite.png",
    "polished_granite":  "assets/minecraft/textures/block/polished_granite.png",
    "polished_diorite":  "assets/minecraft/textures/block/polished_diorite.png",
    "polished_andesite": "assets/minecraft/textures/block/polished_andesite.png",
    "calcite":           "assets/minecraft/textures/block/calcite.png",
    "tuff":              "assets/minecraft/textures/block/tuff.png",
    "dripstone":         "assets/minecraft/textures/block/dripstone_block.png",
    # ── Special blocks ───────────────────────────────────────────────────────
    "ice":               "assets/minecraft/textures/block/ice.png",
    "packed_ice":        "assets/minecraft/textures/block/packed_ice.png",
    "blue_ice":          "assets/minecraft/textures/block/blue_ice.png",
    "snow":              "assets/minecraft/textures/block/snow.png",
    "powder_snow":       "assets/minecraft/textures/block/powder_snow.png",
    "cactus_top":        "assets/minecraft/textures/block/cactus_top.png",
    "cactus_side":       "assets/minecraft/textures/block/cactus_side.png",
    "pumpkin_top":       "assets/minecraft/textures/block/pumpkin_top.png",
    "pumpkin_side":      "assets/minecraft/textures/block/pumpkin_side.png",
    "pumpkin_face":      "assets/minecraft/textures/block/carved_pumpkin.png",
    "melon_top":         "assets/minecraft/textures/block/melon_top.png",
    "melon_side":        "assets/minecraft/textures/block/melon_side.png",
    "hay_top":           "assets/minecraft/textures/block/hay_block_top.png",
    "hay_side":          "assets/minecraft/textures/block/hay_block_side.png",
    "sponge":            "assets/minecraft/textures/block/sponge.png",
    "wet_sponge":        "assets/minecraft/textures/block/wet_sponge.png",
    "honeycomb":         "assets/minecraft/textures/block/honeycomb_block.png",
    "honey":             "assets/minecraft/textures/block/honey_block_top.png",
    "amethyst":          "assets/minecraft/textures/block/amethyst_block.png",
    "budding_amethyst":  "assets/minecraft/textures/block/budding_amethyst.png",
    "sculk":             "assets/minecraft/textures/block/sculk.png",
    "sculk_catalyst":    "assets/minecraft/textures/block/sculk_catalyst_top.png",
    "mud":               "assets/minecraft/textures/block/mud.png",
    "muddy_mangrove_roots": "assets/minecraft/textures/block/muddy_mangrove_roots_top.png",
    # ── Nether blocks ────────────────────────────────────────────────────────
    "nether_bricks":     "assets/minecraft/textures/block/nether_bricks.png",
    "nether_quartz_ore": "assets/minecraft/textures/block/nether_quartz_ore.png",
    "nether_gold_ore":   "assets/minecraft/textures/block/nether_gold_ore.png",
    "crimson_planks":    "assets/minecraft/textures/block/crimson_planks.png",
    "warped_planks":     "assets/minecraft/textures/block/warped_planks.png",
    "crimson_stem":      "assets/minecraft/textures/block/crimson_stem.png",
    "warped_stem":       "assets/minecraft/textures/block/warped_stem.png",
    "basalt":            "assets/minecraft/textures/block/basalt_top.png",
    "blackstone":        "assets/minecraft/textures/block/blackstone.png",
    "gilded_blackstone": "assets/minecraft/textures/block/gilded_blackstone.png",
    "quartz_block":      "assets/minecraft/textures/block/quartz_block_top.png",
    "smooth_quartz":     "assets/minecraft/textures/block/quartz_block_bottom.png",
    # ── End blocks ───────────────────────────────────────────────────────────
    "end_stone_bricks":  "assets/minecraft/textures/block/end_stone_bricks.png",
    "purpur_pillar":     "assets/minecraft/textures/block/purpur_pillar.png",
    "chorus_plant":      "assets/minecraft/textures/block/chorus_plant.png",
    # ── Other blocks ─────────────────────────────────────────────────────────
    "magma_block":       "assets/minecraft/textures/block/magma.png",
    "mycelium_top":      "assets/minecraft/textures/block/mycelium_top.png",
    "podzol_top":        "assets/minecraft/textures/block/podzol_top.png",
    "clay":              "assets/minecraft/textures/block/clay.png",
    "gravel2":           "assets/minecraft/textures/block/gravel.png",
    "red_sand":          "assets/minecraft/textures/block/red_sand.png",
    "sandstone_top":     "assets/minecraft/textures/block/sandstone_top.png",
    "sandstone_side":    "assets/minecraft/textures/block/sandstone.png",
    "red_sandstone_top": "assets/minecraft/textures/block/red_sandstone_top.png",
    "red_sandstone_side":"assets/minecraft/textures/block/red_sandstone.png",
    "prismarine":        "assets/minecraft/textures/block/prismarine.png",
    "prismarine_bricks": "assets/minecraft/textures/block/prismarine_bricks.png",
    "dark_prismarine":   "assets/minecraft/textures/block/dark_prismarine.png",
    "sea_lantern":       "assets/minecraft/textures/block/sea_lantern.png",
    "copper_block":      "assets/minecraft/textures/block/copper_block.png",
    "exposed_copper":    "assets/minecraft/textures/block/exposed_copper.png",
    "weathered_copper":  "assets/minecraft/textures/block/weathered_copper.png",
    "oxidized_copper":   "assets/minecraft/textures/block/oxidized_copper.png",
    "cut_copper":        "assets/minecraft/textures/block/cut_copper.png",
    "iron_block":        "assets/minecraft/textures/block/iron_block.png",
    "gold_block":        "assets/minecraft/textures/block/gold_block.png",
    "diamond_block":     "assets/minecraft/textures/block/diamond_block.png",
    "emerald_block":     "assets/minecraft/textures/block/emerald_block.png",
    "netherite_block":   "assets/minecraft/textures/block/netherite_block.png",
    "lapis_block":       "assets/minecraft/textures/block/lapis_block.png",
    "coal_block":        "assets/minecraft/textures/block/coal_block.png",
    "redstone_block":    "assets/minecraft/textures/block/redstone_block.png",
    "slime_block":       "assets/minecraft/textures/block/slime_block.png",
    "tnt_bottom":        "assets/minecraft/textures/block/tnt_bottom.png",
    "dispenser_front":   "assets/minecraft/textures/block/dispenser_front.png",
    "dropper_front":     "assets/minecraft/textures/block/dropper_front.png",
    "observer_front":    "assets/minecraft/textures/block/observer_front.png",
    "piston_top":        "assets/minecraft/textures/block/piston_top_normal.png",
    "piston_side":       "assets/minecraft/textures/block/piston_side.png",
    "sticky_piston_top": "assets/minecraft/textures/block/piston_top_sticky.png",
    "note_block":        "assets/minecraft/textures/block/note_block.png",
    "jukebox_top":       "assets/minecraft/textures/block/jukebox_top.png",
    "bookshelf2":        "assets/minecraft/textures/block/bookshelf.png",
    "lectern_top":       "assets/minecraft/textures/block/lectern_top.png",
    "enchanting_table_top": "assets/minecraft/textures/block/enchanting_table_top.png",
    "brewing_stand":     "assets/minecraft/textures/block/brewing_stand.png",
    "beacon":            "assets/minecraft/textures/block/beacon.png",
    "conduit":           "assets/minecraft/textures/block/conduit.png",
    "respawn_anchor_top":"assets/minecraft/textures/block/respawn_anchor_top.png",
    "lodestone_top":     "assets/minecraft/textures/block/lodestone_top.png",
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
    # ── More food ────────────────────────────────────────────────────────────
    "cooked_porkchop": "assets/minecraft/textures/item/cooked_porkchop.png",
    "porkchop":        "assets/minecraft/textures/item/porkchop.png",
    "cooked_mutton":   "assets/minecraft/textures/item/cooked_mutton.png",
    "mutton":          "assets/minecraft/textures/item/mutton.png",
    "cooked_rabbit":   "assets/minecraft/textures/item/cooked_rabbit.png",
    "rabbit":          "assets/minecraft/textures/item/rabbit.png",
    "cooked_salmon":   "assets/minecraft/textures/item/cooked_salmon.png",
    "salmon":          "assets/minecraft/textures/item/salmon.png",
    "cooked_cod":      "assets/minecraft/textures/item/cooked_cod.png",
    "cod":             "assets/minecraft/textures/item/cod.png",
    "cake":            "assets/minecraft/textures/item/cake.png",
    "cookie":          "assets/minecraft/textures/item/cookie.png",
    "melon_slice":     "assets/minecraft/textures/item/melon_slice.png",
    "pumpkin_pie":     "assets/minecraft/textures/item/pumpkin_pie.png",
    "carrot":          "assets/minecraft/textures/item/carrot.png",
    "golden_carrot":   "assets/minecraft/textures/item/golden_carrot.png",
    "potato":          "assets/minecraft/textures/item/potato.png",
    "baked_potato":    "assets/minecraft/textures/item/baked_potato.png",
    "beetroot":        "assets/minecraft/textures/item/beetroot.png",
    "beetroot_soup":   "assets/minecraft/textures/item/beetroot_soup.png",
    "mushroom_stew":   "assets/minecraft/textures/item/mushroom_stew.png",
    "suspicious_stew": "assets/minecraft/textures/item/suspicious_stew.png",
    "honey_bottle":    "assets/minecraft/textures/item/honey_bottle.png",
    # ── Potions ──────────────────────────────────────────────────────────────
    "potion":          "assets/minecraft/textures/item/potion_overlay.png",
    "splash_potion":   "assets/minecraft/textures/item/splash_potion_overlay.png",
    "lingering_potion":"assets/minecraft/textures/item/lingering_potion_overlay.png",
    "glass_bottle":    "assets/minecraft/textures/item/glass_bottle.png",
    # ── More items ───────────────────────────────────────────────────────────
    "compass":         "assets/minecraft/textures/item/compass_16.png",
    "clock":           "assets/minecraft/textures/item/clock_16.png",
    "map":             "assets/minecraft/textures/item/map_background.png",
    "fishing_rod":     "assets/minecraft/textures/item/fishing_rod_uncast.png",
    "bucket":          "assets/minecraft/textures/item/bucket.png",
    "water_bucket":    "assets/minecraft/textures/item/water_bucket.png",
    "lava_bucket":     "assets/minecraft/textures/item/lava_bucket.png",
    "milk_bucket":     "assets/minecraft/textures/item/milk_bucket.png",
    "flint_and_steel": "assets/minecraft/textures/item/flint_and_steel.png",
    "flint":           "assets/minecraft/textures/item/flint.png",
    "bone":            "assets/minecraft/textures/item/bone.png",
    "string":          "assets/minecraft/textures/item/string.png",
    "feather":         "assets/minecraft/textures/item/feather.png",
    "gunpowder":       "assets/minecraft/textures/item/gunpowder.png",
    "stick":           "assets/minecraft/textures/item/stick.png",
    "book":            "assets/minecraft/textures/item/book.png",
    "enchanted_book":  "assets/minecraft/textures/item/enchanted_book.png",
    "writable_book":   "assets/minecraft/textures/item/writable_book.png",
    "leather":         "assets/minecraft/textures/item/leather.png",
    "paper":           "assets/minecraft/textures/item/paper.png",
    "quartz":          "assets/minecraft/textures/item/quartz.png",
    "prismarine_shard":"assets/minecraft/textures/item/prismarine_shard.png",
    "prismarine_crystal":"assets/minecraft/textures/item/prismarine_crystals.png",
    "name_tag":        "assets/minecraft/textures/item/name_tag.png",
    "lead":            "assets/minecraft/textures/item/lead.png",
    "saddle":          "assets/minecraft/textures/item/saddle.png",
    "rabbit_foot":     "assets/minecraft/textures/item/rabbit_foot.png",
    "rabbit_hide":     "assets/minecraft/textures/item/rabbit_hide.png",
    "ink_sac":         "assets/minecraft/textures/item/ink_sac.png",
    "glow_ink_sac":    "assets/minecraft/textures/item/glow_ink_sac.png",
    "slimeball":       "assets/minecraft/textures/item/slime_ball.png",
    "magma_cream":     "assets/minecraft/textures/item/magma_cream.png",
    "blaze_powder":    "assets/minecraft/textures/item/blaze_powder.png",
    "nether_brick":    "assets/minecraft/textures/item/nether_brick.png",
    "netherite_scrap": "assets/minecraft/textures/item/netherite_scrap.png",
    "disc_11":         "assets/minecraft/textures/item/music_disc_11.png",
    "disc_pigstep":    "assets/minecraft/textures/item/music_disc_pigstep.png",
    "disc_otherside":  "assets/minecraft/textures/item/music_disc_otherside.png",
    "disc_cat":        "assets/minecraft/textures/item/music_disc_cat.png",
    "disc_blocks":     "assets/minecraft/textures/item/music_disc_blocks.png",
    "disc_chirp":      "assets/minecraft/textures/item/music_disc_chirp.png",
    "amethyst_shard":  "assets/minecraft/textures/item/amethyst_shard.png",
    "echo_shard":      "assets/minecraft/textures/item/echo_shard.png",
    "recovery_compass":"assets/minecraft/textures/item/recovery_compass_16.png",
    "goat_horn":       "assets/minecraft/textures/item/goat_horn.png",
    "torchflower_seeds":"assets/minecraft/textures/item/torchflower_seeds.png",
    "brush":           "assets/minecraft/textures/item/brush.png",
    "pottery_sherd":   "assets/minecraft/textures/item/archer_pottery_sherd.png",
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
    # ── More mobs ────────────────────────────────────────────────────────────
    "pillager":        "assets/minecraft/textures/entity/illager/pillager.png",
    "vindicator":      "assets/minecraft/textures/entity/illager/vindicator.png",
    "evoker":          "assets/minecraft/textures/entity/illager/evoker.png",
    "witch":           "assets/minecraft/textures/entity/witch/witch.png",
    "ravager":         "assets/minecraft/textures/entity/ravager/ravager.png",
    "bee":             "assets/minecraft/textures/entity/bee/bee.png",
    "panda":           "assets/minecraft/textures/entity/panda/panda.png",
    "fox":             "assets/minecraft/textures/entity/fox/fox.png",
    "dolphin":         "assets/minecraft/textures/entity/dolphin.png",
    "turtle":          "assets/minecraft/textures/entity/turtle/big_sea_turtle.png",
    "parrot_red":      "assets/minecraft/textures/entity/parrot/parrot_red_blue.png",
    "polar_bear":      "assets/minecraft/textures/entity/bear/polarbear.png",
    "llama":           "assets/minecraft/textures/entity/llama/llama_creamy.png",
    "donkey":          "assets/minecraft/textures/entity/horse/donkey.png",
    "mule":            "assets/minecraft/textures/entity/horse/mule.png",
    "ocelot":          "assets/minecraft/textures/entity/cat/ocelot.png",
    "rabbit_entity":   "assets/minecraft/textures/entity/rabbit/brown.png",
    "mushroom_cow":    "assets/minecraft/textures/entity/cow/mooshroom.png",
    "squid":           "assets/minecraft/textures/entity/squid/squid.png",
    "glow_squid":      "assets/minecraft/textures/entity/squid/glow_squid.png",
    "bat":             "assets/minecraft/textures/entity/bat.png",
    "silverfish":      "assets/minecraft/textures/entity/silverfish.png",
    "cave_spider":     "assets/minecraft/textures/entity/spider/cave_spider.png",
    "slime_entity":    "assets/minecraft/textures/entity/slime/slime.png",
    "magma_cube":      "assets/minecraft/textures/entity/slime/magmacube.png",
    "zombie_piglin":   "assets/minecraft/textures/entity/piglin/zombified_piglin.png",
    "piglin":          "assets/minecraft/textures/entity/piglin/piglin.png",
    "hoglin":          "assets/minecraft/textures/entity/hoglin/hoglin.png",
    "zoglin":          "assets/minecraft/textures/entity/zoglin/zoglin.png",
    "strider":         "assets/minecraft/textures/entity/strider/strider.png",
    "warden":          "assets/minecraft/textures/entity/warden/warden.png",
    "allay":           "assets/minecraft/textures/entity/allay/allay.png",
    "frog":            "assets/minecraft/textures/entity/frog/temperate_frog.png",
    "axolotl":         "assets/minecraft/textures/entity/axolotl/axolotl_lucy.png",
    "goat":            "assets/minecraft/textures/entity/goat/goat.png",
    "camel":           "assets/minecraft/textures/entity/camel/camel.png",
    "sniffer":         "assets/minecraft/textures/entity/sniffer/sniffer.png",
    # ── GUI / HUD ─────────────────────────────────────────────────────────────
    "hotbar":          "assets/minecraft/textures/gui/widgets.png",
    "icons":           "assets/minecraft/textures/gui/icons.png",
    "crosshair":       "assets/minecraft/textures/gui/icons.png",
    "container_generic": "assets/minecraft/textures/gui/container/generic_54.png",
    "container_inventory": "assets/minecraft/textures/gui/container/inventory.png",
    "container_crafting": "assets/minecraft/textures/gui/container/crafting_table.png",
    "container_furnace": "assets/minecraft/textures/gui/container/furnace.png",
    "container_chest": "assets/minecraft/textures/gui/container/shulker_box.png",
    "title_screen":    "assets/minecraft/textures/gui/title/minecraft.png",
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
    # ── Wool ────────────────────────────────────────────────────────────────
    "wool_white":    "textures/blocks/wool_colored_white.png",
    "wool_orange":   "textures/blocks/wool_colored_orange.png",
    "wool_magenta":  "textures/blocks/wool_colored_magenta.png",
    "wool_light_blue": "textures/blocks/wool_colored_light_blue.png",
    "wool_yellow":   "textures/blocks/wool_colored_yellow.png",
    "wool_lime":     "textures/blocks/wool_colored_lime.png",
    "wool_pink":     "textures/blocks/wool_colored_pink.png",
    "wool_gray":     "textures/blocks/wool_colored_gray.png",
    "wool_light_gray": "textures/blocks/wool_colored_silver.png",
    "wool_cyan":     "textures/blocks/wool_colored_cyan.png",
    "wool_purple":   "textures/blocks/wool_colored_purple.png",
    "wool_blue":     "textures/blocks/wool_colored_blue.png",
    "wool_brown":    "textures/blocks/wool_colored_brown.png",
    "wool_green":    "textures/blocks/wool_colored_green.png",
    "wool_red":      "textures/blocks/wool_colored_red.png",
    "wool_black":    "textures/blocks/wool_colored_black.png",
    # ── Concrete ────────────────────────────────────────────────────────────
    "concrete_white":  "textures/blocks/concrete_white.png",
    "concrete_orange": "textures/blocks/concrete_orange.png",
    "concrete_yellow": "textures/blocks/concrete_yellow.png",
    "concrete_lime":   "textures/blocks/concrete_lime.png",
    "concrete_red":    "textures/blocks/concrete_red.png",
    "concrete_blue":   "textures/blocks/concrete_blue.png",
    "concrete_black":  "textures/blocks/concrete_black.png",
    "concrete_green":  "textures/blocks/concrete_green.png",
    "concrete_cyan":   "textures/blocks/concrete_cyan.png",
    "concrete_purple": "textures/blocks/concrete_purple.png",
    "concrete_magenta":"textures/blocks/concrete_magenta.png",
    "concrete_pink":   "textures/blocks/concrete_pink.png",
    "concrete_gray":   "textures/blocks/concrete_gray.png",
    "concrete_brown":  "textures/blocks/concrete_brown.png",
    "concrete_light_blue": "textures/blocks/concrete_light_blue.png",
    "concrete_light_gray": "textures/blocks/concrete_silver.png",
    # ── Terracotta ──────────────────────────────────────────────────────────
    "terracotta":        "textures/blocks/hardened_clay.png",
    "terracotta_white":  "textures/blocks/hardened_clay_stained_white.png",
    "terracotta_orange": "textures/blocks/hardened_clay_stained_orange.png",
    "terracotta_yellow": "textures/blocks/hardened_clay_stained_yellow.png",
    "terracotta_red":    "textures/blocks/hardened_clay_stained_red.png",
    "terracotta_blue":   "textures/blocks/hardened_clay_stained_blue.png",
    "terracotta_black":  "textures/blocks/hardened_clay_stained_black.png",
    "terracotta_green":  "textures/blocks/hardened_clay_stained_green.png",
    "terracotta_cyan":   "textures/blocks/hardened_clay_stained_cyan.png",
    "terracotta_purple": "textures/blocks/hardened_clay_stained_purple.png",
    "terracotta_brown":  "textures/blocks/hardened_clay_stained_brown.png",
    # ── Glass ───────────────────────────────────────────────────────────────
    "glass":             "textures/blocks/glass.png",
    "glass_white":       "textures/blocks/glass_white.png",
    "glass_orange":      "textures/blocks/glass_orange.png",
    "glass_red":         "textures/blocks/glass_red.png",
    "glass_blue":        "textures/blocks/glass_blue.png",
    "glass_yellow":      "textures/blocks/glass_yellow.png",
    "glass_green":       "textures/blocks/glass_green.png",
    "glass_cyan":        "textures/blocks/glass_cyan.png",
    "glass_purple":      "textures/blocks/glass_purple.png",
    "glass_black":       "textures/blocks/glass_black.png",
    # ── More wood ───────────────────────────────────────────────────────────
    "jungle_log":        "textures/blocks/log_jungle.png",
    "jungle_planks":     "textures/blocks/planks_jungle.png",
    "jungle_leaves":     "textures/blocks/leaves_jungle.png",
    "acacia_log":        "textures/blocks/log_acacia.png",
    "acacia_planks":     "textures/blocks/planks_acacia.png",
    "acacia_leaves":     "textures/blocks/leaves_acacia.png",
    "dark_oak_log":      "textures/blocks/log_big_oak.png",
    "dark_oak_planks":   "textures/blocks/planks_big_oak.png",
    "dark_oak_leaves":   "textures/blocks/leaves_big_oak.png",
    "mangrove_log":      "textures/blocks/mangrove_log.png",
    "mangrove_planks":   "textures/blocks/mangrove_planks.png",
    "cherry_log":        "textures/blocks/cherry_log.png",
    "cherry_planks":     "textures/blocks/cherry_planks.png",
    "cherry_leaves":     "textures/blocks/cherry_leaves.png",
    # ── Stone variants ──────────────────────────────────────────────────────
    "stone_bricks":      "textures/blocks/stonebrick.png",
    "cracked_stone_bricks": "textures/blocks/stonebrick_cracked.png",
    "mossy_stone_bricks":"textures/blocks/stonebrick_mossy.png",
    "deepslate":         "textures/blocks/deepslate_top.png",
    "deepslate_bricks":  "textures/blocks/deepslate_bricks.png",
    "deepslate_tiles":   "textures/blocks/deepslate_tiles.png",
    "polished_deepslate":"textures/blocks/polished_deepslate.png",
    "granite":           "textures/blocks/stone_granite.png",
    "diorite":           "textures/blocks/stone_diorite.png",
    "andesite":          "textures/blocks/stone_andesite.png",
    "polished_granite":  "textures/blocks/stone_granite_smooth.png",
    "polished_diorite":  "textures/blocks/stone_diorite_smooth.png",
    "polished_andesite": "textures/blocks/stone_andesite_smooth.png",
    "calcite":           "textures/blocks/calcite.png",
    "tuff":              "textures/blocks/tuff.png",
    "dripstone":         "textures/blocks/dripstone_block.png",
    # ── Special blocks ──────────────────────────────────────────────────────
    "ice":               "textures/blocks/ice.png",
    "packed_ice":        "textures/blocks/ice_packed.png",
    "blue_ice":          "textures/blocks/blue_ice.png",
    "snow":              "textures/blocks/snow.png",
    "powder_snow":       "textures/blocks/powder_snow.png",
    "cactus_top":        "textures/blocks/cactus_top.png",
    "cactus_side":       "textures/blocks/cactus_side.png",
    "pumpkin_top":       "textures/blocks/pumpkin_top.png",
    "pumpkin_side":      "textures/blocks/pumpkin_side.png",
    "pumpkin_face":      "textures/blocks/pumpkin_face_off.png",
    "melon_top":         "textures/blocks/melon_top.png",
    "melon_side":        "textures/blocks/melon_side.png",
    "hay_top":           "textures/blocks/hay_block_top.png",
    "hay_side":          "textures/blocks/hay_block_side.png",
    "sponge":            "textures/blocks/sponge.png",
    "wet_sponge":        "textures/blocks/sponge_wet.png",
    "honeycomb":         "textures/blocks/honeycomb_block.png",
    "honey":             "textures/blocks/honey_block_top.png",
    "amethyst":          "textures/blocks/amethyst_block.png",
    "budding_amethyst":  "textures/blocks/budding_amethyst.png",
    "sculk":             "textures/blocks/sculk.png",
    "sculk_catalyst":    "textures/blocks/sculk_catalyst_top.png",
    "mud":               "textures/blocks/mud.png",
    "muddy_mangrove_roots": "textures/blocks/muddy_mangrove_roots_top.png",
    # ── Nether ──────────────────────────────────────────────────────────────
    "nether_bricks":     "textures/blocks/nether_brick.png",
    "nether_quartz_ore": "textures/blocks/quartz_ore.png",
    "nether_gold_ore":   "textures/blocks/nether_gold_ore.png",
    "crimson_planks":    "textures/blocks/crimson_planks.png",
    "warped_planks":     "textures/blocks/warped_planks.png",
    "crimson_stem":      "textures/blocks/crimson_stem_top.png",
    "warped_stem":       "textures/blocks/warped_stem_top.png",
    "basalt":            "textures/blocks/basalt_top.png",
    "blackstone":        "textures/blocks/blackstone.png",
    "gilded_blackstone": "textures/blocks/gilded_blackstone.png",
    "quartz_block":      "textures/blocks/quartz_block_top.png",
    "smooth_quartz":     "textures/blocks/quartz_block_bottom.png",
    # ── End ─────────────────────────────────────────────────────────────────
    "end_stone_bricks":  "textures/blocks/end_bricks.png",
    "purpur_pillar":     "textures/blocks/purpur_pillar.png",
    "chorus_plant":      "textures/blocks/chorus_plant.png",
    # ── More ────────────────────────────────────────────────────────────────
    "magma_block":       "textures/blocks/magma.png",
    "mycelium_top":      "textures/blocks/mycelium_top.png",
    "podzol_top":        "textures/blocks/dirt_podzol_top.png",
    "clay":              "textures/blocks/clay.png",
    "red_sand":          "textures/blocks/red_sand.png",
    "sandstone_top":     "textures/blocks/sandstone_top.png",
    "sandstone_side":    "textures/blocks/sandstone_normal.png",
    "red_sandstone_top": "textures/blocks/red_sandstone_top.png",
    "red_sandstone_side":"textures/blocks/red_sandstone_normal.png",
    "prismarine":        "textures/blocks/prismarine_rough.png",
    "prismarine_bricks": "textures/blocks/prismarine_bricks.png",
    "dark_prismarine":   "textures/blocks/prismarine_dark.png",
    "sea_lantern":       "textures/blocks/sea_lantern.png",
    "copper_block":      "textures/blocks/copper_block.png",
    "exposed_copper":    "textures/blocks/exposed_copper.png",
    "weathered_copper":  "textures/blocks/weathered_copper.png",
    "oxidized_copper":   "textures/blocks/oxidized_copper.png",
    "cut_copper":        "textures/blocks/cut_copper.png",
    "iron_block":        "textures/blocks/iron_block.png",
    "gold_block":        "textures/blocks/gold_block.png",
    "diamond_block":     "textures/blocks/diamond_block.png",
    "emerald_block":     "textures/blocks/emerald_block.png",
    "netherite_block":   "textures/blocks/ancient_debris_top.png",
    "lapis_block":       "textures/blocks/lapis_block.png",
    "coal_block":        "textures/blocks/coal_block.png",
    "redstone_block":    "textures/blocks/redstone_block.png",
    "slime_block":       "textures/blocks/slime.png",
    "tnt_bottom":        "textures/blocks/tnt_bottom.png",
    "dispenser_front":   "textures/blocks/dispenser_front_horizontal.png",
    "dropper_front":     "textures/blocks/dropper_front_horizontal.png",
    "observer_front":    "textures/blocks/observer_front.png",
    "piston_top":        "textures/blocks/piston_top_normal.png",
    "piston_side":       "textures/blocks/piston_side.png",
    "sticky_piston_top": "textures/blocks/piston_top_sticky.png",
    "note_block":        "textures/blocks/noteblock.png",
    "jukebox_top":       "textures/blocks/jukebox_top.png",
    "enchanting_table_top": "textures/blocks/enchanting_table_top.png",
    "beacon":            "textures/blocks/beacon.png",
    "respawn_anchor_top":"textures/blocks/respawn_anchor_top.png",
    "iron_block2":       "textures/blocks/iron_block.png",
    # ── Items ────────────────────────────────────────────────────────────────
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
# Внутреннее имя → (java_path, bedrock_path, java_event, bedrock_event)
SOUNDS = {
    "hurt":            ("assets/minecraft/sounds/damage/hit1.ogg",              "sounds/damage/hit1.ogg",              "entity.player.hurt",             "mob.player.hurt"),
    "death":           ("assets/minecraft/sounds/damage/hit3.ogg",              "sounds/damage/hit3.ogg",              "entity.player.death",            "mob.player.death"),
    "explosion":       ("assets/minecraft/sounds/random/explode1.ogg",          "sounds/random/explode1.ogg",          "entity.generic.explode",         "random.explode"),
    "eat":             ("assets/minecraft/sounds/random/eat1.ogg",              "sounds/random/eat1.ogg",              "entity.player.burp",             "mob.player.burp"),
    "levelup":         ("assets/minecraft/sounds/random/levelup.ogg",           "sounds/random/levelup.ogg",           "entity.player.levelup",          "random.levelup"),
    "click":           ("assets/minecraft/sounds/random/click.ogg",             "sounds/random/click.ogg",             "ui.button.click",                "random.click"),
    "swim":            ("assets/minecraft/sounds/liquid/swim1.ogg",             "sounds/liquid/swim1.ogg",             "entity.player.swim",             "mob.player.swim"),
    "anvil":           ("assets/minecraft/sounds/random/anvil_use.ogg",         "sounds/random/anvil_use.ogg",         "block.anvil.use",                "random.anvil_use"),
    "chest_open":      ("assets/minecraft/sounds/random/chestopen.ogg",         "sounds/random/chestopen.ogg",         "block.chest.open",               "random.chestopen"),
    "chest_close":     ("assets/minecraft/sounds/random/chestclosed.ogg",       "sounds/random/chestclosed.ogg",       "block.chest.close",              "random.chestclosed"),
    "bow_shoot":       ("assets/minecraft/sounds/random/bow.ogg",               "sounds/random/bow.ogg",               "entity.arrow.shoot",             "random.bow"),
    "sword_hit":       ("assets/minecraft/sounds/random/classic_hurt.ogg",      "sounds/random/classic_hurt.ogg",      "entity.player.attack.strong",    "mob.player.attack.strong"),
    "portal":          ("assets/minecraft/sounds/portal/portal.ogg",            "sounds/portal/portal.ogg",            "block.portal.ambient",           "portal.portal"),
    "enderman_scream": ("assets/minecraft/sounds/mob/endermen/scream1.ogg",     "sounds/mob/endermen/scream1.ogg",     "entity.enderman.scream",         "mob.endermen.scream"),
    "creeper_hiss":    ("assets/minecraft/sounds/mob/creeper/say1.ogg",         "sounds/mob/creeper/say1.ogg",         "entity.creeper.primed",          "mob.creeper.say"),
    "villager":        ("assets/minecraft/sounds/mob/villager/idle1.ogg",       "sounds/mob/villager/idle1.ogg",       "entity.villager.ambient",        "mob.villager.idle"),
    "thunder":         ("assets/minecraft/sounds/ambient/weather/thunder1.ogg", "sounds/ambient/weather/thunder1.ogg", "entity.lightning_bolt.thunder",  "ambient.weather.thunder"),
    "rain":            ("assets/minecraft/sounds/ambient/weather/rain.ogg",     "sounds/ambient/weather/rain.ogg",     "weather.rain",                   "ambient.weather.rain"),
    "fire":            ("assets/minecraft/sounds/fire/fire.ogg",                "sounds/fire/fire.ogg",                "block.fire.ambient",             "fire.fire"),
    "splash":          ("assets/minecraft/sounds/liquid/splash.ogg",            "sounds/liquid/splash.ogg",            "entity.splash_potion.throw",     "random.splash"),
    # ── Mobs ──────────────────────────────────────────────────────────────────
    "zombie_hurt":     ("assets/minecraft/sounds/mob/zombie/hurt1.ogg",         "sounds/mob/zombie/hurt1.ogg",         "entity.zombie.hurt",             "mob.zombie.hurt"),
    "zombie_death":    ("assets/minecraft/sounds/mob/zombie/death.ogg",         "sounds/mob/zombie/death.ogg",         "entity.zombie.death",            "mob.zombie.death"),
    "zombie_ambient":  ("assets/minecraft/sounds/mob/zombie/say1.ogg",          "sounds/mob/zombie/say1.ogg",          "entity.zombie.ambient",          "mob.zombie.say"),
    "skeleton_hurt":   ("assets/minecraft/sounds/mob/skeleton/hurt.ogg",        "sounds/mob/skeleton/hurt.ogg",        "entity.skeleton.hurt",           "mob.skeleton.hurt"),
    "skeleton_death":  ("assets/minecraft/sounds/mob/skeleton/death.ogg",       "sounds/mob/skeleton/death.ogg",       "entity.skeleton.death",          "mob.skeleton.death"),
    "skeleton_ambient":("assets/minecraft/sounds/mob/skeleton/say1.ogg",        "sounds/mob/skeleton/say1.ogg",        "entity.skeleton.ambient",        "mob.skeleton.say"),
    "creeper_explode": ("assets/minecraft/sounds/mob/creeper/death.ogg",        "sounds/mob/creeper/death.ogg",        "entity.creeper.death",           "mob.creeper.death"),
    "enderman_ambient":("assets/minecraft/sounds/mob/endermen/idle1.ogg",       "sounds/mob/endermen/idle1.ogg",       "entity.enderman.ambient",        "mob.endermen.idle"),
    "enderman_stare":  ("assets/minecraft/sounds/mob/endermen/stare.ogg",       "sounds/mob/endermen/stare.ogg",       "entity.enderman.stare",          "mob.endermen.stare"),
    "ghast_ambient":   ("assets/minecraft/sounds/mob/ghast/moan1.ogg",          "sounds/mob/ghast/moan1.ogg",          "entity.ghast.ambient",           "mob.ghast.moan"),
    "ghast_shoot":     ("assets/minecraft/sounds/mob/ghast/fireball1.ogg",      "sounds/mob/ghast/fireball1.ogg",      "entity.ghast.shoot",             "mob.ghast.fireball"),
    "ghast_scream":    ("assets/minecraft/sounds/mob/ghast/scream1.ogg",        "sounds/mob/ghast/scream1.ogg",        "entity.ghast.warn",              "mob.ghast.scream"),
    "pig_ambient":     ("assets/minecraft/sounds/mob/pig/say1.ogg",             "sounds/mob/pig/say1.ogg",             "entity.pig.ambient",             "mob.pig.say"),
    "pig_death":       ("assets/minecraft/sounds/mob/pig/death.ogg",            "sounds/mob/pig/death.ogg",            "entity.pig.death",               "mob.pig.death"),
    "cow_ambient":     ("assets/minecraft/sounds/mob/cow/say1.ogg",             "sounds/mob/cow/say1.ogg",             "entity.cow.ambient",             "mob.cow.say"),
    "cow_hurt":        ("assets/minecraft/sounds/mob/cow/hurt1.ogg",            "sounds/mob/cow/hurt1.ogg",            "entity.cow.hurt",                "mob.cow.hurt"),
    "wolf_ambient":    ("assets/minecraft/sounds/mob/wolf/bark1.ogg",           "sounds/mob/wolf/bark1.ogg",           "entity.wolf.ambient",            "mob.wolf.bark"),
    "wolf_hurt":       ("assets/minecraft/sounds/mob/wolf/hurt1.ogg",           "sounds/mob/wolf/hurt1.ogg",           "entity.wolf.hurt",               "mob.wolf.hurt"),
    "wolf_howl":       ("assets/minecraft/sounds/mob/wolf/howl1.ogg",           "sounds/mob/wolf/howl1.ogg",           "entity.wolf.howl",               "mob.wolf.howl"),
    "cat_ambient":     ("assets/minecraft/sounds/mob/cat/meow1.ogg",            "sounds/mob/cat/meow1.ogg",            "entity.cat.ambient",             "mob.cat.meow"),
    "spider_ambient":  ("assets/minecraft/sounds/mob/spider/say1.ogg",          "sounds/mob/spider/say1.ogg",          "entity.spider.ambient",          "mob.spider.say"),
    "spider_hurt":     ("assets/minecraft/sounds/mob/spider/hurt1.ogg",         "sounds/mob/spider/hurt1.ogg",         "entity.spider.hurt",             "mob.spider.hurt"),
    "blaze_ambient":   ("assets/minecraft/sounds/mob/blaze/breathe1.ogg",       "sounds/mob/blaze/breathe1.ogg",       "entity.blaze.ambient",           "mob.blaze.breathe"),
    "blaze_shoot":     ("assets/minecraft/sounds/mob/blaze/shoot.ogg",          "sounds/mob/blaze/shoot.ogg",          "entity.blaze.shoot",             "mob.blaze.shoot"),
    "slime_jump":      ("assets/minecraft/sounds/mob/slime/big1.ogg",           "sounds/mob/slime/big1.ogg",           "entity.slime.jump",              "mob.slime.big"),
    "wither_spawn":    ("assets/minecraft/sounds/mob/wither/spawn.ogg",         "sounds/mob/wither/spawn.ogg",         "entity.wither.spawn",            "mob.wither.spawn"),
    "wither_shoot":    ("assets/minecraft/sounds/mob/wither/shoot.ogg",         "sounds/mob/wither/shoot.ogg",         "entity.wither.shoot",            "mob.wither.shoot"),
    "ender_dragon_ambient": ("assets/minecraft/sounds/mob/enderdragon/growl.ogg","sounds/mob/enderdragon/growl.ogg",  "entity.ender_dragon.ambient",    "mob.enderdragon.growl"),
    "ender_dragon_roar":    ("assets/minecraft/sounds/mob/enderdragon/roar.ogg","sounds/mob/enderdragon/roar.ogg",    "entity.ender_dragon.death",      "mob.enderdragon.roar"),
    "horse_ambient":   ("assets/minecraft/sounds/mob/horse/idle.ogg",           "sounds/mob/horse/idle.ogg",           "entity.horse.ambient",           "mob.horse.idle"),
    "horse_gallop":    ("assets/minecraft/sounds/mob/horse/gallop.ogg",         "sounds/mob/horse/gallop.ogg",         "entity.horse.gallop",            "mob.horse.gallop"),
    "villager_trade":  ("assets/minecraft/sounds/mob/villager/yes1.ogg",        "sounds/mob/villager/yes1.ogg",        "entity.villager.yes",            "mob.villager.yes"),
    "villager_no":     ("assets/minecraft/sounds/mob/villager/no1.ogg",         "sounds/mob/villager/no1.ogg",         "entity.villager.no",             "mob.villager.no"),
    "iron_golem_ambient": ("assets/minecraft/sounds/mob/irongolem/walk1.ogg",   "sounds/mob/irongolem/walk1.ogg",      "entity.iron_golem.step",         "mob.irongolem.walk"),
    "iron_golem_hurt": ("assets/minecraft/sounds/mob/irongolem/hit1.ogg",       "sounds/mob/irongolem/hit1.ogg",       "entity.iron_golem.hurt",         "mob.irongolem.hit"),
    "bat_ambient":     ("assets/minecraft/sounds/mob/bat/idle1.ogg",            "sounds/mob/bat/idle1.ogg",            "entity.bat.ambient",             "mob.bat.idle"),
    "chicken_ambient": ("assets/minecraft/sounds/mob/chicken/say1.ogg",         "sounds/mob/chicken/say1.ogg",         "entity.chicken.ambient",         "mob.chicken.say"),
    "sheep_ambient":   ("assets/minecraft/sounds/mob/sheep/say1.ogg",           "sounds/mob/sheep/say1.ogg",           "entity.sheep.ambient",           "mob.sheep.say"),
    "bee_ambient":     ("assets/minecraft/sounds/mob/bee/loop.ogg",             "sounds/mob/bee/loop.ogg",             "entity.bee.loop",                "mob.bee.loop"),
    "bee_sting":       ("assets/minecraft/sounds/mob/bee/sting.ogg",            "sounds/mob/bee/sting.ogg",            "entity.bee.sting",               "mob.bee.sting"),
    "hoglin_ambient":  ("assets/minecraft/sounds/mob/hoglin/ambient.ogg",       "sounds/mob/hoglin/ambient.ogg",       "entity.hoglin.ambient",          "mob.hoglin.ambient"),
    "piglin_ambient":  ("assets/minecraft/sounds/mob/piglin/ambient1.ogg",      "sounds/mob/piglin/ambient1.ogg",      "entity.piglin.ambient",          "mob.piglin.ambient"),
    "strider_ambient": ("assets/minecraft/sounds/mob/strider/idle.ogg",         "sounds/mob/strider/idle.ogg",         "entity.strider.ambient",         "mob.strider.idle"),
    "axolotl_idle":    ("assets/minecraft/sounds/mob/axolotl/idle.ogg",         "sounds/mob/axolotl/idle.ogg",         "entity.axolotl.idle",            "mob.axolotl.idle"),
    "warden_ambient":  ("assets/minecraft/sounds/mob/warden/idle.ogg",          "sounds/mob/warden/idle.ogg",          "entity.warden.ambient",          "mob.warden.idle"),
    "warden_roar":     ("assets/minecraft/sounds/mob/warden/roar.ogg",          "sounds/mob/warden/roar.ogg",          "entity.warden.roar",             "mob.warden.roar"),
    "sniffer_ambient": ("assets/minecraft/sounds/mob/sniffer/idle.ogg",         "sounds/mob/sniffer/idle.ogg",         "entity.sniffer.idle",            "mob.sniffer.idle"),
    "camel_ambient":   ("assets/minecraft/sounds/mob/camel/idle.ogg",           "sounds/mob/camel/idle.ogg",           "entity.camel.ambient",           "mob.camel.idle"),
    # ── Blocks ────────────────────────────────────────────────────────────────
    "block_grass":     ("assets/minecraft/sounds/step/grass1.ogg",              "sounds/step/grass1.ogg",              "block.grass.step",               "step.grass"),
    "block_stone":     ("assets/minecraft/sounds/step/stone1.ogg",              "sounds/step/stone1.ogg",              "block.stone.step",               "step.stone"),
    "block_wood":      ("assets/minecraft/sounds/step/wood1.ogg",               "sounds/step/wood1.ogg",              "block.wood.step",                "step.wood"),
    "block_sand":      ("assets/minecraft/sounds/step/sand1.ogg",               "sounds/step/sand1.ogg",              "block.sand.step",                "step.sand"),
    "block_gravel":    ("assets/minecraft/sounds/step/gravel1.ogg",             "sounds/step/gravel1.ogg",            "block.gravel.step",              "step.gravel"),
    "block_glass_break": ("assets/minecraft/sounds/random/glass.ogg",           "sounds/random/glass.ogg",            "block.glass.break",              "random.glass"),
    "furnace_fire":    ("assets/minecraft/sounds/fire/ignite.ogg",              "sounds/fire/ignite.ogg",             "item.flintandsteel.use",         "fire.ignite"),
    "door_open":       ("assets/minecraft/sounds/random/door_open.ogg",         "sounds/random/door_open.ogg",        "block.wooden_door.open",         "random.door_open"),
    "door_close":      ("assets/minecraft/sounds/random/door_close.ogg",        "sounds/random/door_close.ogg",       "block.wooden_door.close",        "random.door_close"),
    "iron_door_open":  ("assets/minecraft/sounds/random/door_iron_open.ogg",    "sounds/random/door_iron_open.ogg",   "block.iron_door.open",           "random.door_open"),
    "tnt_fuse":        ("assets/minecraft/sounds/game/tnt/fuse.ogg",            "sounds/game/tnt/fuse.ogg",           "entity.tnt.primed",              "game.tnt.primed"),
    "ore_break":       ("assets/minecraft/sounds/dig/stone4.ogg",               "sounds/dig/stone4.ogg",              "block.stone.break",              "dig.stone"),
    "water_ambient":   ("assets/minecraft/sounds/ambient/underwater/enter.ogg", "sounds/ambient/underwater/enter.ogg","ambient.underwater.enter",       "ambient.underwater.enter"),
    "nether_ambient":  ("assets/minecraft/sounds/ambient/nether/wastes/loop.ogg","sounds/ambient/nether/wastes/loop.ogg","ambient.nether_wastes.loop",  "ambient.nether.loop"),
    "end_ambient":     ("assets/minecraft/sounds/ambient/end/end.ogg",          "sounds/ambient/end/end.ogg",         "ambient.end.loop",               "ambient.end"),
    "cave_ambient":    ("assets/minecraft/sounds/ambient/cave/cave1.ogg",       "sounds/ambient/cave/cave1.ogg",      "ambient.cave",                   "ambient.cave"),
    "music_game":      ("assets/minecraft/sounds/music/game/creative/biome_fest.ogg","sounds/music/game/creative/biome_fest.ogg","music.game",         "music.game"),
    "music_menu":      ("assets/minecraft/sounds/music/menu/menu1.ogg",         "sounds/music/menu/menu1.ogg",        "music.menu",                     "music.menu"),
    "music_creative":  ("assets/minecraft/sounds/music/game/creative/biome_fest.ogg","sounds/music/game/creative/biome_fest.ogg","music.creative",    "music.game.creative"),
    "xp_pickup":       ("assets/minecraft/sounds/random/orb.ogg",               "sounds/random/orb.ogg",              "entity.experience_orb.pickup",   "random.orb"),
    "item_pickup":     ("assets/minecraft/sounds/random/pop.ogg",               "sounds/random/pop.ogg",              "entity.item.pickup",             "random.pop"),
    "drink":           ("assets/minecraft/sounds/random/drink.ogg",             "sounds/random/drink.ogg",            "entity.player.drink",            "mob.player.drink"),
    "fall_big":        ("assets/minecraft/sounds/damage/fallbig.ogg",           "sounds/damage/fallbig.ogg",          "entity.player.big_fall",         "damage.fallbig"),
    "fall_small":      ("assets/minecraft/sounds/damage/fallsmall.ogg",         "sounds/damage/fallsmall.ogg",        "entity.player.small_fall",       "damage.fallsmall"),
    "teleport":        ("assets/minecraft/sounds/mob/endermen/portal.ogg",      "sounds/mob/endermen/portal.ogg",     "entity.enderman.teleport",       "mob.endermen.portal"),
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
    "container_generic":   "📦 Инвентарь (9×6)",
    "container_inventory": "🎒 Инвентарь игрока",
    "container_crafting":  "🔨 Стол крафта",
    "container_furnace":   "🔥 Печь (UI)",
    "container_chest":     "📦 Шалкеровый ящик",
    "title_screen":        "🎮 Экран заголовка",
    # Wool
    "wool_white":      "⬜ Белая шерсть",
    "wool_orange":     "🟠 Оранжевая шерсть",
    "wool_magenta":    "💜 Пурпурная шерсть",
    "wool_light_blue": "🩵 Голубая шерсть",
    "wool_yellow":     "🟡 Жёлтая шерсть",
    "wool_lime":       "💚 Лаймовая шерсть",
    "wool_pink":       "🩷 Розовая шерсть",
    "wool_gray":       "🩶 Серая шерсть",
    "wool_light_gray": "⬜ Светло-серая шерсть",
    "wool_cyan":       "🩵 Циановая шерсть",
    "wool_purple":     "🟣 Фиолетовая шерсть",
    "wool_blue":       "🔵 Синяя шерсть",
    "wool_brown":      "🟫 Коричневая шерсть",
    "wool_green":      "🟢 Зелёная шерсть",
    "wool_red":        "🔴 Красная шерсть",
    "wool_black":      "⬛ Чёрная шерсть",
    # Concrete
    "concrete_white":      "⬜ Белый бетон",
    "concrete_orange":     "🟠 Оранжевый бетон",
    "concrete_yellow":     "🟡 Жёлтый бетон",
    "concrete_lime":       "💚 Лаймовый бетон",
    "concrete_red":        "🔴 Красный бетон",
    "concrete_blue":       "🔵 Синий бетон",
    "concrete_black":      "⬛ Чёрный бетон",
    "concrete_green":      "🟢 Зелёный бетон",
    "concrete_cyan":       "🩵 Циановый бетон",
    "concrete_purple":     "🟣 Фиолетовый бетон",
    "concrete_magenta":    "💜 Пурпурный бетон",
    "concrete_pink":       "🩷 Розовый бетон",
    "concrete_gray":       "🩶 Серый бетон",
    "concrete_brown":      "🟫 Коричневый бетон",
    "concrete_light_blue": "🩵 Голубой бетон",
    "concrete_light_gray": "⬜ Светло-серый бетон",
    # Terracotta
    "terracotta":          "🏺 Терракота",
    "terracotta_white":    "⬜ Белая терракота",
    "terracotta_orange":   "🟠 Оранжевая терракота",
    "terracotta_yellow":   "🟡 Жёлтая терракота",
    "terracotta_red":      "🔴 Красная терракота",
    "terracotta_blue":     "🔵 Синяя терракота",
    "terracotta_black":    "⬛ Чёрная терракота",
    "terracotta_green":    "🟢 Зелёная терракота",
    "terracotta_cyan":     "🩵 Циановая терракота",
    "terracotta_purple":   "🟣 Фиолетовая терракота",
    "terracotta_brown":    "🟫 Коричневая терракота",
    # Glass
    "glass":               "🪟 Стекло",
    "glass_white":         "⬜ Белое стекло",
    "glass_orange":        "🟠 Оранжевое стекло",
    "glass_red":           "🔴 Красное стекло",
    "glass_blue":          "🔵 Синее стекло",
    "glass_yellow":        "🟡 Жёлтое стекло",
    "glass_green":         "🟢 Зелёное стекло",
    "glass_cyan":          "🩵 Циановое стекло",
    "glass_purple":        "🟣 Фиолетовое стекло",
    "glass_black":         "⬛ Чёрное стекло",
    # Wood
    "jungle_log":          "🪵 Джунглевое бревно",
    "jungle_planks":       "🪵 Джунглевые доски",
    "jungle_leaves":       "🍃 Джунглевые листья",
    "acacia_log":          "🪵 Акациевое бревно",
    "acacia_planks":       "🪵 Акациевые доски",
    "acacia_leaves":       "🍃 Акациевые листья",
    "dark_oak_log":        "🪵 Тёмнодубовое бревно",
    "dark_oak_planks":     "🪵 Тёмнодубовые доски",
    "dark_oak_leaves":     "🍃 Тёмнодубовые листья",
    "mangrove_log":        "🪵 Мангровое бревно",
    "mangrove_planks":     "🪵 Мангровые доски",
    "cherry_log":          "🪵 Вишнёвое бревно",
    "cherry_planks":       "🪵 Вишнёвые доски",
    "cherry_leaves":       "🌸 Вишнёвые листья",
    # Stone
    "stone_bricks":        "🧱 Каменный кирпич",
    "cracked_stone_bricks":"🧱 Треснутый каменный кирпич",
    "mossy_stone_bricks":  "🧱 Замшелый каменный кирпич",
    "deepslate":           "🪨 Глубинный сланец",
    "deepslate_bricks":    "🪨 Кирпичи из сланца",
    "deepslate_tiles":     "🪨 Плитки из сланца",
    "polished_deepslate":  "🪨 Полированный сланец",
    "granite":             "🪨 Гранит",
    "diorite":             "🪨 Диорит",
    "andesite":            "🪨 Андезит",
    "polished_granite":    "🪨 Полированный гранит",
    "polished_diorite":    "🪨 Полированный диорит",
    "polished_andesite":   "🪨 Полированный андезит",
    "calcite":             "🪨 Кальцит",
    "tuff":                "🪨 Туф",
    "dripstone":           "🪨 Сталактит",
    # Special blocks
    "ice":                 "🧊 Лёд",
    "packed_ice":          "🧊 Паковый лёд",
    "blue_ice":            "🧊 Синий лёд",
    "snow":                "❄️ Снег",
    "powder_snow":         "❄️ Рыхлый снег",
    "cactus_top":          "🌵 Кактус (верх)",
    "cactus_side":         "🌵 Кактус (сторона)",
    "pumpkin_top":         "🎃 Тыква (верх)",
    "pumpkin_side":        "🎃 Тыква (сторона)",
    "pumpkin_face":        "🎃 Тыква (лицо)",
    "melon_top":           "🍉 Арбуз (верх)",
    "melon_side":          "🍉 Арбуз (сторона)",
    "hay_top":             "🌾 Стог сена (верх)",
    "hay_side":            "🌾 Стог сена (сторона)",
    "sponge":              "🧽 Губка",
    "wet_sponge":          "🧽 Мокрая губка",
    "honeycomb":           "🍯 Соты",
    "honey":               "🍯 Мёд",
    "amethyst":            "💜 Аметист",
    "budding_amethyst":    "💜 Растущий аметист",
    "sculk":               "🟣 Вопль",
    "sculk_catalyst":      "🟣 Катализатор воплей",
    "mud":                 "🟤 Грязь",
    "muddy_mangrove_roots":"🌿 Грязные корни мангров",
    # Nether
    "nether_bricks":       "🔴 Кирпичи Незера",
    "nether_quartz_ore":   "⬜ Кварцевая руда",
    "nether_gold_ore":     "🟡 Золотая руда Незера",
    "crimson_planks":      "🔴 Багровые доски",
    "warped_planks":       "🟣 Искажённые доски",
    "crimson_stem":        "🔴 Ствол багрового дерева",
    "warped_stem":         "🟣 Ствол искажённого дерева",
    "basalt":              "⬛ Базальт",
    "blackstone":          "⬛ Чёрный камень",
    "gilded_blackstone":   "🟡 Позолоченный чёрный камень",
    "quartz_block":        "⬜ Блок кварца",
    "smooth_quartz":       "⬜ Гладкий кварц",
    "magma_block":         "🔴 Магма",
    # End
    "end_stone_bricks":    "🟨 Кирпичи камня края",
    "purpur_pillar":       "🟣 Пурпурная колонна",
    "chorus_plant":        "🟣 Хорус",
    # Mineral blocks
    "iron_block":          "⚙️ Блок железа",
    "gold_block":          "🟡 Блок золота",
    "diamond_block":       "💎 Блок алмаза",
    "emerald_block":       "💚 Блок изумруда",
    "netherite_block":     "⬛ Блок незерита",
    "lapis_block":         "🔵 Блок лазурита",
    "coal_block":          "⬛ Блок угля",
    "redstone_block":      "🔴 Блок редстоуна",
    "copper_block":        "🟠 Блок меди",
    "exposed_copper":      "🟠 Окисленная медь (1)",
    "weathered_copper":    "🟠 Окисленная медь (2)",
    "oxidized_copper":     "🟢 Окисленная медь (3)",
    "cut_copper":          "🟠 Вырезанная медь",
    "slime_block":         "🟢 Блок слизи",
    "prismarine":          "🩵 Призмарин",
    "prismarine_bricks":   "🩵 Кирпичи призмарина",
    "dark_prismarine":     "🟢 Тёмный призмарин",
    "sea_lantern":         "🪔 Морской фонарь",
    # Natural
    "mycelium_top":        "🍄 Мицелий (верх)",
    "podzol_top":          "🟤 Подзол (верх)",
    "clay":                "🟫 Глина",
    "red_sand":            "🟠 Красный песок",
    "sandstone_top":       "🏜 Песчаник (верх)",
    "sandstone_side":      "🏜 Песчаник (сторона)",
    "red_sandstone_top":   "🟠 Красный песчаник (верх)",
    "red_sandstone_side":  "🟠 Красный песчаник (сторона)",
    # Redstone
    "note_block":          "🎵 Блок нот",
    "jukebox_top":         "💿 Музыкальный ящик",
    "enchanting_table_top":"✨ Стол зачарований",
    "beacon":              "🔦 Маяк",
    "respawn_anchor_top":  "🔮 Якорь возрождения",
    "dispenser_front":     "⚙️ Раздатчик",
    "dropper_front":       "⚙️ Бросатель",
    "observer_front":      "👁 Наблюдатель",
    "piston_top":          "⚙️ Поршень (верх)",
    "piston_side":         "⚙️ Поршень (сторона)",
    "sticky_piston_top":   "⚙️ Липкий поршень",
    # Mobs extended
    "pillager":        "🏹 Разбойник",
    "vindicator":      "🪓 Заклинатель",
    "evoker":          "🔮 Призыватель",
    "witch":           "🧙 Ведьма",
    "ravager":         "💢 Разоритель",
    "bee":             "🐝 Пчела",
    "panda":           "🐼 Панда",
    "fox":             "🦊 Лиса",
    "dolphin":         "🐬 Дельфин",
    "turtle":          "🐢 Черепаха",
    "parrot_red":      "🦜 Попугай",
    "polar_bear":      "🐻 Полярный медведь",
    "llama":           "🦙 Лама",
    "donkey":          "🫏 Осёл",
    "mule":            "🐴 Мул",
    "ocelot":          "🐱 Оцелот",
    "rabbit_entity":   "🐰 Кролик",
    "mushroom_cow":    "🍄 Грибная корова",
    "squid":           "🦑 Кальмар",
    "glow_squid":      "✨ Светящийся кальмар",
    "bat":             "🦇 Летучая мышь",
    "silverfish":      "🐛 Чешуйница",
    "cave_spider":     "🕷 Пещерный паук",
    "slime_entity":    "🟢 Слизень",
    "magma_cube":      "🔴 Огненный куб",
    "zombie_piglin":   "🧟 Зомби-пиглин",
    "piglin":          "🐷 Пиглин",
    "hoglin":          "🐗 Хоглин",
    "zoglin":          "🐗 Зоглин",
    "strider":         "🦎 Страйдер",
    "warden":          "🟫 Хранитель",
    "allay":           "🩵 Эллэй",
    "frog":            "🐸 Лягушка",
    "axolotl":         "🦎 Аксолотль",
    "goat":            "🐐 Козёл",
    "camel":           "🐪 Верблюд",
    "sniffer":         "🦕 Нюхач",
}

SOUND_LABELS = {
    # Игрок
    "hurt":            "💥 Урон игрока",
    "death":           "💀 Смерть игрока",
    "explosion":       "💣 Взрыв",
    "eat":             "🍖 Еда",
    "drink":           "🧪 Питьё зелья",
    "levelup":         "⬆️ Повышение уровня",
    "click":           "🖱 Клик кнопки",
    "swim":            "🏊 Плавание",
    "fall_big":        "💢 Большое падение",
    "fall_small":      "🩹 Малое падение",
    "xp_pickup":       "✨ Подбор опыта",
    "item_pickup":     "🎒 Подбор предмета",
    # Оружие
    "bow_shoot":       "🏹 Выстрел из лука",
    "sword_hit":       "⚔️ Удар мечом",
    "anvil":           "⚒ Наковальня",
    # Блоки
    "chest_open":      "📦 Открытие сундука",
    "chest_close":     "📦 Закрытие сундука",
    "door_open":       "🚪 Открытие двери",
    "door_close":      "🚪 Закрытие двери",
    "iron_door_open":  "🔩 Открытие железной двери",
    "block_glass_break": "🪟 Разбитие стекла",
    "block_grass":     "🌿 Шаги по траве",
    "block_stone":     "🪨 Шаги по камню",
    "block_wood":      "🪵 Шаги по дереву",
    "block_sand":      "🏜 Шаги по песку",
    "block_gravel":    "⬜ Шаги по гравию",
    "ore_break":       "⛏ Разрушение руды",
    "furnace_fire":    "🔥 Розжиг (огниво)",
    "tnt_fuse":        "💣 Поджог TNT",
    # Порталы и телепорт
    "portal":          "🌀 Портал Незера",
    "teleport":        "🟣 Телепорт Эндермена",
    # Мобы — нежить
    "zombie_ambient":  "🧟 Зомби (звук)",
    "zombie_hurt":     "🧟 Зомби (ранение)",
    "zombie_death":    "🧟 Зомби (смерть)",
    "skeleton_ambient":"💀 Скелет (звук)",
    "skeleton_hurt":   "💀 Скелет (ранение)",
    "skeleton_death":  "💀 Скелет (смерть)",
    "creeper_hiss":    "💚 Шипение крипера",
    "creeper_explode": "💚 Взрыв крипера",
    "enderman_ambient":"🕴 Эндермен (звук)",
    "enderman_scream": "😱 Крик Эндермена",
    "enderman_stare":  "👁 Взгляд Эндермена",
    # Мобы — нейтральные
    "pig_ambient":     "🐷 Свинья",
    "pig_death":       "🐷 Смерть свиньи",
    "cow_ambient":     "🐄 Корова",
    "cow_hurt":        "🐄 Корова (ранение)",
    "chicken_ambient": "🐔 Курица",
    "sheep_ambient":   "🐑 Овца",
    "wolf_ambient":    "🐺 Волк (лай)",
    "wolf_hurt":       "🐺 Волк (ранение)",
    "wolf_howl":       "🐺 Волк (вой)",
    "cat_ambient":     "🐱 Кошка",
    "spider_ambient":  "🕷 Паук (звук)",
    "spider_hurt":     "🕷 Паук (ранение)",
    "horse_ambient":   "🐴 Лошадь (звук)",
    "horse_gallop":    "🐴 Лошадь (галоп)",
    "bee_ambient":     "🐝 Пчела (жужжание)",
    "bee_sting":       "🐝 Пчела (укус)",
    "bat_ambient":     "🦇 Летучая мышь",
    "slime_jump":      "🟢 Слизень (прыжок)",
    "axolotl_idle":    "🦎 Аксолотль",
    # Мобы — незер
    "blaze_ambient":   "🔥 Иблис (дыхание)",
    "blaze_shoot":     "🔥 Иблис (выстрел)",
    "ghast_ambient":   "👻 Гаст (звук)",
    "ghast_shoot":     "👻 Гаст (выстрел)",
    "ghast_scream":    "👻 Гаст (крик)",
    "hoglin_ambient":  "🐗 Хоглин",
    "piglin_ambient":  "🐷 Пиглин",
    "strider_ambient": "🦎 Страйдер",
    # Боссы
    "wither_spawn":    "💀 Иссушитель (появление)",
    "wither_shoot":    "💀 Иссушитель (выстрел)",
    "ender_dragon_ambient": "🐉 Дракон Края (звук)",
    "ender_dragon_roar":    "🐉 Дракон Края (рёв)",
    "warden_ambient":  "🟫 Хранитель (звук)",
    "warden_roar":     "🟫 Хранитель (рёв)",
    # Деревня
    "villager":        "👨‍🌾 Житель (речь)",
    "villager_trade":  "👨‍🌾 Житель (торговля)",
    "villager_no":     "👨‍🌾 Житель (отказ)",
    "iron_golem_ambient": "🤖 Железный голем (шаги)",
    "iron_golem_hurt": "🤖 Железный голем (удар)",
    # Новые мобы
    "sniffer_ambient": "🦕 Нюхач",
    "camel_ambient":   "🐪 Верблюд",
    # Окружение
    "thunder":         "⛈ Гром",
    "rain":            "🌧 Дождь",
    "fire":            "🔥 Огонь",
    "water_ambient":   "💦 Под водой",
    "nether_ambient":  "🔴 Атмосфера Незера",
    "end_ambient":     "🟣 Атмосфера Края",
    "cave_ambient":    "🕳 Звуки пещеры",
    "splash":          "💦 Всплеск",
    # Музыка
    "music_game":      "🎵 Музыка в игре",
    "music_menu":      "🎵 Музыка меню",
    "music_creative":  "🎵 Музыка Креатива",
}

CATEGORIES = {
    "weapons": ("⚔️ Оружие",      ["sword_wood","sword_stone","sword_iron","sword_gold","sword_diamond","sword_netherite","bow","crossbow","trident","axe_wood","axe_stone","axe_iron","axe_gold","axe_diamond","axe_netherite"]),
    "tools":   ("🔨 Инструменты", ["pickaxe_wood","pickaxe_stone","pickaxe_iron","pickaxe_gold","pickaxe_diamond","pickaxe_netherite","shovel_wood","shovel_stone","shovel_iron","shovel_gold","shovel_diamond","shovel_netherite","hoe_wood","hoe_stone","hoe_iron","hoe_gold","hoe_diamond","hoe_netherite"]),
    "armor":   ("🛡 Броня",        ["leather_helmet","leather_chestplate","leather_leggings","leather_boots","chainmail_helmet","chainmail_chestplate","chainmail_leggings","chainmail_boots","iron_helmet","iron_chestplate","iron_leggings","iron_boots","gold_helmet","gold_chestplate","gold_leggings","gold_boots","diamond_helmet","diamond_chestplate","diamond_leggings","diamond_boots","netherite_helmet","netherite_chestplate","netherite_leggings","netherite_boots"]),
    "blocks":  ("🧱 Базовые блоки",["grass_top","grass_side","dirt","stone","cobblestone","sand","gravel","oak_log","oak_planks","oak_leaves","birch_log","birch_planks","spruce_log","spruce_planks","netherrack","obsidian","bedrock","tnt_top","tnt_side","crafting_table_top","crafting_table_side","furnace_front","chest_front","bookshelf","diamond_ore","iron_ore","gold_ore","coal_ore","emerald_ore","redstone_ore","lapis_ore","ancient_debris","crying_obsidian","glowstone","soul_sand","end_stone","purpur_block"]),
    "wool":    ("🐑 Шерсть",       ["wool_white","wool_orange","wool_magenta","wool_light_blue","wool_yellow","wool_lime","wool_pink","wool_gray","wool_light_gray","wool_cyan","wool_purple","wool_blue","wool_brown","wool_green","wool_red","wool_black"]),
    "concrete":("🟫 Бетон",        ["concrete_white","concrete_orange","concrete_yellow","concrete_lime","concrete_red","concrete_blue","concrete_black","concrete_green","concrete_cyan","concrete_purple","concrete_magenta","concrete_pink","concrete_gray","concrete_brown","concrete_light_blue","concrete_light_gray"]),
    "terracotta":("🏺 Терракота",  ["terracotta","terracotta_white","terracotta_orange","terracotta_yellow","terracotta_red","terracotta_blue","terracotta_black","terracotta_green","terracotta_cyan","terracotta_purple","terracotta_brown"]),
    "glass":   ("🪟 Стекло",       ["glass","glass_white","glass_orange","glass_red","glass_blue","glass_yellow","glass_green","glass_cyan","glass_purple","glass_black"]),
    "wood":    ("🪵 Дерево",       ["jungle_log","jungle_planks","jungle_leaves","acacia_log","acacia_planks","acacia_leaves","dark_oak_log","dark_oak_planks","dark_oak_leaves","mangrove_log","mangrove_planks","cherry_log","cherry_planks","cherry_leaves"]),
    "stone":   ("🪨 Камень",       ["stone_bricks","cracked_stone_bricks","mossy_stone_bricks","deepslate","deepslate_bricks","deepslate_tiles","polished_deepslate","granite","diorite","andesite","polished_granite","polished_diorite","polished_andesite","calcite","tuff","dripstone"]),
    "special": ("✨ Особые блоки", ["ice","packed_ice","blue_ice","snow","powder_snow","cactus_top","cactus_side","pumpkin_top","pumpkin_side","pumpkin_face","melon_top","melon_side","hay_top","hay_side","sponge","wet_sponge","honeycomb","honey","amethyst","budding_amethyst","sculk","sculk_catalyst","mud","muddy_mangrove_roots"]),
    "nether":  ("🔴 Незер",        ["nether_bricks","nether_quartz_ore","nether_gold_ore","crimson_planks","warped_planks","crimson_stem","warped_stem","basalt","blackstone","gilded_blackstone","quartz_block","smooth_quartz","magma_block"]),
    "end":     ("🟣 Край",         ["end_stone_bricks","purpur_pillar","chorus_plant"]),
    "mineral": ("💎 Минеральные блоки", ["iron_block","gold_block","diamond_block","emerald_block","netherite_block","lapis_block","coal_block","redstone_block","copper_block","exposed_copper","weathered_copper","oxidized_copper","cut_copper","slime_block","prismarine","prismarine_bricks","dark_prismarine","sea_lantern"]),
    "natural": ("🌿 Природа",      ["mycelium_top","podzol_top","clay","red_sand","sandstone_top","sandstone_side","red_sandstone_top","red_sandstone_side"]),
    "redstone":("⚙️ Механизмы",    ["note_block","jukebox_top","enchanting_table_top","beacon","respawn_anchor_top","dispenser_front","dropper_front","observer_front","piston_top","piston_side","sticky_piston_top"]),
    "items":   ("🎒 Предметы",     ["apple","golden_apple","enchanted_apple","bread","cooked_beef","beef","cooked_chicken","diamond","emerald","iron_ingot","gold_ingot","netherite_ingot","coal","arrow","spectral_arrow","shield","totem","ender_pearl","ender_eye","blaze_rod","nether_star","heart_of_sea","elytra"]),
    "mobs":    ("🐷 Мобы",         ["zombie","skeleton","creeper","enderman","pig","cow","sheep","chicken","spider","blaze","ghast","wither","ender_dragon","villager","iron_golem","wolf","cat","horse","phantom","pillager","vindicator","evoker","witch","ravager","bee","panda","fox","dolphin","turtle","parrot_red","polar_bear","llama","donkey","mule","ocelot","rabbit_entity","mushroom_cow","squid","glow_squid","bat","silverfish","cave_spider","slime_entity","magma_cube","zombie_piglin","piglin","hoglin","zoglin","strider","warden","allay","frog","axolotl","goat","camel","sniffer"]),
    "gui":     ("🎮 Интерфейс",    ["hotbar","icons","crosshair","container_generic","container_inventory","container_crafting","container_furnace","container_chest","title_screen"]),
}

# ─── FSM STATES ────────────────────────────────────────────────────────────────
class PackStates(StatesGroup):
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

class EditStates(StatesGroup):
    upload_pack   = State()   # ждём загрузки .zip/.mcpack
    choose_action = State()   # добавить текстуру / звук / скачать
    choose_category = State()
    choose_item   = State()
    upload_file   = State()

# ─── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль",          callback_data="profile"),
         InlineKeyboardButton(text="ℹ️ О боте",           callback_data="about")],
        [InlineKeyboardButton(text="💎 Купить подписку",  callback_data="buy_sub")],
        [InlineKeyboardButton(text="🎨 Создать ресурс-пак", callback_data="create_pack")],
        [InlineKeyboardButton(text="✏️ Редактировать пак",  callback_data="edit_pack")],
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

def pack_content_kb():
    """Что добавить в пак — можно всё вместе."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Добавить текстуру", callback_data="add_texture")],
        [InlineKeyboardButton(text="🔊 Добавить звук",     callback_data="add_sound")],
        [InlineKeyboardButton(text="📦 Скачать пак",       callback_data="finish_pack")],
        [InlineKeyboardButton(text="⬅️ Назад",             callback_data="main_menu")],
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

def items_kb_with_back(items: list):
    rows = []
    for key in items:
        rows.append([InlineKeyboardButton(text=ITEM_LABELS.get(key, key), callback_data=f"item_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="back_to_categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def add_more_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Ещё текстуру",  callback_data="add_texture"),
         InlineKeyboardButton(text="🔊 Ещё звук",      callback_data="add_sound")],
        [InlineKeyboardButton(text="📦 Скачать пак",   callback_data="finish_pack")],
    ])

def add_more_sound_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Ещё текстуру",  callback_data="add_texture"),
         InlineKeyboardButton(text="🔊 Ещё звук",      callback_data="add_sound")],
        [InlineKeyboardButton(text="📦 Скачать пак",   callback_data="finish_pack")],
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

def edit_pack_content_kb():
    """Меню действий при редактировании существующего пака."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖼 Добавить текстуру", callback_data="edit_add_texture")],
        [InlineKeyboardButton(text="🔊 Добавить звук",     callback_data="edit_add_sound")],
        [InlineKeyboardButton(text="📦 Скачать изменённый пак", callback_data="edit_finish_pack")],
        [InlineKeyboardButton(text="⬅️ Назад",             callback_data="main_menu")],
    ])

def edit_category_kb():
    rows = []
    for key, (label, _) in CATEGORIES.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"ecat_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="edit_back_content")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def edit_sound_category_kb():
    rows = []
    for key, label in SOUND_LABELS.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"esnd_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="edit_back_content")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def edit_items_kb(items: list):
    rows = []
    for key in items:
        rows.append([InlineKeyboardButton(text=ITEM_LABELS.get(key, key), callback_data=f"eitem_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к категориям", callback_data="edit_back_categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ─── PACK BUILDER ──────────────────────────────────────────────────────────────
def _make_sounds_json(sound_keys: list) -> str:
    """Генерирует sounds.json для Java Edition."""
    result = {}
    for key in sound_keys:
        event     = SOUNDS[key][2]   # Java event
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
        event    = SOUNDS[key][3]   # Bedrock event (исправлено!)
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
    name = message.from_user.first_name or "игрок"
    await message.answer(
        f"🎮 <b>Привет, {name}! Добро пожаловать в PackCraftBot!</b>\n\n"
        "Создай кастомный ресурс-пак для Minecraft прямо здесь:\n"
        "• 🖼 <b>Текстуры</b> — блоки, мобы, броня, инструменты, GUI\n"
        "• 🔊 <b>Звуки</b> — замени любой звук игры\n"
        "• ☕ Java Edition и 📱 Bedrock Edition\n"
        "• 📦 Текстуры и звуки в <b>одном паке</b>!\n\n"
        "🆓 <b>Бесплатно:</b> 1 пак\n"
        "💎 <b>Подписка:</b> безлимитные паки!\n\n"
        "Выбери действие:",
        reply_markup=main_menu_kb(), parse_mode="HTML"
    )

@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📖 <b>Как пользоваться PackCraftBot?</b>\n\n"
        "<b>🎨 Создать новый пак:</b>\n"
        "<b>1.</b> Нажми «🎨 Создать ресурс-пак»\n"
        "<b>2.</b> Выбери версию: ☕ Java или 📱 Bedrock\n"
        "<b>3.</b> Введи название и описание пака\n"
        "<b>4.</b> При желании загрузи иконку пака\n"
        "<b>5.</b> Добавляй текстуры (PNG) и звуки (OGG) — сколько угодно\n"
        "<b>6.</b> Нажми «📦 Скачать пак» — получишь готовый файл!\n\n"
        "<b>✏️ Редактировать существующий пак (подписка):</b>\n"
        "<b>1.</b> Нажми «✏️ Редактировать пак»\n"
        "<b>2.</b> Отправь свой .zip или .mcpack файл\n"
        "<b>3.</b> Добавь новые текстуры и звуки\n"
        "<b>4.</b> Скачай обновлённый пак!\n\n"
        "📌 <b>Форматы файлов:</b>\n"
        "• Текстуры: <code>.png</code> (16×16 до 128×128)\n"
        "• Звуки: <code>.ogg</code> (конвертировать: audio.online-convert.com)\n\n"
        "📌 <b>Установка:</b>\n"
        "• <b>Java:</b> скопируй .zip в папку <code>resourcepacks</code>\n"
        "• <b>Bedrock:</b> переименуй в .mcpack и открой",
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
        "✅ Текстуры: броня, оружие, инструменты, блоки, шерсть, бетон, стекло, дерево, мобы, GUI и многое другое\n"
        "✅ Звуки: мобы, блоки, окружение, музыка — более 70 звуков\n"
        "✅ Автоматическая сборка .zip / .mcpack\n"
        "✅ sounds.json / sound_definitions.json генерируются автоматически\n"
        "✅ <b>Редактирование существующего пака</b> — загрузи и дополни!\n\n"
        "💎 <b>Тарифы:</b>\n"
        "• Бесплатно — 1 пак\n"
        "• Неделя — 50⭐ или $1\n"
        "• Навсегда — 150⭐ или $3\n\n"
        "🔒 Редактирование паков — только для подписчиков",
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
    # Проверяем что payload корректный
    valid_payloads = {"stars_week", "stars_forever"}
    if pcq.invoice_payload not in valid_payloads:
        await bot.answer_pre_checkout_query(pcq.id, ok=False, error_message="Неверный платёж. Попробуй снова.")
        return
    await bot.answer_pre_checkout_query(pcq.id, ok=True)

@dp.message(F.successful_payment)
async def payment_done(message: Message):
    uid     = message.from_user.id
    payload = message.successful_payment.invoice_payload
    amount  = message.successful_payment.total_amount
    log_payment(uid, "stars", amount, payload)
    sub_type = "forever" if "forever" in payload else "week"

    # Не понижаем forever → week
    u = get_user(uid)
    if u and u.get("sub_type") == "forever":
        await message.answer(
            "ℹ️ У тебя уже есть подписка <b>навсегда</b> — она сильнее!\n"
            "Обратись к администратору для возврата средств.",
            parse_mode="HTML", reply_markup=main_menu_kb()
        )
        return

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
    uid    = cq.from_user.id
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            "https://pay.crypt.bot/api/createInvoice",
            headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
            json={
                "asset": "USDT", "amount": str(amount),
                "description": f"PackCraftBot — {'неделя' if plan=='week' else 'навсегда'}",
                # Прячем uid в payload чтобы потом проверить
                "payload": f"crypto_{plan}_{uid}",
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
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"check_crypto_{invoice_id}_{plan}_{uid}")],
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
    parts = cq.data.split("_")
    # Формат: check_crypto_{invoice_id}_{plan}_{uid}
    if len(parts) < 5:
        await cq.answer("Неверный запрос.", show_alert=True)
        return
    invoice_id   = parts[2]
    plan         = parts[3]
    owner_uid    = int(parts[4])

    # Проверяем что кнопку нажал тот кто платил
    if cq.from_user.id != owner_uid:
        await cq.answer("❌ Это не твой счёт.", show_alert=True)
        return

    import aiohttp
    async with aiohttp.ClientSession() as session:
        resp = await session.get(
            "https://pay.crypt.bot/api/getInvoices",
            headers={"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN},
            params={"invoice_ids": invoice_id}
        )
        data = await resp.json()

    if not data.get("ok") or not data["result"]["items"]:
        await cq.answer("Ошибка проверки. Попробуй позже.", show_alert=True)
        return

    inv = data["result"]["items"][0]

    # Дополнительно проверяем payload из самого invoice
    expected_payload = f"crypto_{plan}_{owner_uid}"
    if inv.get("payload") != expected_payload:
        await cq.answer("❌ Счёт не совпадает. Попробуй снова.", show_alert=True)
        return

    if inv["status"] == "paid":
        uid = cq.from_user.id
        # Не понижаем forever → week
        u = get_user(uid)
        if u and u.get("sub_type") == "forever" and plan == "week":
            await cq.message.edit_text(
                "ℹ️ У тебя уже есть подписка <b>навсегда</b>!",
                parse_mode="HTML", reply_markup=main_menu_kb()
            )
            return
        log_payment(uid, "crypto", inv.get("amount"), f"crypto_{plan}")
        give_sub(uid, plan)
        label = "навсегда ♾" if plan == "forever" else "на неделю 📅"
        await cq.message.edit_text(
            f"✅ <b>Оплата получена! Подписка {label} активирована.</b>",
            parse_mode="HTML", reply_markup=main_menu_kb()
        )
    else:
        await cq.answer("Оплата ещё не прошла. Подожди немного и попробуй снова.", show_alert=True)

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
    await state.update_data(texture_files={}, sound_files={})
    await state.set_state(PackStates.choose_version)
    await cq.message.edit_text(
        "🌍 <b>Выбери версию Minecraft:</b>\n\n"
        "В один пак войдут и текстуры, и звуки — всё вместе!",
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
    await _proceed_to_content_menu(message, state)

@dp.callback_query(PackStates.upload_icon, F.data == "skip_icon")
async def cb_skip_icon(cq: CallbackQuery, state: FSMContext):
    await state.update_data(pack_icon=None)
    await _proceed_to_content_menu(cq.message, state, edit=True)

async def _proceed_to_content_menu(msg, state: FSMContext, edit: bool = False):
    """Показывает единое меню: добавить текстуру / добавить звук / скачать."""
    data = await state.get_data()
    tex_count = len(data.get("texture_files", {}))
    snd_count = len(data.get("sound_files", {}))
    summary = ""
    if tex_count or snd_count:
        summary = f"\n\n📊 В паке уже: 🖼 {tex_count} текстур, 🔊 {snd_count} звуков"
    await state.set_state(PackStates.add_more)
    text = f"🎨 <b>Что добавить в пак?</b>{summary}\n\nДобавляй текстуры и звуки — всё войдёт в один файл."
    if edit:
        await msg.edit_text(text, reply_markup=pack_content_kb(), parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=pack_content_kb(), parse_mode="HTML")

@dp.callback_query(PackStates.choose_category, F.data.startswith("cat_"))
async def cb_category(cq: CallbackQuery, state: FSMContext):
    cat_key = cq.data[4:]
    _, items = CATEGORIES[cat_key]
    await state.update_data(category=cat_key)
    await state.set_state(PackStates.choose_item)
    await cq.message.edit_text(
        "🔍 <b>Выбери что заменить:</b>",
        reply_markup=items_kb_with_back(items), parse_mode="HTML"
    )

@dp.callback_query(PackStates.choose_category, F.data.startswith("snd_"))
async def cb_sound_item(cq: CallbackQuery, state: FSMContext):
    snd_key = cq.data[4:]
    await state.update_data(current_item=snd_key, current_mode="sound")
    await state.set_state(PackStates.upload_file)
    label = SOUND_LABELS.get(snd_key, snd_key)
    await cq.message.edit_text(
        f"🔊 <b>{label}</b>\n\nОтправь файл звука в формате <b>.ogg</b>\n\n"
        "💡 Конвертировать mp3→ogg можно на сайте <a href='https://audio.online-convert.com/ru/convert-to-ogg'>online-convert.com</a>",
        reply_markup=back_kb("back_to_sounds"), parse_mode="HTML", disable_web_page_preview=True
    )

@dp.callback_query(PackStates.choose_item, F.data.startswith("item_"))
async def cb_item(cq: CallbackQuery, state: FSMContext):
    item_key = cq.data[5:]
    await state.update_data(current_item=item_key, current_mode="texture")
    await state.set_state(PackStates.upload_file)
    label = ITEM_LABELS.get(item_key, item_key)
    # Назад → к списку предметов этой категории
    data = await state.get_data()
    cat_key = data.get("category", "weapons")
    await cq.message.edit_text(
        f"🖼 <b>{label}</b>\n\nОтправь текстуру в формате <b>PNG</b>\n"
        "Поддерживаемые размеры: 16×16, 32×32, 64×64, 128×128",
        reply_markup=back_kb(f"back_to_items_{cat_key}"), parse_mode="HTML"
    )

# ─── УМНЫЕ КНОПКИ "НАЗАД" ──────────────────────────────────────────────────────
@dp.callback_query(F.data == "back_to_sounds")
async def cb_back_to_sounds(cq: CallbackQuery, state: FSMContext):
    await state.set_state(PackStates.choose_category)
    await cq.message.edit_text(
        "🔊 <b>Выбери звук для замены:</b>",
        reply_markup=sound_category_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "back_to_categories")
async def cb_back_to_categories(cq: CallbackQuery, state: FSMContext):
    await state.set_state(PackStates.choose_category)
    await cq.message.edit_text(
        "📂 <b>Выбери категорию текстуры:</b>",
        reply_markup=category_kb("texture"), parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("back_to_items_"))
async def cb_back_to_items(cq: CallbackQuery, state: FSMContext):
    cat_key = cq.data.replace("back_to_items_", "")
    if cat_key not in CATEGORIES:
        await cb_back_to_categories(cq, state)
        return
    _, items = CATEGORIES[cat_key]
    await state.set_state(PackStates.choose_item)
    await cq.message.edit_text(
        "🔍 <b>Выбери что заменить:</b>",
        reply_markup=items_kb(items), parse_mode="HTML"
    )

@dp.message(PackStates.upload_file, F.photo | F.document | F.audio)
async def upload_file(message: Message, state: FSMContext):
    data         = await state.get_data()
    current_mode = data.get("current_mode", "texture")
    item_key     = data.get("current_item")
    tex_files    = data.get("texture_files", {})
    snd_files    = data.get("sound_files", {})

    if current_mode == "sound":
        # Принимаем .ogg как документ ИЛИ как audio (пересланные файлы)
        file_id   = None
        file_name = ""
        if message.document:
            file_id   = message.document.file_id
            file_name = message.document.file_name or ""
        elif message.audio:
            file_id   = message.audio.file_id
            file_name = message.audio.file_name or message.audio.title or "sound.ogg"
        else:
            await message.answer(
                "❌ Отправь <b>.ogg файл</b> как документ (скрепка → файл)\n\n"
                "💡 Конвертировать можно на audio.online-convert.com",
                parse_mode="HTML"
            )
            return

        if not file_name.lower().endswith(".ogg"):
            await message.answer(
                "❌ Нужен файл в формате <b>.ogg</b>\n\n"
                "💡 Конвертировать mp3/wav → ogg можно бесплатно:\naudio.online-convert.com",
                parse_mode="HTML"
            )
            return

        file = await bot.get_file(file_id)
        buf  = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        snd_files[item_key] = buf.getvalue()
        await state.update_data(sound_files=snd_files)
        label = SOUND_LABELS.get(item_key, item_key)
        await message.answer(
            f"✅ <b>{label}</b> добавлен!\n\nЧто добавим дальше?",
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
            f"✅ <b>{label}</b> добавлена!\n\nЧто добавим дальше?",
            reply_markup=add_more_kb(), parse_mode="HTML"
        )
    await state.set_state(PackStates.add_more)

@dp.callback_query(F.data == "add_texture")
async def cb_add_texture(cq: CallbackQuery, state: FSMContext):
    await state.update_data(current_mode="texture")
    await state.set_state(PackStates.choose_category)
    await cq.message.edit_text(
        "📂 <b>Выбери категорию текстуры:</b>",
        reply_markup=category_kb("texture"), parse_mode="HTML"
    )

@dp.callback_query(F.data == "add_sound")
async def cb_add_sound(cq: CallbackQuery, state: FSMContext):
    await state.update_data(current_mode="sound")
    await state.set_state(PackStates.choose_category)
    await cq.message.edit_text(
        "🔊 <b>Выбери звук для замены:</b>",
        reply_markup=sound_category_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "choose_cat")
async def cb_choose_cat(cq: CallbackQuery, state: FSMContext):
    await cb_add_texture(cq, state)

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
        await cq.answer("⚠️ Добавь хотя бы одну текстуру или звук!", show_alert=True)
        return

    await cq.message.edit_text("⏳ <b>Собираю ресурс-пак...</b> Подожди секунду!", parse_mode="HTML")

    try:
        if version == "java":
            pack_bytes = build_java_pack(tex_files, snd_files, pack_name, pack_desc, pack_icon)
            filename   = f"{pack_name}_Java.zip"
        else:
            pack_bytes = build_bedrock_pack(tex_files, snd_files, pack_name, pack_desc, pack_icon)
            filename   = f"{pack_name}_Bedrock.mcpack"
    except Exception as e:
        await cq.message.edit_text(
            f"❌ <b>Ошибка сборки пака:</b> <code>{e}</code>\n\nПопробуй ещё раз.",
            reply_markup=main_menu_kb(), parse_mode="HTML"
        )
        return

    increment_packs(uid)
    await state.clear()

    total = len(tex_files) + len(snd_files)
    if version == "java":
        install_text = (
            "1. Помести <code>{}.zip</code> в папку <code>resourcepacks</code>\n"
            "2. Зайди в игру → Настройки → Пакеты ресурсов → активируй пак"
        ).format(pack_name)
    else:
        install_text = (
            "1. Переименуй файл в <code>{}.mcpack</code>\n"
            "2. Открой файл — Bedrock установит автоматически"
        ).format(pack_name)

    icon_note = " ✅" if pack_icon else " ➖"
    await bot.send_document(
        chat_id=uid,
        document=BufferedInputFile(pack_bytes, filename=filename),
        caption=(
            f"✅ <b>Ресурс-пак готов!</b>\n\n"
            f"📛 Название: <b>{pack_name}</b>\n"
            f"📝 Описание: <i>{pack_desc}</i>\n"
            f"📦 Версия: {'☕ Java Edition' if version=='java' else '📱 Bedrock Edition'}\n"
            f"🖼 Текстур: <b>{len(tex_files)}</b>\n"
            f"🔊 Звуков: <b>{len(snd_files)}</b>\n"
            f"🖼 Иконка пака:{icon_note}\n\n"
            f"📥 <b>Как установить:</b>\n{install_text}"
        ),
        parse_mode="HTML",
        reply_markup=main_menu_kb()
    )

# ─── EDIT PACK ─────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "edit_pack")
async def cb_edit_pack(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    if not is_subscribed(uid):
        await cq.message.edit_text(
            "🔒 <b>Редактирование пака — только для подписчиков</b>\n\n"
            "Купи подписку, чтобы загружать готовые паки и дополнять их!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_sub")],
                [InlineKeyboardButton(text="⬅️ Назад",           callback_data="main_menu")],
            ]),
            parse_mode="HTML"
        )
        return
    await state.clear()
    await state.set_state(EditStates.upload_pack)
    await cq.message.edit_text(
        "✏️ <b>Редактирование существующего пака</b>\n\n"
        "Отправь свой ресурс-пак файлом:\n"
        "• <b>Java Edition</b> — <code>.zip</code>\n"
        "• <b>Bedrock Edition</b> — <code>.mcpack</code>\n\n"
        "Я распакую его, ты добавишь новые текстуры или звуки, и получишь обновлённый файл!",
        reply_markup=back_kb("main_menu"), parse_mode="HTML"
    )

@dp.message(EditStates.upload_pack, F.document)
async def edit_upload_pack(message: Message, state: FSMContext):
    doc = message.document
    fname = (doc.file_name or "").lower()

    if not (fname.endswith(".zip") or fname.endswith(".mcpack")):
        await message.answer(
            "❌ Нужен файл <b>.zip</b> (Java) или <b>.mcpack</b> (Bedrock)\n\n"
            "Отправь правильный файл:",
            reply_markup=back_kb("main_menu"), parse_mode="HTML"
        )
        return

    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await message.answer(
            "❌ Файл слишком большой (максимум 20 МБ)\n\nОтправь другой файл:",
            reply_markup=back_kb("main_menu"), parse_mode="HTML"
        )
        return

    await message.answer("⏳ <b>Читаю пак...</b>", parse_mode="HTML")

    file = await bot.get_file(doc.file_id)
    buf  = io.BytesIO()
    await bot.download_file(file.file_path, buf)
    pack_bytes = buf.getvalue()

    # Определяем версию по расширению
    version = "bedrock" if fname.endswith(".mcpack") else "java"

    # Считываем существующие файлы из архива
    try:
        existing_files: dict[str, bytes] = {}
        with zipfile.ZipFile(io.BytesIO(pack_bytes), "r") as zf:
            for name in zf.namelist():
                existing_files[name] = zf.read(name)
    except Exception as e:
        await message.answer(
            f"❌ Не удалось прочитать архив: <code>{e}</code>\n\nПопробуй другой файл.",
            reply_markup=back_kb("main_menu"), parse_mode="HTML"
        )
        return

    # Извлекаем имя и описание из метаданных пака
    pack_name = fname.replace(".zip", "").replace(".mcpack", "")
    pack_desc = "Custom Pack by PackCraftBot"

    if version == "java":
        meta_raw = existing_files.get("pack.mcmeta")
        if meta_raw:
            try:
                meta = json.loads(meta_raw)
                desc_raw = meta.get("pack", {}).get("description", "")
                # Убираем форматирование §
                import re
                clean = re.sub(r"§.", "", str(desc_raw))
                parts = clean.split("—")
                if len(parts) >= 2:
                    pack_name = parts[0].strip() or pack_name
                    pack_desc = parts[1].strip() or pack_desc
            except Exception:
                pass
    else:
        manifest_raw = existing_files.get("manifest.json")
        if manifest_raw:
            try:
                manifest = json.loads(manifest_raw)
                header = manifest.get("header", {})
                pack_name = header.get("name", pack_name)
                pack_desc = header.get("description", pack_desc)
            except Exception:
                pass

    await state.update_data(
        version=version,
        existing_files=existing_files,
        texture_files={},
        sound_files={},
        pack_name=pack_name,
        pack_desc=pack_desc,
        pack_icon=None,
    )
    await state.set_state(EditStates.choose_action)

    file_count = len(existing_files)
    ver_label  = "☕ Java Edition" if version == "java" else "📱 Bedrock Edition"
    await message.answer(
        f"✅ <b>Пак загружен!</b>\n\n"
        f"📛 Название: <b>{pack_name}</b>\n"
        f"📦 Версия: <b>{ver_label}</b>\n"
        f"📄 Файлов в паке: <b>{file_count}</b>\n\n"
        "Теперь добавь новые текстуры или звуки:",
        reply_markup=edit_pack_content_kb(), parse_mode="HTML"
    )

@dp.callback_query(EditStates.choose_action, F.data == "edit_back_content")
async def cb_edit_back_content(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tex_count = len(data.get("texture_files", {}))
    snd_count = len(data.get("sound_files", {}))
    summary = f"\n\n📊 Добавлено: 🖼 {tex_count} текстур, 🔊 {snd_count} звуков" if (tex_count or snd_count) else ""
    await state.set_state(EditStates.choose_action)
    await cq.message.edit_text(
        f"✏️ <b>Что добавить в пак?</b>{summary}",
        reply_markup=edit_pack_content_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "edit_add_texture")
async def cb_edit_add_texture(cq: CallbackQuery, state: FSMContext):
    await state.update_data(edit_current_mode="texture")
    await state.set_state(EditStates.choose_category)
    await cq.message.edit_text(
        "📂 <b>Выбери категорию текстуры:</b>",
        reply_markup=edit_category_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "edit_add_sound")
async def cb_edit_add_sound(cq: CallbackQuery, state: FSMContext):
    await state.update_data(edit_current_mode="sound")
    await state.set_state(EditStates.choose_category)
    await cq.message.edit_text(
        "🔊 <b>Выбери звук для замены:</b>",
        reply_markup=edit_sound_category_kb(), parse_mode="HTML"
    )

@dp.callback_query(EditStates.choose_category, F.data.startswith("ecat_"))
async def cb_edit_category(cq: CallbackQuery, state: FSMContext):
    cat_key = cq.data[5:]
    if cat_key not in CATEGORIES:
        await cq.answer("Неизвестная категория", show_alert=True)
        return
    _, items = CATEGORIES[cat_key]
    await state.update_data(edit_category=cat_key)
    await state.set_state(EditStates.choose_item)
    await cq.message.edit_text(
        "🔍 <b>Выбери что заменить:</b>",
        reply_markup=edit_items_kb(items), parse_mode="HTML"
    )

@dp.callback_query(EditStates.choose_category, F.data.startswith("esnd_"))
async def cb_edit_sound_item(cq: CallbackQuery, state: FSMContext):
    snd_key = cq.data[5:]
    if snd_key not in SOUNDS:
        await cq.answer("Неизвестный звук", show_alert=True)
        return
    await state.update_data(edit_current_item=snd_key, edit_current_mode="sound")
    await state.set_state(EditStates.upload_file)
    label = SOUND_LABELS.get(snd_key, snd_key)
    await cq.message.edit_text(
        f"🔊 <b>{label}</b>\n\nОтправь файл звука в формате <b>.ogg</b>\n\n"
        "💡 Конвертировать mp3→ogg можно на сайте "
        "<a href='https://audio.online-convert.com/ru/convert-to-ogg'>online-convert.com</a>",
        reply_markup=back_kb("edit_back_sounds"), parse_mode="HTML", disable_web_page_preview=True
    )

@dp.callback_query(EditStates.choose_category, F.data == "edit_back_categories")
@dp.callback_query(EditStates.choose_item, F.data == "edit_back_categories")
async def cb_edit_back_categories(cq: CallbackQuery, state: FSMContext):
    await state.set_state(EditStates.choose_category)
    await cq.message.edit_text(
        "📂 <b>Выбери категорию текстуры:</b>",
        reply_markup=edit_category_kb(), parse_mode="HTML"
    )

@dp.callback_query(F.data == "edit_back_sounds")
async def cb_edit_back_sounds(cq: CallbackQuery, state: FSMContext):
    await state.set_state(EditStates.choose_category)
    await cq.message.edit_text(
        "🔊 <b>Выбери звук для замены:</b>",
        reply_markup=edit_sound_category_kb(), parse_mode="HTML"
    )

@dp.callback_query(EditStates.choose_item, F.data.startswith("eitem_"))
async def cb_edit_item(cq: CallbackQuery, state: FSMContext):
    item_key = cq.data[6:]
    await state.update_data(edit_current_item=item_key, edit_current_mode="texture")
    await state.set_state(EditStates.upload_file)
    label = ITEM_LABELS.get(item_key, item_key)
    data = await state.get_data()
    cat_key = data.get("edit_category", "blocks")
    await cq.message.edit_text(
        f"🖼 <b>{label}</b>\n\nОтправь текстуру в формате <b>PNG</b>\n"
        "Поддерживаемые размеры: 16×16, 32×32, 64×64, 128×128",
        reply_markup=back_kb(f"edit_back_items_{cat_key}"), parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("edit_back_items_"))
async def cb_edit_back_items(cq: CallbackQuery, state: FSMContext):
    cat_key = cq.data.replace("edit_back_items_", "")
    if cat_key not in CATEGORIES:
        await cb_edit_back_categories(cq, state)
        return
    _, items = CATEGORIES[cat_key]
    await state.set_state(EditStates.choose_item)
    await cq.message.edit_text(
        "🔍 <b>Выбери что заменить:</b>",
        reply_markup=edit_items_kb(items), parse_mode="HTML"
    )

@dp.message(EditStates.upload_file, F.photo | F.document | F.audio)
async def edit_upload_file(message: Message, state: FSMContext):
    data             = await state.get_data()
    current_mode     = data.get("edit_current_mode", "texture")
    item_key         = data.get("edit_current_item")
    tex_files        = data.get("texture_files", {})
    snd_files        = data.get("sound_files", {})

    if current_mode == "sound":
        file_id   = None
        file_name = ""
        if message.document:
            file_id   = message.document.file_id
            file_name = message.document.file_name or ""
        elif message.audio:
            file_id   = message.audio.file_id
            file_name = message.audio.file_name or message.audio.title or "sound.ogg"
        else:
            await message.answer(
                "❌ Отправь <b>.ogg файл</b> как документ\n\n"
                "💡 Конвертировать можно на audio.online-convert.com",
                parse_mode="HTML"
            )
            return
        if not file_name.lower().endswith(".ogg"):
            await message.answer(
                "❌ Нужен файл в формате <b>.ogg</b>\n\n"
                "💡 Конвертировать mp3/wav → ogg: audio.online-convert.com",
                parse_mode="HTML"
            )
            return
        file = await bot.get_file(file_id)
        buf  = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        snd_files[item_key] = buf.getvalue()
        await state.update_data(sound_files=snd_files)
        label = SOUND_LABELS.get(item_key, item_key)
        await message.answer(
            f"✅ <b>{label}</b> добавлен!\n\nЧто ещё добавим?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🖼 Ещё текстуру",  callback_data="edit_add_texture"),
                 InlineKeyboardButton(text="🔊 Ещё звук",      callback_data="edit_add_sound")],
                [InlineKeyboardButton(text="📦 Скачать пак",   callback_data="edit_finish_pack")],
            ]),
            parse_mode="HTML"
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
            f"✅ <b>{label}</b> добавлена!\n\nЧто ещё добавим?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🖼 Ещё текстуру",  callback_data="edit_add_texture"),
                 InlineKeyboardButton(text="🔊 Ещё звук",      callback_data="edit_add_sound")],
                [InlineKeyboardButton(text="📦 Скачать пак",   callback_data="edit_finish_pack")],
            ]),
            parse_mode="HTML"
        )
    await state.set_state(EditStates.choose_action)

@dp.callback_query(F.data == "edit_finish_pack")
async def cb_edit_finish_pack(cq: CallbackQuery, state: FSMContext):
    data           = await state.get_data()
    version        = data.get("version", "java")
    existing_files = data.get("existing_files", {})
    tex_files      = data.get("texture_files", {})
    snd_files      = data.get("sound_files", {})
    pack_name      = data.get("pack_name", "EditedPack")
    pack_desc      = data.get("pack_desc", "Custom Pack by PackCraftBot")
    pack_icon      = data.get("pack_icon", None)
    uid            = cq.from_user.id

    if not tex_files and not snd_files:
        await cq.answer("⚠️ Добавь хотя бы одну текстуру или звук!", show_alert=True)
        return

    await cq.message.edit_text("⏳ <b>Собираю обновлённый пак...</b>", parse_mode="HTML")

    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Сначала копируем все существующие файлы
            for fname, fdata in existing_files.items():
                zf.writestr(fname, fdata)

            if version == "java":
                # Перезаписываем / добавляем новые текстуры
                for key, img_data in tex_files.items():
                    path = JAVA_PATHS.get(key)
                    if path:
                        zf.writestr(path, img_data)
                # Новые звуки
                if snd_files:
                    # Считываем существующий sounds.json, если есть
                    existing_sounds_raw = existing_files.get("assets/minecraft/sounds.json")
                    existing_sounds = {}
                    if existing_sounds_raw:
                        try:
                            existing_sounds = json.loads(existing_sounds_raw)
                        except Exception:
                            pass
                    for key, snd_data in snd_files.items():
                        path = SOUNDS[key][0]
                        zf.writestr(path, snd_data)
                        # Добавляем событие
                        event = SOUNDS[key][2]
                        rel   = path.replace("assets/minecraft/sounds/", "").replace(".ogg", "")
                        existing_sounds[event] = {
                            "sounds": [{"name": rel, "stream": False}],
                            "replace": True,
                        }
                    zf.writestr(
                        "assets/minecraft/sounds.json",
                        json.dumps(existing_sounds, indent=2, ensure_ascii=False)
                    )
            else:
                # Bedrock
                for key, img_data in tex_files.items():
                    path = BEDROCK_PATHS.get(key)
                    if path:
                        zf.writestr(path, img_data)
                if snd_files:
                    existing_snd_raw = existing_files.get("sounds/sound_definitions.json")
                    existing_snd = {"format_version": "1.14.0", "sound_definitions": {}}
                    if existing_snd_raw:
                        try:
                            existing_snd = json.loads(existing_snd_raw)
                        except Exception:
                            pass
                    for key, snd_data in snd_files.items():
                        path  = SOUNDS[key][1]
                        event = SOUNDS[key][3]
                        bed_path = path.replace(".ogg", "")
                        zf.writestr(path, snd_data)
                        existing_snd["sound_definitions"][event] = {
                            "category": "neutral",
                            "sounds":   [{"name": bed_path}],
                        }
                    zf.writestr(
                        "sounds/sound_definitions.json",
                        json.dumps(existing_snd, indent=2, ensure_ascii=False)
                    )

        pack_bytes = buf.getvalue()
        filename   = f"{pack_name}_edited.zip" if version == "java" else f"{pack_name}_edited.mcpack"
    except Exception as e:
        await cq.message.edit_text(
            f"❌ <b>Ошибка сборки пака:</b> <code>{e}</code>\n\nПопробуй ещё раз.",
            reply_markup=main_menu_kb(), parse_mode="HTML"
        )
        return

    await state.clear()
    total = len(tex_files) + len(snd_files)
    await bot.send_document(
        chat_id=uid,
        document=BufferedInputFile(pack_bytes, filename=filename),
        caption=(
            f"✅ <b>Пак обновлён!</b>\n\n"
            f"📛 Название: <b>{pack_name}</b>\n"
            f"📦 Версия: {'☕ Java Edition' if version=='java' else '📱 Bedrock Edition'}\n"
            f"🖼 Добавлено текстур: <b>{len(tex_files)}</b>\n"
            f"🔊 Добавлено звуков: <b>{len(snd_files)}</b>\n\n"
            f"Все оригинальные файлы пака сохранены!"
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

# ─── FALLBACK ──────────────────────────────────────────────────────────────────
@dp.message()
async def fallback_message(message: Message, state: FSMContext):
    """Обрабатывает любые неожиданные сообщения вне FSM-состояний."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(
            "👋 Используй меню ниже или введи /start",
            reply_markup=main_menu_kb()
        )

# ─── MAIN ──────────────────────────────────────────────────────────────────────
async def main():
    init_firebase()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
