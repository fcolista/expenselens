from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Expense, ExpenseTag, Tag, User, tag_categories
from app.schemas import CategoryOutput, TagOutput
from app.security import current_user, csrf_check

router = APIRouter(prefix="/api", tags=["taxonomy"])


class TaxonomyInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="#64748b", pattern=r"^#[0-9a-fA-F]{6}$")
    category_ids: list[int] = Field(default_factory=list, max_length=30)


class BudgetInput(BaseModel):
    monthly_budget: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)


class TagCategoriesInput(BaseModel):
    category_ids: list[int] = Field(default_factory=list, max_length=30)


class CategoryNameInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def household_categories(db: Session, user: User, category_ids: list[int]) -> list[Category]:
    ids = set(category_ids)
    categories = db.scalars(select(Category).where(
        Category.id.in_(ids), Category.household_id == user.household_id
    )).all() if ids else []
    if len(categories) != len(ids):
        raise HTTPException(422, "Una o più categorie non valide")
    return categories


@router.get("/categories", response_model=list[CategoryOutput])
def categories(user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> list[CategoryOutput]:
    return db.scalars(select(Category).where(Category.household_id == user.household_id).order_by(Category.name)).all()


@router.post("/categories", status_code=201, response_model=CategoryOutput,
             dependencies=[Depends(csrf_check)])
def create_category(body: TaxonomyInput, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> CategoryOutput:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Il nome non può essere vuoto")
    if db.scalar(select(Category.id).where(
            Category.household_id == user.household_id, Category.name == name)):
        raise HTTPException(409, "Categoria già esistente")
    item = Category(household_id=user.household_id, name=name, color=body.color)
    db.add(item)
    db.commit()
    db.refresh(item)
    return CategoryOutput.model_validate(item)


@router.put("/categories/{category_id}", response_model=CategoryOutput,
            dependencies=[Depends(csrf_check)])
def rename_category(category_id: int, body: CategoryNameInput, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> CategoryOutput:
    item = db.scalar(select(Category).where(
        Category.id == category_id, Category.household_id == user.household_id
    ))
    if not item:
        raise HTTPException(404, "Categoria non trovata")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Il nome non può essere vuoto")
    if db.scalar(select(Category.id).where(
            Category.household_id == user.household_id, Category.name == name,
            Category.id != category_id)):
        raise HTTPException(409, "Categoria già esistente")
    item.name = name
    db.commit()
    db.refresh(item)
    return CategoryOutput.model_validate(item)


@router.get("/tags", response_model=list[TagOutput])
def tags(user: User = Depends(current_user),
         db: Session = Depends(get_db)) -> list[TagOutput]:
    return db.scalars(select(Tag).where(Tag.household_id == user.household_id).order_by(Tag.name)).all()


@router.post("/tags", status_code=201, response_model=TagOutput,
             dependencies=[Depends(csrf_check)])
def create_tag(body: TaxonomyInput, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> TagOutput:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Il nome non può essere vuoto")
    if db.scalar(select(Tag.id).where(Tag.household_id == user.household_id, Tag.name == name)):
        raise HTTPException(409, "Tag già esistente")
    item = Tag(
        household_id=user.household_id,
        name=name,
        categories=household_categories(db, user, body.category_ids),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return TagOutput.model_validate(item)


@router.put("/tags/{tag_id}", response_model=TagOutput, dependencies=[Depends(csrf_check)])
def update_tag_categories(tag_id: int, body: TagCategoriesInput, user: User = Depends(current_user),
                          db: Session = Depends(get_db)) -> TagOutput:
    item = db.scalar(select(Tag).where(
        Tag.id == tag_id, Tag.household_id == user.household_id
    ))
    if not item:
        raise HTTPException(404, "Tag non trovato")
    item.categories = household_categories(db, user, body.category_ids)
    db.commit()
    db.refresh(item)
    return TagOutput.model_validate(item)


@router.put("/categories/{category_id}/budget", response_model=CategoryOutput,
            dependencies=[Depends(csrf_check)])
def set_category_budget(category_id: int, body: BudgetInput, user: User = Depends(current_user),
                        db: Session = Depends(get_db)) -> CategoryOutput:
    item = db.scalar(select(Category).where(
        Category.id == category_id, Category.household_id == user.household_id))
    if not item:
        raise HTTPException(404, "Categoria non trovata")
    item.monthly_budget = body.monthly_budget
    db.commit()
    db.refresh(item)
    return CategoryOutput.model_validate(item)


@router.delete("/categories/{category_id}", status_code=204, dependencies=[Depends(csrf_check)])
def delete_category(category_id: int, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> None:
    item = db.scalar(select(Category).where(
        Category.id == category_id, Category.household_id == user.household_id))
    if not item:
        raise HTTPException(404, "Categoria non trovata")
    db.execute(delete(tag_categories).where(tag_categories.c.category_id == item.id))
    db.execute(update(Expense).where(Expense.category_id == item.id).values(category_id=None))
    db.delete(item)
    db.commit()


@router.delete("/tags/{tag_id}", status_code=204, dependencies=[Depends(csrf_check)])
def delete_tag(tag_id: int, user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> None:
    item = db.scalar(select(Tag).where(Tag.id == tag_id, Tag.household_id == user.household_id))
    if not item:
        raise HTTPException(404, "Tag non trovato")
    db.execute(delete(ExpenseTag).where(ExpenseTag.tag_id == item.id))
    db.delete(item)
    db.commit()
