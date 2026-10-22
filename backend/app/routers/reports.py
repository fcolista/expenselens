from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Expense, ExpenseTag, Tag, User
from app.routers.expenses import base_query
from app.security import current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
def report(user: User = Depends(current_user), db: Session = Depends(get_db),
           start: date = Query(default_factory=lambda: date.today().replace(day=1)),
           end: date = Query(default_factory=date.today),
           currency: str = Query(default="EUR", pattern="^[A-Z]{3}$")) -> dict[str, object]:
    if start > end:
        raise HTTPException(422, "La data iniziale deve precedere la data finale")
    query = base_query(user).where(Expense.spent_on >= start, Expense.spent_on <= end,
                                  Expense.currency == currency)
    filtered_expenses = query.subquery()
    total, count = db.execute(select(func.coalesce(func.sum(filtered_expenses.c.amount), 0),
                                    func.count()).select_from(filtered_expenses)).one()
    category_name = func.coalesce(Category.name, "Senza categoria")
    categories = db.execute(select(category_name, func.sum(Expense.amount).label("total"))
                             .select_from(Expense).outerjoin(Category, Expense.category_id == Category.id)
                             .where(Expense.household_id == user.household_id,
                                    Expense.spent_on >= start, Expense.spent_on <= end,
                                    Expense.currency == currency,
                                    ((Expense.visibility == "shared") | (Expense.created_by == user.id)))
                             .group_by(category_name).order_by(func.sum(Expense.amount).desc())).all()
    days = db.execute(select(Expense.spent_on, func.sum(Expense.amount).label("total"))
                      .where(Expense.household_id == user.household_id,
                             Expense.spent_on >= start, Expense.spent_on <= end,
                             Expense.currency == currency,
                             ((Expense.visibility == "shared") | (Expense.created_by == user.id)))
                      .group_by(Expense.spent_on).order_by(Expense.spent_on)).all()
    merchants = db.execute(select(Expense.merchant, func.sum(Expense.amount).label("total"))
                           .where(Expense.household_id == user.household_id,
                                  Expense.spent_on >= start, Expense.spent_on <= end,
                                  Expense.currency == currency,
                                  ((Expense.visibility == "shared") | (Expense.created_by == user.id)))
                           .group_by(Expense.merchant).order_by(func.sum(Expense.amount).desc()).limit(5)).all()
    tags = db.execute(select(Tag.name, func.sum(Expense.amount).label("total"))
                      .join(ExpenseTag, ExpenseTag.tag_id == Tag.id)
                      .join(Expense, Expense.id == ExpenseTag.expense_id)
                      .where(Expense.household_id == user.household_id,
                             Tag.household_id == user.household_id,
                             Expense.spent_on >= start, Expense.spent_on <= end,
                             Expense.currency == currency,
                             ((Expense.visibility == "shared") | (Expense.created_by == user.id)))
                      .group_by(Tag.name).order_by(func.sum(Expense.amount).desc()).limit(10)).all()
    members = db.execute(select(User.display_name, func.sum(Expense.amount).label("total"))
                         .join(Expense, Expense.created_by == User.id)
                         .where(Expense.household_id == user.household_id,
                                Expense.spent_on >= start, Expense.spent_on <= end,
                                Expense.currency == currency,
                                ((Expense.visibility == "shared") | (Expense.created_by == user.id)))
                         .group_by(User.display_name).order_by(func.sum(Expense.amount).desc())).all()
    return {"start": start, "end": end, "currency": currency, "total": total, "count": count,
            "by_category": [{"name": name or "Senza categoria", "total": amount} for name, amount in categories],
            "by_day": [{"date": day, "total": amount} for day, amount in days],
            "top_merchants": [{"name": name, "total": amount} for name, amount in merchants],
            "by_tag": [{"name": name, "total": amount} for name, amount in tags],
            "by_user": [{"name": name, "total": amount} for name, amount in members]}
