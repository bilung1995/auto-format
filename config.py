import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()


def get_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")

    if not raw:
        return set()

    result = set()

    for value in raw.split(","):
        value = value.strip()

        if value.isdigit():
            result.add(int(value))

    return result


ADMIN_IDS = get_admin_ids()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN belum diisi di file .env"
    )
