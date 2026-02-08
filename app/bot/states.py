from aiogram.fsm.state import State, StatesGroup


class PackStates(StatesGroup):
    waiting_for_pack_title = State()


class InviteStates(StatesGroup):
    waiting_for_invite_username = State()
