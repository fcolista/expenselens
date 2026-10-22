import csv
import io
import json
import re
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import Select, delete, or_, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.ai import get_extractor
from app.config import settings
from app.database import get_db
from app.models import Category, Expense, ExpenseTag, Tag, User
from app.limits import OCR_UPLOADS_PER_HOUR, limiter
from app.schemas import ExpenseInput, ExpenseOutput
from app.security import current_user, csrf_check
from app.storage import storage

router = APIRouter(prefix="/api", tags=["expenses"])
ALLOWED_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                 "application/pdf": ".pdf"}


def validate_relations(db: Session, user: User, body: ExpenseInput) -> None:
    if body.category_id is not None and not db.scalar(select(Category.id).where(
            Category.id == body.category_id, Category.household_id == user.household_id)):
        raise HTTPException(422, "Categoria non valida")
    if body.tag_ids:
        found = db.scalars(select(Tag.id).where(
            Tag.id.in_(set(body.tag_ids)), Tag.household_id == user.household_id)).all()
        if len(found) != len(set(body.tag_ids)):
            raise HTTPException(422, "Uno o più tag non validi")


def save_expense(db: Session, user: User, body: ExpenseInput) -> Expense:
    validate_relations(db, user, body)
    data = body.model_dump(exclude={"tag_ids"})
    data["items"] = [item.model_dump(mode="json") for item in body.items]
    if data["document_key"] and not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp|pdf)", data["document_key"]):
        raise HTTPException(422, "Documento non valido")
    expense = Expense(**data, household_id=user.household_id, created_by=user.id)
    db.add(expense)
    db.flush()
    for tag_id in set(body.tag_ids):
        db.add(ExpenseTag(expense_id=expense.id, tag_id=tag_id))
    db.commit()
    db.refresh(expense)
    return expense


def base_query(user: User) -> Select[tuple[Expense]]:
    return select(Expense).where(
        Expense.household_id == user.household_id,
        ((Expense.visibility == "shared") | (Expense.created_by == user.id)),
    )


@router.get("/expenses", response_model=list[ExpenseOutput])
def list_expenses(user: User = Depends(current_user), db: Session = Depends(get_db),
                  start: date | None = None, end: date | None = None,
                  category_id: int | None = None, search: str | None = Query(default=None, max_length=100),
                  sort: str = Query(default="newest", pattern="^(newest|oldest|amount_desc)$"),
                  limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)
                  ) -> list[ExpenseOutput]:
    if start and end and start > end:
        raise HTTPException(422, "La data iniziale deve precedere la data finale")
    query = base_query(user)
    if start:
        query = query.where(Expense.spent_on >= start)
    if end:
        query = query.where(Expense.spent_on <= end)
    if category_id:
        query = query.where(Expense.category_id == category_id)
    if search:
        query = query.where(or_(Expense.merchant.ilike(f"%{search}%"),
                                Expense.description.ilike(f"%{search}%")))
    ordering = {"newest": Expense.spent_on.desc(), "oldest": Expense.spent_on.asc(),
                "amount_desc": Expense.amount.desc()}[sort]
    return db.scalars(query.order_by(ordering, Expense.id.desc()).limit(limit).offset(offset)).all()


@router.post("/expenses", response_model=ExpenseOutput, status_code=201,
             dependencies=[Depends(csrf_check)])
