import base64
from datetime import date
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import ai
from app.database import Base
from app.models import Expense, Household, User
from app.routers.expenses import base_query
from app.routers.reports import report
from app.schemas import ExpenseItemInput, ReceiptExtraction
from app.services import parse_amount, parse_spesa_details


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_family(db: Session):
    family = Household(name="Test")
    other_family = Household(name="Other")
    db.add_all([family, other_family])
    db.flush()
    owner = User(email="owner@example.test", password_hash="hash", display_name="Owner",
                 household_id=family.id)
    member = User(email="member@example.test", password_hash="hash", display_name="Member",
                  household_id=family.id)
    outsider = User(email="other@example.test", password_hash="hash", display_name="Outsider",
                    household_id=other_family.id)
    db.add_all([owner, member, outsider])
    db.flush()
    return owner, member, outsider


@pytest.mark.parametrize(("text", "expected"), [
    ("12,50", Decimal("12.50")), ("0.01", Decimal("0.01")), ("1200", Decimal("1200")),
])
def test_parse_amount_valid(text: str, expected: Decimal):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["0", "-1", "1.001", "1e3", "NaN", "1,2,3", ""])
def test_parse_amount_rejects_invalid_values(text: str):
    with pytest.raises(ValueError):
        parse_amount(text)


def test_parse_spesa_details_checks_optional_category_and_iso_date():
    description, category, spent_on = parse_spesa_details("Pranzo | Alimentari | 2026-10-20")
    assert (description, category, spent_on) == ("Pranzo", "Alimentari", date(2026, 10, 20))
    with pytest.raises(ValueError):
        parse_spesa_details("Pranzo | Alimentari | 2026-02-30")


def test_receipt_schema_rejects_unknown_ai_fields():
    with pytest.raises(ValidationError):
        ReceiptExtraction.model_validate({"merchant": "Shop", "amount": 12, "total_guess": 12})


def test_expense_items_accept_negative_discount_amounts():
    item = ExpenseItemInput.model_validate({
        "description": "Sconto",
        "quantity": "1",
        "unit_price": "-1.00",
        "total": "-1.00",
    })
    assert item.unit_price == Decimal("-1.00")
    assert item.total == Decimal("-1.00")


def test_household_filter_hides_other_household_and_private_expenses(db: Session):
    owner, member, outsider = make_family(db)
    db.add_all([
        Expense(household_id=owner.household_id, created_by=owner.id, amount=Decimal("10"),
                currency="EUR", spent_on=date(2026, 1, 1), merchant="Private", visibility="personal"),
        Expense(household_id=owner.household_id, created_by=owner.id, amount=Decimal("20"),
                currency="EUR", spent_on=date(2026, 1, 1), merchant="Shared", visibility="shared"),
        Expense(household_id=outsider.household_id, created_by=outsider.id, amount=Decimal("99"),
                currency="EUR", spent_on=date(2026, 1, 1), merchant="Outside", visibility="shared"),
    ])
    db.commit()
    member_results = db.scalars(base_query(member)).all()
    owner_results = db.scalars(base_query(owner)).all()
    assert {item.merchant for item in member_results} == {"Shared"}
    assert {item.merchant for item in owner_results} == {"Private", "Shared"}


def test_report_aggregates_in_sql_and_applies_household_visibility(db: Session):
    owner, member, outsider = make_family(db)
    db.add_all([
        Expense(household_id=owner.household_id, created_by=owner.id, amount=Decimal("10.25"),
                currency="EUR", spent_on=date(2026, 1, 5), merchant="Shop", visibility="shared"),
        Expense(household_id=owner.household_id, created_by=owner.id, amount=Decimal("100"),
                currency="USD", spent_on=date(2026, 1, 5), merchant="Foreign", visibility="shared"),
        Expense(household_id=owner.household_id, created_by=owner.id, amount=Decimal("75"),
                currency="EUR", spent_on=date(2026, 1, 5), merchant="Private", visibility="personal"),
        Expense(household_id=outsider.household_id, created_by=outsider.id, amount=Decimal("500"),
                currency="EUR", spent_on=date(2026, 1, 5), merchant="Outside", visibility="shared"),
    ])
    db.commit()
    result = report(user=member, db=db, start=date(2026, 1, 1), end=date(2026, 1, 31), currency="EUR")
    assert result["total"] == Decimal("10.25")
    assert result["count"] == 1
    assert result["currency"] == "EUR"


def test_vision_adapter_validates_structured_response(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": (
                '{"merchant":"Shop","amount":"8.50","currency":"EUR",'
                '"description":"Shop, via Roma 1","items":[{"description":"Caffè"}]}'
            )}}]}

    monkeypatch.setattr(ai.settings, "vision_api_url", "https://example.test/api")
    monkeypatch.setattr(ai.settings, "vision_api_key", "test-key")
    monkeypatch.setattr(ai.settings, "vision_model", "test-model")
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result = ai.VisionAPIExtractor().extract(b"not-used-by-mock", "image/png")
    assert result.merchant == "Shop"
    assert result.amount == Decimal("8.50")
    assert result.description == "Caffè"


