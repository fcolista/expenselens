from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=100)
    household_name: str | None = Field(default=None, min_length=1, max_length=100)
    invite_token: str | None = Field(default=None, min_length=20, max_length=100)


class HouseholdMemberInput(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)


class LoginInput(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = Field(default=None, max_length=32,
                                  pattern=r"^(?:\d{6}|[0-9a-f]{10})$")


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    display_name: str
    household_id: int
    mfa_enabled: bool
    is_admin: bool


class AuthOutput(BaseModel):
    user: UserOutput
    csrf_token: str


class CategoryOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    color: str
    icon: str = ""
    monthly_budget: Decimal | None = None


class TagOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    category_ids: list[int]


class ExpenseItemInput(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    total: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)


class ExpenseInput(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    spent_on: date
    merchant: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    category_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list, max_length=30)
    payment_method: str | None = Field(default=None, max_length=40)
    visibility: Literal["personal", "shared"] = "shared"
    review_status: Literal["reviewed", "needs_review"] = "reviewed"
    document_key: str | None = Field(default=None, max_length=80)
    tax_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    items: list[ExpenseItemInput] = Field(default_factory=list, max_length=200)


class ExpenseOutput(ExpenseInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_by: int


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=1, max_length=200)
    quantity: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=3)
    unit_price: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)
    total: Decimal | None = Field(default=None, max_digits=12, decimal_places=2)


class ReceiptExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spent_on: date | None = None
    merchant: str | None = Field(default=None, max_length=160)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    tax_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    suggested_category: str | None = Field(default=None, max_length=60)
    description: str = Field(default="", max_length=500)
    payment_method: Literal["Contanti", "Carta", "Elettronico"] | None = None
    items: list[ExtractedItem] = Field(default_factory=list, max_length=200)