def create_expense(body: ExpenseInput, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Expense:
    return save_expense(db, user, body)


@router.put("/expenses/{expense_id}", response_model=ExpenseOutput,
            dependencies=[Depends(csrf_check)])
def update_expense(expense_id: int, body: ExpenseInput, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> Expense:
    expense = db.scalar(base_query(user).where(Expense.id == expense_id))
    if not expense:
        raise HTTPException(404, "Spesa non trovata")
    validate_relations(db, user, body)
    if body.document_key and not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp|pdf)", body.document_key):
        raise HTTPException(422, "Documento non valido")
    for key, value in body.model_dump(exclude={"tag_ids"}).items():
        setattr(expense, key, [item.model_dump(mode="json") for item in body.items]
                if key == "items" else value)
    db.execute(delete(ExpenseTag).where(ExpenseTag.expense_id == expense.id))
    for tag_id in set(body.tag_ids):
        db.add(ExpenseTag(expense_id=expense.id, tag_id=tag_id))
    db.commit()
    db.refresh(expense)
    return expense


@router.post("/expenses/{expense_id}/duplicate", response_model=ExpenseOutput, status_code=201,
             dependencies=[Depends(csrf_check)])
def duplicate_expense(expense_id: int, user: User = Depends(current_user),
                      db: Session = Depends(get_db)) -> Expense:
    original = db.scalar(base_query(user).where(Expense.id == expense_id))
    if not original:
        raise HTTPException(404, "Spesa non trovata")
    tag_ids = db.scalars(select(ExpenseTag.tag_id).where(ExpenseTag.expense_id == expense_id)).all()
    body = ExpenseInput(amount=original.amount, currency=original.currency, spent_on=date.today(),
                        merchant=original.merchant, description=original.description,
                        category_id=original.category_id, tag_ids=tag_ids,
                        payment_method=original.payment_method, visibility=original.visibility,
                        review_status="reviewed", tax_amount=original.tax_amount,
                        items=original.items or [])
    return save_expense(db, user, body)


@router.delete("/expenses/{expense_id}", status_code=204, dependencies=[Depends(csrf_check)])
def delete_expense(expense_id: int, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> None:
    expense = db.scalar(base_query(user).where(Expense.id == expense_id))
    if not expense:
        raise HTTPException(404, "Spesa non trovata")
    if expense.document_key:
        storage.delete(expense.document_key)
    db.delete(expense)
    db.commit()


@router.post("/documents/extract", dependencies=[Depends(csrf_check)])
@limiter.limit(f"{OCR_UPLOADS_PER_HOUR}/hour")
async def upload_document(request: Request, file: UploadFile = File(...),
                          user: User = Depends(current_user)) -> dict[str, object]:
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, "Il documento supera il limite consentito")
    mime = file.content_type or ""
    if mime not in ALLOWED_MIMES:
        raise HTTPException(415, "Formato non supportato: carica JPEG, PNG, WebP o PDF")
    if mime == "application/pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(415, "Il file PDF non è valido")
    else:
        try:
            image = Image.open(io.BytesIO(content))
            actual_mime = Image.MIME.get(image.format or "")
            image.verify()
        except (UnidentifiedImageError, OSError):
            raise HTTPException(415, "Il file immagine non è valido") from None
        if actual_mime != mime:
            raise HTTPException(415, "Il tipo dichiarato non corrisponde al contenuto del file")
    key = storage.save(content, ALLOWED_MIMES[mime])
    try:
        extraction = await run_in_threadpool(get_extractor().extract, content, mime)
    except Exception:
        storage.delete(key)
        raise
    return {"document_key": key, "extraction": extraction}


@router.get("/documents/{key}")
def get_document(key: str, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> Response:
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp|pdf)", key):
        raise HTTPException(404, "Documento non trovato")
    if not db.scalar(select(Expense.id).where(
            Expense.household_id == user.household_id, Expense.document_key == key,
            ((Expense.visibility == "shared") | (Expense.created_by == user.id)))):
        raise HTTPException(404, "Documento non trovato")
    content = storage.read(key)
    from fastapi.responses import Response
    mime = "application/pdf" if key.endswith(".pdf") else f"image/{'jpeg' if key.endswith('.jpg') else key.rsplit('.', 1)[1]}"
    return Response(content, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})


@router.get("/expenses/export/{format}")
def export_expenses(format: str, user: User = Depends(current_user), db: Session = Depends(get_db),
                    start: date | None = None, end: date | None = None,
                    currency: str | None = Query(default=None, pattern="^[A-Z]{3}$"),
                    category_id: int | None = None, search: str | None = Query(default=None, max_length=100),
                    sort: str = Query(default="newest", pattern="^(newest|oldest|amount_desc)$")
                    ) -> Response:
    if format not in {"csv", "json"}:
        raise HTTPException(404, "Formato non supportato")
    if start and end and start > end:
        raise HTTPException(422, "La data iniziale deve precedere la data finale")
    query = base_query(user)
    if start:
        query = query.where(Expense.spent_on >= start)
    if end:
        query = query.where(Expense.spent_on <= end)
    if currency:
        query = query.where(Expense.currency == currency)
    if category_id:
        query = query.where(Expense.category_id == category_id)
    if search:
        query = query.where(or_(Expense.merchant.ilike(f"%{search}%"),
                                Expense.description.ilike(f"%{search}%")))
    ordering = {"newest": Expense.spent_on.desc(), "oldest": Expense.spent_on.asc(),
                "amount_desc": Expense.amount.desc()}[sort]
    expenses = db.scalars(query.order_by(ordering, Expense.id.desc())).all()
    rows = [ExpenseOutput.model_validate(item).model_dump(mode="json") for item in expenses]
    if format == "json":
        return StreamingResponse(io.BytesIO(json.dumps(rows, ensure_ascii=False).encode()),
                                 media_type="application/json",
                                 headers={"Content-Disposition": "attachment; filename=expenses.json"})
    stream = io.StringIO(newline="")
    columns = list(rows[0]) if rows else list(ExpenseOutput.model_fields)
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        safe_row = {
            key: ("'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value)
            for key, value in row.items()
        }
        writer.writerow(safe_row)
    return StreamingResponse(iter([stream.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=expenses.csv"})
