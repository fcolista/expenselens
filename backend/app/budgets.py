from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Category, Expense, ExchangeRate

BASE_CURRENCY = "EUR"


def household_rates(db: Session, household_id: int) -> dict[str, Decimal]:
    rates = {row.currency: row.rate for row in db.scalars(
        select(ExchangeRate).where(ExchangeRate.household_id == household_id))}
    rates[BASE_CURRENCY] = Decimal(1)
    return rates


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end


def converted_total(rows: list[tuple[str, Decimal]], rates: dict[str, Decimal]) -> tuple[Decimal, list[str]]:
    total, missing = Decimal(0), []
    for currency, amount in rows:
        if currency in rates:
            total += amount * rates[currency]
        else:
            missing.append(currency)
    return total.quantize(Decimal("0.01")), missing


def budget_status(db: Session, household_id: int, user_id: int, day: date) -> list[dict]:
    """Spent vs monthly budget per category, in the base currency."""
    start, end = month_bounds(day)
    categories = db.scalars(select(Category).where(
        Category.household_id == household_id, Category.monthly_budget.is_not(None))
        .order_by(Category.name)).all()
    if not categories:
        return []
    rates = household_rates(db, household_id)
    spent_rows = db.execute(
        select(Expense.category_id, Expense.currency, func.sum(Expense.amount))
        .where(Expense.household_id == household_id, Expense.spent_on >= start, Expense.spent_on < end,
               Expense.category_id.in_([c.id for c in categories]),
               or_(Expense.visibility == "shared", Expense.created_by == user_id))
        .group_by(Expense.category_id, Expense.currency)).all()
    result = []
    for category in categories:
        spent, _ = converted_total(
            [(currency, amount) for category_id, currency, amount in spent_rows
             if category_id == category.id], rates)
        budget = category.monthly_budget
        result.append({
            "category_id": category.id, "name": category.name, "color": category.color,
            "budget": budget, "spent": spent, "ratio": float(spent / budget) if budget else 0.0,
        })
    return result
