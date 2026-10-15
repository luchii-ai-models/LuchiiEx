import logging
import os

import httpx

log = logging.getLogger("alerts")


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"


async def send(chat_id, text: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(_url("sendMessage"), json={"chat_id": chat_id, "text": text})
    return r.json()


async def latest_chat():
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(_url("getUpdates"))
    for u in reversed(r.json().get("result", [])):
        chat = (u.get("message") or u.get("edited_message") or {}).get("chat")
        if chat:
            return chat
    return None


async def notify(bot: dict, text: str):
    if not bot or not bot.get("alerts_enabled") or not bot.get("telegram_chat_id"):
        return
    try:
        await send(bot["telegram_chat_id"], text)
    except Exception as e:
        log.warning("telegram notify failed: %s", e)
