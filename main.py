# main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, Update
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

# -----------------------------
# Config
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
WEBHOOK_HOST = (os.getenv("WEBHOOK_HOST") or "").rstrip("/")  # e.g. https://your-service.onrender.com
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
USE_WEBHOOK = bool(WEBHOOK_HOST)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("bot")


# -----------------------------
# HTTP client wrapper
# -----------------------------
class MockAPIClient:
    """Лёгкая обёртка вокруг httpx с ретраями и логированием."""

    def __init__(self, base_url: str):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> Any:
        """Выполнить запрос с 3 ретраями при сетевых сбоях или 5xx."""
        backoff = 0.5
        last_exc: Exception | None = None

        for attempt in range(3):
            try:
                resp = await self._client.request(method, url, **kw)
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ReadTimeout) as e:
                last_exc = e
                if attempt < 2:
                    log.warning("HTTP transient error: %s (attempt %s)", e, attempt + 1)
                else:
                    break
            except httpx.HTTPStatusError as e:
                last_exc = e
                if 500 <= e.response.status_code < 600 and attempt < 2:
                    log.warning(
                        "HTTP %s on %s, retrying (attempt %s)",
                        e.response.status_code, url, attempt + 1,
                    )
                else:
                    break

            await asyncio.sleep(backoff)
            backoff *= 2

        assert last_exc is not None
        raise last_exc

    async def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", url, params=params)

    async def post(self, url: str, payload: dict[str, Any]) -> Any:
        return await self._request("POST", url, json=payload)


API: MockAPIClient | None = None  # инициализируем в on_startup()

# -----------------------------
# State
# -----------------------------
user_mode: dict[int, str] = {}  # user_id -> ecom|realty|clinic

# -----------------------------
# Router
# -----------------------------
router = Router()


@router.message(CommandStart())
async def on_start(m: Message) -> None:
    if not m.from_user:
        return
    user_mode[m.from_user.id] = "ecom"
    await m.answer(
        "Демо-бот запущен.\n"
        "Доступные режимы: ecom | realty | clinic\n\n"
        "Напишите один из режимов, чтобы переключиться.",
    )


@router.message(F.text.lower().in_({"ecom", "realty", "clinic"}))
async def switch_mode(m: Message) -> None:
    if not m.from_user or not m.text:
        return
    mode = m.text.lower()
    user_mode[m.from_user.id] = mode
    await m.answer(f"Режим переключен на: {mode}. Спросите что-нибудь по сценарию.")


