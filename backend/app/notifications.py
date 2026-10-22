from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.budgets import budget_status, converted_total, household_rates, month_bounds
from app.models import BudgetAlertSent, Category, Expense, TelegramUser, User
from app.telegram_i18n import tr

BUDGET_LEVELS = (100, 80)


def period_summary(db: Session, user: User, start: date, end: date) -> dict:
    """Totals for [start, end) in EUR, converted with the household's rates."""
    visible = (Expense.household_id == user.household_id, Expense.spent_on >= start,
               Expense.spent_on < end,
               or_(Expense.visibility == "shared", Expense.created_by == user.id))
    rates = household_rates(db, user.household_id)
    by_currency = db.execute(select(Expense.currency, func.sum(Expense.amount), func.count())
                             .where(*visible).group_by(Expense.currency)).all()
    total, missing = converted_total([(c, a) for c, a, _ in by_currency], rates)
    per_category = db.execute(
        select(func.coalesce(Category.name, "—"), Expense.currency, func.sum(Expense.amount))
        .select_from(Expense).outerjoin(Category, Expense.category_id == Category.id)
        .where(*visible).group_by(Category.name, Expense.currency)).all()
    grouped: dict[str, list[tuple[str, Decimal]]] = {}
    for name, currency, amount in per_category:
        grouped.setdefault(name, []).append((currency, amount))
    top = sorted(((name, converted_total(rows, rates)[0]) for name, rows in grouped.items()),
                 key=lambda item: item[1], reverse=True)[:3]
    return {"total": total, "count": sum(count for _, _, count in by_currency),
            "top": top, "missing": missing}


def summary_text(lang: str, kind: str, start: date, end: date, data: dict) -> str:
    last_day = end - timedelta(days=1)
    lines = [tr(lang, f"summary_{kind}", start=f"{start:%d/%m/%Y}", end=f"{last_day:%d/%m/%Y}")]
    if not data["count"]:
        lines.append(tr(lang, "summary_empty"))
    else:
        lines.append(tr(lang, "summary_body", total=f"{data['total']:.2f}", count=data["count"]))
        lines.append(tr(lang, "summary_top"))
        lines += [f"• {name}: {amount:.2f} EUR" for name, amount in data["top"]]
    if data["missing"]:
        lines.append(tr(lang, "summary_missing", currencies=", ".join(sorted(set(data["missing"])))))
    return "\n".join(lines)


def _period_for(link: TelegramUser, today: date) -> tuple[str, date, date, str] | None:
    if link.notify_period == "weekly":
        this_monday = today - timedelta(days=today.weekday())
        iso = this_monday.isocalendar()
        return "week", this_monday - timedelta(days=7), this_monday, f"W{iso.year}-{iso.week:02d}"
    if link.notify_period == "monthly":
        this_month, _ = month_bounds(today)
        previous = month_bounds(this_month - timedelta(days=1))[0]
        return "month", previous, this_month, f"M{this_month:%Y-%m}"
    return None


def due_summaries(db: Session, now: datetime, notify_hour: int) -> list[tuple[int, str]]:
    """Returns (telegram_id, text) for summaries that should be sent now."""
    if now.hour < notify_hour:
        return []
    out: list[tuple[int, str]] = []
    for link in db.scalars(select(TelegramUser).where(TelegramUser.notify_period != "off")).all():
        period = _period_for(link, now.date())
        if not period:
            continue
        kind, start, end, key = period
        if not link.last_summary_key:
            link.last_summary_key = key  # do not send retroactively on first activation
            continue
        if link.last_summary_key == key:
            continue
        user = db.get(User, link.user_id)
        out.append((link.telegram_id, summary_text(link.language, kind, start, end,
                                                   period_summary(db, user, start, end))))
        link.last_summary_key = key
    db.commit()
    return out


def budget_alerts(db: Session, today: date, user_id: int | None = None) -> list[tuple[int, str]]:
    """Returns (telegram_id, text) for budgets that crossed 80% or 100% and were not yet notified."""
    month = f"{today:%Y-%m}"
    query = select(TelegramUser).where(TelegramUser.notify_budget.is_(True))
    if user_id is not None:
        query = query.where(TelegramUser.user_id == user_id)
    out: list[tuple[int, str]] = []
    for link in db.scalars(query).all():
        user = db.get(User, link.user_id)
        for item in budget_status(db, user.household_id, user.id, today):
            level = next((lv for lv in BUDGET_LEVELS if item["ratio"] * 100 >= lv), 0)
            if not level:
                continue
            sent = db.get(BudgetAlertSent, (user.id, item["category_id"], month))
            if sent and sent.level >= level:
                continue
            if sent:
                sent.level = level
            else:
                db.add(BudgetAlertSent(user_id=user.id, category_id=item["category_id"],
                                       month=month, level=level))
            out.append((link.telegram_id, tr(
                link.language, f"budget_alert_{level}", name=item["name"], spent=f"{item['spent']:.2f}",
                budget=f"{item['budget']:.2f}", pct=int(item["ratio"] * 100))))
    db.commit()
    return out
