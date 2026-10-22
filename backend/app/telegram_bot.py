import asyncio
import io
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.ai import get_extractor
from app.budgets import budget_status
from app.config import settings
from app.database import SessionLocal
from app.models import Category, Expense, Tag, TelegramLinkToken, TelegramUser, User
from app.notifications import budget_alerts, due_summaries
from app.routers.expenses import ALLOWED_MIMES, save_expense
from app.schemas import ExpenseInput
from app.security import hash_value
from app.services import parse_amount, parse_spesa_details
from app.storage import storage
from app.telegram_i18n import from_telegram, tr

logger = logging.getLogger(__name__)
bot = Bot(settings.telegram_bot_token) if settings.telegram_bot_token else None
dispatcher = Dispatcher()
NOTIFICATION_INTERVAL_SECONDS = 600


def lang_for(db: Session | None, message: Message) -> str:
    """Language of the linked user, falling back to the Telegram client language."""
    if db is None:
        with SessionLocal() as own:
            return lang_for(own, message)
    link = db.get(TelegramUser, message.from_user.id)
    return link.language if link else from_telegram(message.from_user.language_code)


class TelegramRateLimit(BaseMiddleware):
    def __init__(self):
        self.last_message: dict[int, float] = defaultdict(float)

    async def __call__(self, handler: Callable[..., Awaitable[Any]], event: object,
                       data: dict[str, Any]) -> Any:
        telegram_user = getattr(event, "from_user", None)
        if telegram_user:
            now = asyncio.get_running_loop().time()
            if now - self.last_message[telegram_user.id] < 1.5:
                if isinstance(event, Message):
                    await event.answer(tr(from_telegram(telegram_user.language_code), "wait"))
                return None
            self.last_message[telegram_user.id] = now
        return await handler(event, data)


dispatcher.message.middleware(TelegramRateLimit())


class GuidedExpense(StatesGroup):
    amount = State()
    details = State()
    spent_on = State()
    category = State()


def visible_expenses(user: User) -> ColumnElement[bool]:
    return ((Expense.visibility == "shared") | (Expense.created_by == user.id))


def linked_user(db: Session, telegram_id: int) -> User | None:
    link = db.get(TelegramUser, telegram_id)
    return db.get(User, link.user_id) if link else None


@dispatcher.message(Command("start"))
async def start(message: Message) -> None:
    lang = lang_for(None, message)
    if message.chat.type != "private":
        await message.answer(tr(lang, "private_only"))
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 1:
        await message.answer(tr(lang, "link_hint"))
        return
    with SessionLocal() as db:
        link = db.scalar(select(TelegramLinkToken).where(
            TelegramLinkToken.token_hash == hash_value(parts[1]),
            TelegramLinkToken.consumed.is_(False),
        ).with_for_update())
        expires_at = link.expires_at if link else None
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if not link or expires_at <= datetime.now(timezone.utc):
            await message.answer(tr(lang, "token_invalid"))
            return
        previous = db.get(TelegramUser, message.from_user.id)
        already_linked = db.scalar(select(TelegramUser).where(TelegramUser.user_id == link.user_id))
        if already_linked and already_linked.telegram_id != message.from_user.id:
            await message.answer(tr(lang, "already_linked"))
            return
        if previous:
            db.delete(previous)
            db.flush()
        lang = from_telegram(message.from_user.language_code)
        db.add(TelegramUser(telegram_id=message.from_user.id, user_id=link.user_id, language=lang))
        link.consumed = True
        db.commit()
    await message.answer(tr(lang, "linked"))


@dispatcher.message(Command("aiuto", "help"))
async def help_command(message: Message) -> None:
    await message.answer(tr(lang_for(None, message), "help"))


@dispatcher.message(Command("lingua", "lang"))
async def set_language(message: Message) -> None:
    parts = (message.text or "").split()
    with SessionLocal() as db:
        lang = lang_for(db, message)
        link = db.get(TelegramUser, message.from_user.id)
        if not link:
            await message.answer(tr(lang, "not_linked"))
            return
        if len(parts) != 2 or parts[1].lower() not in ("it", "en"):
            await message.answer(tr(lang, "lang_usage"))
            return
        link.language = parts[1].lower()
        db.commit()
        lang = link.language
    await message.answer(tr(lang, "lang_set"))