@router.message(F.text)
async def handle_text(m: Message) -> None:
    if not m.from_user:
        return
    assert API is not None, "API client not initialized"

    mode = user_mode.get(m.from_user.id, "ecom")
    text = (m.text or "").lower()

    try:
        # ---- E-COM
        if mode == "ecom":
            if "где мой заказ" in text or "заказ #" in text:
                digits = "".join(ch for ch in text if ch.isdigit()) or "1234"
                data = await API.get("/mock/ecom/order", {"order_id": digits})
                await m.answer(f"Заказ #{data.get('order_id')}: {data.get('status')}, ETA {data.get('eta')}")
                return

            if "вернуть" in text or "возврат" in text:
                res = await API.post(
                    "/mock/ecom/return",
                    {"order_id": "1234", "item_sku": "HOO-XL", "reason": "size", "condition": "new"},
                )
                await m.answer(f"Создан возврат: {res.get('rma')} — этикетка: {res.get('label_url')}")
                rel = await API.get("/mock/ecom/related", {"sku": "HOO-XL", "limit": 3})
                upsell = ", ".join(x.get("name", "") for x in rel if isinstance(x, dict))
                if upsell:
                    await m.answer(f"Рекомендую добавить к заказу: {upsell}. Нужна помощь?")
                return

        # ---- REALTY
        if mode == "realty":
            if any(k in text for k in ("квартира", "2-к", "2к")):
                lst = await API.get(
                    "/mock/realty/search",
                    {"budget_max": 15_000_000, "district": "ЮЗАО", "rooms": 2, "mortgage": True},
                )
                preview = "\n".join(
                    f"{x.get('id')}: {int(x.get('price', 0)):,} ₽ — {x.get('address','')}".replace(",", " ")
                    for x in lst
                )
                await m.answer(f"Подходящие варианты:\n{preview}\n\nБронируем APT-202 завтра 19:00?")
                return

            if "бронь" in text or "заброни" in text:
                res = await API.post(
                    "/mock/realty/book",
                    {"listing_id": "APT-202", "datetime": "2025-08-21T19:00", "name": "Илья", "phone": "+7..."},
                )
                await m.answer(
                    "Бронь подтверждена. Приглашение отправлено."
                    if res.get("status") == "booked"
                    else "Не удалось забронировать, попробуйте позже."
                )
                return

        # ---- CLINIC
        if mode == "clinic":
            if "болит горло" in text or "температура" in text:
                data = await API.get("/mock/clinic/slots", {"speciality": "лор", "date_from": "2025-08-20"})
                slots = data if isinstance(data, list) else data.get("slots", [])
                human = ", ".join(slots) if slots else "слотов нет"
                await m.answer(
                    "Это не медицинская консультация. Рекомендую очный приём у ЛОР.\n"
                    f"Доступные слоты: {human}"
                )
                return

            if "на 18:00" in text or "18:00" in text:
                res = await API.post(
                    "/mock/clinic/book",
                    {"speciality": "лор", "datetime": "2025-08-20T18:00", "name": "Олег", "phone": "+7..."},
                )
                await m.answer(
                    "Запись подтверждена. Номер талона CLN-5521."
                    if res.get("status") == "booked"
                    else "Не удалось записать, попробуйте позже."
                )
                return

        # ---- fallback
        await m.answer("Для демо попробуйте сценарии из выбранного режима.")
    except httpx.HTTPError as e:
        log.exception("API error: %s", e)
        await m.answer("Сервис временно недоступен, попробуйте позже.")
    except Exception as e:
        log.exception("Unexpected error: %s", e)
        await m.answer("Что-то пошло не так. Попробуйте ещё раз чуть позже.")


# -----------------------------
# Webhook lifecycle
# -----------------------------
BOT = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
DP = Dispatcher()
DP.include_router(router)


async def on_startup() -> None:
    global API
    API = MockAPIClient(API_BASE)

    if USE_WEBHOOK:
        url = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
        await BOT.set_webhook(url, secret_token=(WEBHOOK_SECRET or None))
        log.info("Webhook set to %s", url)
    else:
        log.info("Running with long polling (no WEBHOOK_HOST provided)")


async def on_shutdown() -> None:
    if API is not None:
        with suppress(Exception):
            await API.aclose()
    if USE_WEBHOOK:
        with suppress(Exception):
            await BOT.delete_webhook()
    log.info("Shutdown complete")


def build_aiohttp_app() -> web.Application:
    app = web.Application()

    async def health(_req: web.Request) -> web.StreamResponse:
        return web.json_response({"ok": True})

    async def handle(request: web.Request) -> web.StreamResponse:
        if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            return web.Response(status=403, text="forbidden")
        data = await request.json()
        update = Update.model_validate(data)
        await DP.feed_update(BOT, update)
        return web.Response(text="ok")

    app.router.add_get("/healthz", health)
    app.router.add_post(WEBHOOK_PATH, handle)
    return app


# -----------------------------
# Entrypoint
# -----------------------------
async def main() -> None:
    await on_startup()
    if USE_WEBHOOK:
        runner = web.AppRunner(build_aiohttp_app())
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
        await site.start()
        log.info("Webhook server started on 0.0.0.0:%s", os.getenv("PORT", "8000"))
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            with suppress(Exception):
                await runner.cleanup()
            await on_shutdown()
    else:
        try:
            await DP.start_polling(BOT)
        finally:
            await on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())