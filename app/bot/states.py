from aiogram.fsm.state import State, StatesGroup


class PackStates(StatesGroup):
    waiting_for_pack_title = State()


class InviteStates(StatesGroup):
    waiting_for_invite_username = State()


class EmojiStates(StatesGroup):
    waiting_for_custom_emoji = State()