@pytest.mark.parametrize(("text", "expected"), [
    ("PAGAMENTO CONTANTI", "Contanti"),
    ("VISA contactless", "Carta"),
    ("Credit Card", "Carta"),
    ("Pagamento con Satispay", "Elettronico"),
    ("PAGAMENTO", None),
    ("CONTANTI VISA", None),
])
def test_infer_payment_method_is_conservative(text: str, expected: str | None):
    assert ai.infer_payment_method(text) == expected


def test_azure_document_intelligence_extracts_receipt_fields(monkeypatch):
    operation_url = (
        "https://example.cognitiveservices.azure.com/documentintelligence/"
        "documentModels/prebuilt-receipt/analyzeResults/test-id?api-version=2024-11-30"
    )
    posted = {}
    poll_count = 0

    class FakeResponse:
        def __init__(self, payload, headers=None):
            self.payload = payload
            self.headers = headers or {}

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted.update(kwargs)
        return FakeResponse({}, {"Operation-Location": operation_url})

    def fake_get(url, **kwargs):
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            return FakeResponse({"status": "running"}, {"Retry-After": "0.1"})
        return FakeResponse({
            "status": "succeeded",
            "analyzeResult": {
                "content": "SUPERMERCATO\nPAGAMENTO VISA\nTOTALE 12,50 EUR",
                "documents": [{"fields": {
                    "MerchantName": {"valueString": "Supermercato"},
                    "TransactionDate": {"valueDate": "2026-10-20"},
                    "Total": {"valueCurrency": {"amount": 12.5, "currencyCode": "EUR"}},
                    "TotalTax": {"valueCurrency": {"amount": 1.2}},
                    "PaymentMethod": {"valueString": "Visa"},
                    "Items": {"valueArray": [{"valueObject": {
                        "Description": {"valueString": "Pane"},
                        "Quantity": {"valueNumber": 2},
                        "Price": {"valueCurrency": {"amount": 1.25}},
                        "TotalPrice": {"valueCurrency": {"amount": 2.5}},
                    }}, {"valueObject": {
                        "Description": {"valueString": "Sconto"},
                        "Quantity": {"valueNumber": 1},
                        "Price": {"valueCurrency": {"amount": -1.0}},
                        "TotalPrice": {"valueCurrency": {"amount": -1.0}},
                    }}]},
                }}],
            },
        })

    monkeypatch.setattr(ai.settings, "azure_document_intelligence_endpoint",
                        "https://example.cognitiveservices.azure.com/")
    monkeypatch.setattr(ai.settings, "azure_document_intelligence_key", "test-key")
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(ai.time, "sleep", lambda _: None)

    result = ai.AzureDocumentIntelligenceExtractor().extract(b"receipt-bytes", "image/jpeg")

    assert posted["url"] == (
        "https://example.cognitiveservices.azure.com/documentintelligence/"
        "documentModels/prebuilt-receipt:analyze"
    )
    assert posted["headers"]["Ocp-Apim-Subscription-Key"] == "test-key"
    assert posted["json"]["base64Source"] == base64.b64encode(b"receipt-bytes").decode("ascii")
    assert result.merchant == "Supermercato"
    assert result.spent_on == date(2026, 10, 20)
    assert result.amount == Decimal("12.5")
    assert result.tax_amount == Decimal("1.2")
    assert result.payment_method == "Carta"
    assert result.description == "Pane, Sconto"
    assert result.items[0].description == "Pane"
    assert result.items[0].quantity == Decimal("2")
    assert result.items[0].total == Decimal("2.5")
    assert result.items[1].description == "Sconto"
    assert result.items[1].unit_price == Decimal("-1.0")
    assert result.items[1].total == Decimal("-1.0")
    assert poll_count == 2


def test_azure_document_intelligence_rejects_non_azure_poll_url(monkeypatch):
    class FakeResponse:
        headers = {"Operation-Location": "https://attacker.example/collect"}

        def raise_for_status(self):
            return None

    monkeypatch.setattr(ai.settings, "azure_document_intelligence_endpoint",
                        "https://example.cognitiveservices.azure.com")
    monkeypatch.setattr(ai.settings, "azure_document_intelligence_key", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        httpx, "get", lambda *args, **kwargs: pytest.fail("must not send the key to another host")
    )

    with pytest.raises(RuntimeError, match="URL di polling non valido"):
        ai.AzureDocumentIntelligenceExtractor().extract(b"receipt-bytes", "image/jpeg")


def test_azure_document_intelligence_enforces_free_tier_file_size(monkeypatch):
    monkeypatch.setattr(ai.settings, "azure_document_intelligence_endpoint",
                        "https://example.cognitiveservices.azure.com")
    monkeypatch.setattr(ai.settings, "azure_document_intelligence_key", "test-key")
    monkeypatch.setattr(
        httpx, "post", lambda *args, **kwargs: pytest.fail("oversized document must not be sent")
    )

    with pytest.raises(ValueError, match="piano Azure F0"):
        ai.AzureDocumentIntelligenceExtractor().extract(
            b"x" * (4 * 1024 * 1024 + 1), "application/pdf"
        )
