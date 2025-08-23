# main.py
from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import suppress
from typing import Any

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, Update
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

# ---- Config ----
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "http://localhost:8000")  # mock_api base
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # e.g. https://your-render-service.onrender.com
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # optional secret header
USE_WEBHOOK = bool(WEBHOOK_HOST)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("bot")


# ---- HTTP client ----
http = httpx.AsyncClient(base_url=API_BASE, timeout=10.0)


# ---- State (very simple in-memory for demo) ----
user_mode: dict[int, str] = {}  # user_id -> ecom|realty|clinic


# ---- Helpers ----
async def get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    r = await http.get(url, params=params)
    r.raise_for_status()
    return r.json()


async def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    r = await http.post(url, json=payload)
    r.raise_for_status()
    return r.json()


# ---- Router ----
router = Router()


@router.message(CommandStart())
async def on_start(m: Message):
    user_mode[m.from_user.id] = "ecom"
    await m.answer(
        "Демо-бот запущен.\n"
        "Доступные режимы: ecom | realty | clinic\n\n"
        "Напишите один из режимов, чтобы переключиться.",
    )


@router.message(F.text.lower().in_({"ecom", "realty", "clinic"}))
async def switch_mode(m: Message):
    mode = m.text.lower()
    user_mode[m.from_user.id] = mode
    await m.answer(f"Режим переключен на: {mode}. Спросите что-нибудь по сценарию.")


@router.message(F.text)
async def handle_text(m: Message):
    mode = user_mode.get(m.from_user.id, "ecom")
    text = (m.text or "").lower()

    # ---- E-COM scenarios ----
    if mode == "ecom":
        if "где мой заказ" in text or "заказ #" in text:
            digits = "".join(ch for ch in text if ch.isdigit()) or "1234"
            data = await get_json("/mock/ecom/order", {"order_id": digits})
            await m.answer(f"Заказ #{data['order_id']}: {data['status']}, ETA {data['eta']}")
            return

        if "вернуть" in text or "возврат" in text:
            res = await post_json(
                "/mock/ecom/return",
                {"order_id": "1234", "item_sku": "HOO-XL", "reason": "size", "condition": "new"},
            )
            await m.answer(f"Создан возврат: {res['rma']} — этикетка: {res['label_url']}")
            rel = await get_json("/mock/ecom/related", {"sku": "HOO-XL", "limit": 3})
            upsell = ", ".join(x["name"] for x in rel)
            await m.answer(f"Рекомендую добавить к заказу: {upsell}. Нужна помощь?")
            return

    # ---- REALTY scenarios ----
    if mode == "realty":
        if "квартира" in text or "2-к" in text or "2к" in text:
            lst = await get_json(
                "/mock/realty/search",
                {"budget_max": 15_000_000, "district": "ЮЗАО", "rooms": 2, "mortgage": True},
            )
            preview = "\n".join(f"{x['id']}: {x['price']:,} ₽ — {x['address']}".replace(",", " ") for x in lst)
            await m.answer(f"Подходящие варианты:\n{preview}\n\nБронируем APT-202 завтра 19:00?")
            return

        if "бронь" in text or "заброни" in text:
            res = await post_json(
                "/mock/realty/book",
                {"listing_id": "APT-202", "datetime": "2025-08-21T19:00", "name": "Илья", "phone": "+7..."},
            )
            if res.get("status") == "booked":
                await m.answer("Бронь подтверждена. Приглашение отправлено.")
            else:
                await m.answer("Не удалось забронировать, попробуйте позже.")
            return

    # ---- CLINIC scenarios ----
    if mode == "clinic":
        if "болит горло" in text or "температура" in text:
            slots = await get_json("/mock/clinic/slots", {"speciality": "лор", "date_from": "2025-08-20"})
            await m.answer(
                "Это не медицинская консультация. Рекомендую очный приём у ЛОР.\n"
                "Доступные слоты: " + ", ".join(slots)
            )
            return

        if "на 18:00" in text or "18:00" in text:
            res = await post_json(
                "/mock/clinic/book",
                {"speciality": "лор", "datetime": "2025-08-20T18:00", "name": "Олег", "phone": "+7..."},
            )
            if res.get("status") == "booked":
                await m.answer("Запись подтверждена. Номер талона CLN-5521.")
            else:
                await m.answer("Не удалось записать, попробуйте позже.")
            return

    # ---- Fallback ----
    await m.answer("Я понял вас. Для демо попробуйте сценарии из выбранного режима.")


# ---- App bootstrap (webhook or polling) ----
async def on_startup(bot: Bot):
    if USE_WEBHOOK:
        url = f"{WEBHOOK_HOST.rstrip('/')}{WEBHOOK_PATH}"
        await bot.set_webhook(url, secret_token=WEBHOOK_SECRET or None)
        log.info("Webhook set to %s", url)
    else:
        log.info("Running with long polling (no WEBHOOK_HOST provided)")


async def on_shutdown(bot: Bot):
    with suppress(Exception):
        await http.aclose()
    if USE_WEBHOOK:
        with suppress(Exception):
            await bot.delete_webhook()
    log.info("Shutdown complete")


def build_aiohttp_app(dp: Dispatcher) -> web.Application:
    app = web.Application()

    async def handle(request: web.Request) -> web.Response:
        if WEBHOOK_SECRET:
            if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
                return web.Response(status=403, text="forbidden")
        data = await request.json()
        update = Update.model_validate(data)
        await dp.feed_update(bot, update)
        return web.Response(text="ok")

    app.router.add_get("/healthz", lambda _: web.json_response({"ok": True}))
    app.router.add_post(WEBHOOK_PATH, handle)
    return app


bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
dp.include_router(router)


async def main():
    await on_startup(bot)
    if USE_WEBHOOK:
        app = build_aiohttp_app(dp)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
        await site.start()
        log.info("Webhook server started on 0.0.0.0:%s", os.getenv("PORT", "8000"))
        # Keep alive
        while True:
            await asyncio.sleep(3600)
    else:
        try:
            await dp.start_polling(bot)
        finally:
            await on_shutdown(bot)


if __name__ == "__main__":
    asyncio.run(main())