@dispatcher.message(Command("spesa", "expense"))
async def add_expense(message: Message, state: FSMContext) -> None:
    lang = lang_for(None, message)
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await state.set_state(GuidedExpense.amount)
        await message.answer(tr(lang, "amount_ask"))
        return
    try:
        amount = parse_amount(parts[1])
    except ValueError:
        await message.answer(tr(lang, "amount_bad"))
        return
    try:
        details, category_name, spent_on = parse_spesa_details(parts[2])
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await _store_expense(message, amount, details, spent_on, category_name)


@dispatcher.message(GuidedExpense.amount, F.text, ~F.text.startswith("/"))
async def guided_amount(message: Message, state: FSMContext) -> None:
    lang = lang_for(None, message)
    try:
        amount = parse_amount(message.text or "")
    except ValueError:
        await message.answer(tr(lang, "amount_bad_retry"))
        return
    await state.update_data(amount=str(amount))
    await state.set_state(GuidedExpense.details)
    await message.answer(tr(lang, "details_ask"))


@dispatcher.message(GuidedExpense.details, F.text, ~F.text.startswith("/"))
async def guided_details(message: Message, state: FSMContext) -> None:
    lang = lang_for(None, message)
    details = (message.text or "").strip()
    if not details or len(details) > 160:
        await message.answer(tr(lang, "details_bad"))
        return
    await state.update_data(details=details)
    await state.set_state(GuidedExpense.spent_on)
    await message.answer(tr(lang, "date_ask"))


@dispatcher.message(GuidedExpense.spent_on, F.text, ~F.text.startswith("/"))
async def guided_date(message: Message, state: FSMContext) -> None:
    lang = lang_for(None, message)
    raw = (message.text or "").strip()
    if raw == "-":
        spent_on = date.today()
    else:
        try:
            spent_on = date.fromisoformat(raw)
        except ValueError:
            await message.answer(tr(lang, "date_bad"))
            return
    await state.update_data(spent_on=spent_on.isoformat())
    await state.set_state(GuidedExpense.category)
    await message.answer(tr(lang, "category_ask"))


