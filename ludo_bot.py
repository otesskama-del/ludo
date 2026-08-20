import asyncio
import os
import random
from typing import Any

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)


TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Добавьте новый токен бота в переменные окружения."
    )


bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# Открытые игры: game_id -> данные игры.
games: dict[str, dict[str, Any]] = {}


def decode_slot(value: int) -> tuple[int, int, int]:
    """
    Telegram отдаёт для 🎰 значение от 1 до 64.
    Представляем его как три цифры от 1 до 4.
    Цифра 4 используется как "7", поэтому 4,4,4 = 777.
    """
    number = max(0, min(value - 1, 63))
    first = number // 16 + 1
    second = number // 4 % 4 + 1
    third = number % 4 + 1
    return first, second, third


def slot_text(value: int) -> str:
    reels = decode_slot(value)
    return "".join("7" if reel == 4 else str(reel) for reel in reels)


def is_777(value: int) -> bool:
    return decode_slot(value) == (4, 4, 4)


def is_77(value: int) -> bool:
    reels = decode_slot(value)
    return reels.count(4) == 2


def game_keyboard(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎲",
                    # В game_id есть двоеточие, поэтому используем "|"
                    # как разделитель callback-параметров.
                    callback_data=f"open|{game_id}|{row * 5 + col}",
                )
                for col in range(5)
            ]
            for row in range(5)
        ]
    )


def result_grid(blocks: list[str], opened: int) -> str:
    symbols = {"NFT": "💎", "ROSE": "🌹", "MISS": "💨"}
    lines = []
    for row in range(5):
        cells = []
        for col in range(5):
            index = row * 5 + col
            cells.append(symbols[blocks[index]] if index == opened else "❓")
        lines.append(" ".join(cells))
    return "\n".join(lines)


@router.my_chat_member()
async def added_to_chat(event: types.ChatMemberUpdated) -> None:
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    was_not_member = old_status in {"left", "kicked"}
    is_member = new_status in {"member", "administrator"}
    if was_not_member and is_member:
        await event.answer(
            "🎰 Привет! Я слотовый игровой бот.\n\n"
            "Отправьте в этот чат анимационный кубик 🎰.\n"
            "Если выпадет 777 — появится игровое поле с NFT и розами.\n"
            "Если выпадет 77 — я сообщу, что победа была близко."
        )


@router.message(CommandStart())
async def start(message: types.Message) -> None:
    await message.answer(
        "🎰 Отправь анимационный кубик со значком 🎰.\n\n"
        "777 — игра с NFT и розами.\n"
        "77 — почти победа.\n"
        "Другие комбинации — промах."
    )


@router.message(F.dice)
async def handle_slot(message: types.Message) -> None:
    # Пересланные 🎰 не считаются новым броском.
    # Особенно важно для 777, сохранённого в «Избранном»:
    # такое сообщение нельзя удалять и нельзя выдавать за выигрыш.
    if getattr(message, "forward_origin", None) is not None:
        return

    dice = message.dice

    # Игнорируем обычные кубики, дартс, баскетбол и другие анимации.
    if dice.emoji != "🎰":
        return

    value = dice.value
    combination = slot_text(value)
    game_id = f"{message.chat.id}:{message.message_id}"

    if is_777(value):
        blocks = ["NFT"] + ["ROSE"] * 12 + ["MISS"] * 12
        random.shuffle(blocks)
        games[game_id] = {
            "blocks": blocks,
            "chat_id": message.chat.id,
            "owner_id": message.from_user.id if message.from_user else 0,
        }
        await message.reply(
            "🎉🎉🎉 ВЫПАЛО 777!\n"
            "@repst, зафиксирована выигрышная комбинация!\n\n"
            "🎲 Выбери один кубик. Внутри спрятаны:\n"
            "💎 1 NFT\n"
            "🌹 12 роз\n"
            "💨 12 промахов",
            reply_markup=game_keyboard(game_id),
            protect_content=True,
        )

        # Удаляем исходный выигрышный 🎰, чтобы его нельзя было
        # переслать после обработки. Бот должен быть администратором.
        try:
            await message.delete()
        except Exception:
            # Если у бота нет права удаления, игра всё равно продолжается.
            pass
    elif is_77(value):
        await message.reply(
            f"🔥 Выпало {combination} — близко к победе!\n"
            "Повезёт в следующий раз 🎰"
        )
    else:
        await message.reply(
            f"💨 Выпало {combination} — промах.\n"
            "Попробуй ещё раз!"
        )


@router.callback_query(F.data.startswith("open|"))
async def open_cell(callback: CallbackQuery) -> None:
    try:
        # Формат: open|chat_id:message_id|cell_index
        _, game_id, index_text = callback.data.split("|", 2)
        index = int(index_text)
    except (AttributeError, ValueError):
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return

    game = games.get(game_id)
    if game is None:
        await callback.answer("Эта игра уже завершена.", show_alert=True)
        return

    if callback.message.chat.id != game["chat_id"]:
        await callback.answer("Игра находится в другом чате.", show_alert=True)
        return

    if not 0 <= index < 25:
        await callback.answer("Некорректная клетка.", show_alert=True)
        return

    blocks = game["blocks"]
    selected = blocks[index]

    if selected == "NFT":
        result = "💎 ДЖЕКПОТ! Выпал NFT!"
    elif selected == "ROSE":
        result = "🌹 Ты нашёл розу!"
    else:
        result = "💨 Пусто. Повезёт в следующий раз!"

    await callback.message.edit_text(
        f"🎯 Выбрана клетка №{index + 1}\n"
        f"{result}\n\n"
        f"{result_grid(blocks, index)}\n\n"
        "📊 Осталось на поле:\n"
        f"💎 NFT: {blocks.count('NFT')}\n"
        f"🌹 Роз: {blocks.count('ROSE')}\n"
        f"💨 Пустых: {blocks.count('MISS')}"
    )
    await callback.answer()
    games.pop(game_id, None)


async def main() -> None:
    print("🚀 Бот запущен и ожидает анимационные кубики 🎰")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())