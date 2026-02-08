from app.services.pack_service import generate_short_name


def test_generate_short_name_suffix_and_len() -> None:
    name = generate_short_name(
        title="Мой супер длинный стикерпак с символами !!!",
        tg_user_id=123,
        bot_username="mycoolbot",
        salt="x",
    )
    assert name.endswith("_by_mycoolbot")
    assert len(name) <= 64
    assert all(ch.isalnum() or ch == "_" for ch in name)