@dispatcher.message(GuidedExpense.category, F.text, ~F.text.startswith("/"))
async def guided_category(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    category_name = (message.text or "").strip()
    category_name = None if category_name == "-" else category_name
    if category_name and len(category_name) > 60:
        await message.answer(tr(lang_for(None, message), "category_long"))
        return
    saved = await _store_expense(
        message, Decimal(data["amount"]), data["details"],
        date.fromisoformat(data["spent_on"]), category_name,
    )
    if saved:
        await state.clear()


async def send_alerts(items: list[tuple[int, str]]) -> None:
    for telegram_id, text in items:
        try:
            await bot.send_message(telegram_id, text)
        except Exception as exc:
            logger.warning("Telegram notification failed",
                           extra={"error_type": type(exc).__name__})


async def _store_expense(message: Message, amount: Decimal, details: str,
                         spent_on: date, category_name: str | None) -> bool:
    with SessionLocal() as db:
        lang = lang_for(db, message)
        if not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999.99") or len(details) > 160:
            await message.answer(tr(lang, "invalid_values"))
            return False
        user = linked_user(db, message.from_user.id)
        if not user:
            await message.answer(tr(lang, "not_linked"))
            return False
        category_id = None
        if category_name:
            category_id = db.scalar(select(Category.id).where(
                Category.household_id == user.household_id,
                func.lower(Category.name) == category_name.lower(),
            ))
            if category_id is None:
                await message.answer(tr(lang, "category_missing"))
                return False
        try:
            expense = save_expense(db, user, ExpenseInput(
                amount=amount, spent_on=spent_on, merchant=details[:160], currency="EUR",
                description=details[:500], category_id=category_id,
            ))
        except Exception as exc:
            db.rollback()
            logger.error("Telegram expense insertion failed",
                         extra={"error_type": type(exc).__name__})
            await message.answer(tr(lang, "save_failed"))
            return False
        alerts = budget_alerts(db, date.today(), user.id)
    await message.answer(tr(lang, "saved", amount=f"{expense.amount:.2f}", currency=expense.currency))
    await send_alerts(alerts)
    return True


def sniff_mime(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    try:
        image = Image.open(io.BytesIO(content))
        mime = Image.MIME.get(image.format or "")
        image.verify()
    except (UnidentifiedImageError, OSError):
        return None
    return mime if mime in ALLOWED_MIMES else None


@dispatcher.message(F.photo | F.document)
async def receipt_file(message: Message, state: FSMContext) -> None:
    await state.clear()
    with SessionLocal() as db:
        lang = lang_for(db, message)
        user = linked_user(db, message.from_user.id)
        user_id = user.id if user else None
    if user_id is None:
        await message.answer(tr(lang, "not_linked"))
        return
    attachment = message.photo[-1] if message.photo else message.document
    if message.document and (message.document.mime_type or "") not in ALLOWED_MIMES:
        await message.answer(tr(lang, "photo_unsupported"))
        return
    if (attachment.file_size or 0) > settings.max_upload_bytes:
        await message.answer(tr(lang, "photo_too_big"))
        return
    await message.answer(tr(lang, "photo_reading"))
    buffer = io.BytesIO()
    await bot.download(attachment, destination=buffer)
    content = buffer.getvalue()
    if len(content) > settings.max_upload_bytes:
        await message.answer(tr(lang, "photo_too_big"))
        return
    mime = sniff_mime(content)
    if mime is None:
        await message.answer(tr(lang, "photo_invalid"))
        return
    try:
        extraction = await asyncio.to_thread(get_extractor().extract, content, mime)
    except Exception as exc:
        logger.error("Telegram receipt extraction failed", extra={"error_type": type(exc).__name__})
        await message.answer(tr(lang, "photo_failed"))
        return
    if not extraction.amount or extraction.amount <= 0:
        await message.answer(tr(lang, "photo_no_amount"))
        return
    key = storage.save(content, ALLOWED_MIMES[mime])
    with SessionLocal() as db:
        user = db.get(User, user_id)
        category_id = None
        if extraction.suggested_category:
            category_id = db.scalar(select(Category.id).where(
                Category.household_id == user.household_id,
                func.lower(Category.name) == extraction.suggested_category.lower()))
        try:
            expense = save_expense(db, user, ExpenseInput(
                amount=extraction.amount, currency=extraction.currency,
                spent_on=extraction.spent_on or date.today(),
                merchant=(extraction.merchant or "Telegram")[:160], description=extraction.description,
                category_id=category_id, payment_method=extraction.payment_method,
                tax_amount=extraction.tax_amount,
                items=[item.model_dump() for item in extraction.items],
                review_status="needs_review", document_key=key))
        except Exception as exc:
            db.rollback()
            storage.delete(key)
            logger.error("Telegram receipt insertion failed", extra={"error_type": type(exc).__name__})
            await message.answer(tr(lang, "save_failed"))
            return
        alerts = budget_alerts(db, date.today(), user.id)
    await message.answer(tr(lang, "photo_saved", merchant=expense.merchant, amount=f"{expense.amount:.2f}",
                            currency=expense.currency, date=f"{expense.spent_on:%d/%m/%Y}"))
    await send_alerts(alerts)


@dispatcher.message(Command("ultime", "latest"))
async def latest(message: Message) -> None:
    with SessionLocal() as db:
        lang = lang_for(db, message)
        user = linked_user(db, message.from_user.id)
        if not user:
            await message.answer(tr(lang, "not_linked"))
            return
        rows = db.scalars(select(Expense).where(Expense.household_id == user.household_id,
                                                visible_expenses(user))
                          .order_by(Expense.spent_on.desc(), Expense.id.desc()).limit(5)).all()
        answer = "\n".join(f"{row.spent_on:%d/%m} · {row.merchant[:40]}: {row.amount:.2f} {row.currency}"
                           for row in rows)
    await message.answer(answer or tr(lang, "none"))


@dispatcher.message(Command("totale", "total"))
async def total(message: Message) -> None:
    with SessionLocal() as db:
        lang = lang_for(db, message)
        user = linked_user(db, message.from_user.id)
        if not user:
            await message.answer(tr(lang, "not_linked"))
            return
        amount = db.scalar(select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.household_id == user.household_id, visible_expenses(user),
            Expense.currency == "EUR",
            Expense.spent_on >= date.today().replace(day=1)))
    await message.answer(tr(lang, "month_total", amount=f"{amount:.2f}"))


@dispatcher.message(Command("categorie", "categories"))
async def list_categories(message: Message) -> None:
    with SessionLocal() as db:
        lang = lang_for(db, message)
        user = linked_user(db, message.from_user.id)
        names = db.scalars(select(Category.name).where(Category.household_id == user.household_id)
                           .order_by(Category.name)).all() if user else []
    await message.answer(tr(lang, "categories", names=", ".join(names)) if names
                         else tr(lang, "categories_empty"))


@dispatcher.message(Command("tag", "tags"))
async def list_tags(message: Message) -> None:
    with SessionLocal() as db:
        lang = lang_for(db, message)
        user = linked_user(db, message.from_user.id)
        names = db.scalars(select(Tag.name).where(Tag.household_id == user.household_id)
                           .order_by(Tag.name)).all() if user else []
    await message.answer(tr(lang, "tags", names=", ".join(names)) if names else tr(lang, "tags_empty"))


@dispatcher.message(Command("budget"))
async def budget(message: Message) -> None:
    with SessionLocal() as db:
        lang = lang_for(db, message)
        user = linked_user(db, message.from_user.id)
        if not user:
            await message.answer(tr(lang, "not_linked"))
            return
        items = budget_status(db, user.household_id, user.id, date.today())
    if not items:
        await message.answer(tr(lang, "budget_none"))
        return
    lines = [tr(lang, "budget_title")] + [
        f"• {item['name']}: {item['spent']:.2f} / {item['budget']:.2f} EUR ({int(item['ratio'] * 100)}%)"
        for item in items]
    await message.answer("\n".join(lines))


PERIOD_WORDS = {"off": "off", "weekly": "weekly", "settimanale": "weekly", "monthly": "monthly",
                "mensile": "monthly"}


@dispatcher.message(Command("notifiche", "notify"))
async def notifications(message: Message) -> None:
    args = (message.text or "").lower().split()[1:]
    with SessionLocal() as db:
        lang = lang_for(db, message)
        link = db.get(TelegramUser, message.from_user.id)
        if not link:
            await message.answer(tr(lang, "not_linked"))
            return
        updated = False
        if len(args) == 1 and args[0] in PERIOD_WORDS:
            link.notify_period, updated = PERIOD_WORDS[args[0]], True
            link.last_summary_key = ""
        elif len(args) == 2 and args[0] == "budget" and args[1] in ("on", "off"):
            link.notify_budget, updated = args[1] == "on", True
        elif args:
            await message.answer(tr(lang, "notify_usage"))
            return
        db.commit()
        text = tr(lang, "notify_state", period=tr(lang, f"period_{link.notify_period}"),
                  budget=tr(lang, "on" if link.notify_budget else "off"))
    await message.answer((tr(lang, "notify_updated") if updated else "") + text)


@dispatcher.message(Command("cancel"))
async def cancel_guided_expense(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(tr(lang_for(None, message), "cancelled"))


async def notification_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                items = due_summaries(db, datetime.now(), settings.telegram_notify_hour)
                items += budget_alerts(db, date.today())
            await send_alerts(items)
        except Exception as exc:
            logger.error("Telegram notification loop failed", extra={"error_type": type(exc).__name__})
        await asyncio.sleep(NOTIFICATION_INTERVAL_SECONDS)


async def main() -> None:
    if bot is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN non configurato")
    notifier = asyncio.create_task(notification_loop())
    try:
        await dispatcher.start_polling(bot)
    finally:
        notifier.cancel()


if __name__ == "__main__":
    asyncio.run(main())
