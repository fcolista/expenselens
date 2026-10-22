from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Expense, TelegramLinkToken, TelegramUser, User, UserSession
from app.schemas import HouseholdMemberInput, UserOutput
from app.security import csrf_check, household_admin

router = APIRouter(prefix="/api/household", tags=["household"])


@router.get("/members", response_model=list[UserOutput])
def members(admin: User = Depends(household_admin),
            db: Session = Depends(get_db)) -> list[UserOutput]:
    return db.scalars(
        select(User).where(User.household_id == admin.household_id).order_by(User.id)
    ).all()


@router.put("/members/{member_id}", response_model=UserOutput,
            dependencies=[Depends(csrf_check)])
def update_member(member_id: int, body: HouseholdMemberInput,
                  admin: User = Depends(household_admin),
                  db: Session = Depends(get_db)) -> UserOutput:
    member = db.scalar(select(User).where(
        User.id == member_id, User.household_id == admin.household_id
    ))
    if not member:
        raise HTTPException(404, "Membro non trovato")

    email = str(body.email).lower()
    duplicate = db.scalar(select(User.id).where(User.email == email, User.id != member.id))
    if duplicate:
        raise HTTPException(409, "Email già utilizzata")
    name = body.display_name.strip()
    if not name:
        raise HTTPException(422, "Il nome non può essere vuoto")

    member.email = email
    member.display_name = name
    db.commit()
    db.refresh(member)
    return UserOutput.model_validate(member)


@router.delete("/members/{member_id}", status_code=204, dependencies=[Depends(csrf_check)])
def delete_member(member_id: int, admin: User = Depends(household_admin),
                  db: Session = Depends(get_db)) -> None:
    if member_id == admin.id:
        raise HTTPException(400, "L'amministratore non può rimuovere il proprio account")
    member = db.scalar(select(User).where(
        User.id == member_id, User.household_id == admin.household_id
    ))
    if not member:
        raise HTTPException(404, "Membro non trovato")

    db.execute(update(Expense).where(Expense.created_by == member.id)
               .values(created_by=admin.id))
    db.execute(delete(UserSession).where(UserSession.user_id == member.id))
    db.execute(delete(TelegramLinkToken).where(TelegramLinkToken.user_id == member.id))
    db.execute(delete(TelegramUser).where(TelegramUser.user_id == member.id))
    db.delete(member)
    db.commit()
