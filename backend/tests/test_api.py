from datetime import date
from io import BytesIO
from decimal import Decimal

import pyotp
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.models import Expense, Household, User, UserSession
from app.schemas import ReceiptExtraction
from app.security import SESSION_COOKIE, create_session


class StubExtractor:
    def extract(self, content: bytes, content_type: str) -> ReceiptExtraction:
        return ReceiptExtraction(merchant="Market", amount=Decimal("10.00"), currency="EUR")


def test_auth_mfa_upload_expenses_and_reports_are_household_scoped(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main.settings, "secure_cookies", False)
    monkeypatch.setattr(main.settings, "storage_dir", str(tmp_path / "documents"))
    monkeypatch.setattr("app.routers.expenses.get_extractor", lambda: StubExtractor())

    try:
        with TestClient(main.app) as owner, TestClient(main.app) as other:
            registered = owner.post("/api/auth/register", json={
                "email": "owner@example.com", "password": "a-secure-test-password",
                "display_name": "Owner", "household_name": "Home",
            })
            assert registered.status_code == 201, registered.text
            csrf = registered.json()["csrf_token"]

            assert owner.post("/api/expenses", json={}, headers={"X-CSRF-Token": "wrong"}).status_code == 403

            setup = owner.post("/api/auth/mfa/setup", headers={"X-CSRF-Token": csrf})
            assert setup.status_code == 200
            code = pyotp.TOTP(setup.json()["secret"]).now()
            enabled = owner.post("/api/auth/mfa/enable", json={"code": code},
                                 headers={"X-CSRF-Token": csrf})
            assert enabled.status_code == 200
            recovery_code = enabled.json()["recovery_codes"][0]

            owner.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
            logged_in = owner.post("/api/auth/login", json={
                "email": "owner@example.com", "password": "a-secure-test-password",
                "totp_code": pyotp.TOTP(setup.json()["secret"]).now(),
            })
            assert logged_in.status_code == 200
            csrf = logged_in.json()["csrf_token"]

            categories = owner.get("/api/categories").json()
            category_id = next(category["id"] for category in categories if category["name"] == "Casa")
            tag_response = owner.post("/api/tags", json={
                "name": "Ricorrente", "category_ids": [category_id],
            }, headers={"X-CSRF-Token": csrf})
            assert tag_response.status_code == 201, tag_response.text
            tag_id = tag_response.json()["id"]
            assert tag_response.json()["category_ids"] == [category_id]
            second_category = owner.post("/api/categories", json={"name": "Test Categoria"},
                                         headers={"X-CSRF-Token": csrf})
            assert second_category.status_code == 201
            linked = owner.put(f"/api/tags/{tag_id}", json={
                "name": "Ricorrente", "category_ids": [category_id, second_category.json()["id"]],
            }, headers={"X-CSRF-Token": csrf})
            assert linked.status_code == 200, linked.text
            assert set(linked.json()["category_ids"]) == {category_id, second_category.json()["id"]}
            image_bytes = BytesIO()
            Image.new("RGB", (2, 2), "white").save(image_bytes, format="PNG")
            upload = owner.post("/api/documents/extract",
                                files={"file": ("receipt.png", image_bytes.getvalue(), "image/png")},
                                headers={"X-CSRF-Token": csrf})
            assert upload.status_code == 200
            document_key = upload.json()["document_key"]
            for _ in range(9):
                accepted = owner.post(
                    "/api/documents/extract",
                    files={"file": ("receipt.png", image_bytes.getvalue(), "image/png")},
                    headers={"X-CSRF-Token": csrf},
                )
                assert accepted.status_code == 200
            limited = owner.post(
                "/api/documents/extract",
                files={"file": ("receipt.png", image_bytes.getvalue(), "image/png")},
                headers={"X-CSRF-Token": csrf},
            )
            assert limited.status_code == 429
            assert "10 analisi OCR all'ora" in limited.json()["detail"]

            expense_payload = {
                "amount": "10.00", "currency": "EUR", "spent_on": "2026-01-05",
                "merchant": "Market", "description": "Food", "category_id": category_id,
                "tag_ids": [tag_id],
                "visibility": "shared", "review_status": "reviewed", "document_key": document_key,
                "items": [{"description": "Bread", "quantity": "1", "unit_price": "2.50", "total": "2.50"}],
            }
            created = owner.post("/api/expenses", json=expense_payload,
                                 headers={"X-CSRF-Token": csrf})
            assert created.status_code == 201
            assert created.json()["items"][0]["description"] == "Bread"
            assert created.json()["tag_ids"] == [tag_id]
            assert owner.get(f"/api/documents/{document_key}").status_code == 200

            personal = {**expense_payload, "amount": "5.00", "merchant": "Private",
                        "visibility": "personal", "document_key": None, "items": []}
            assert owner.post("/api/expenses", json=personal,
                              headers={"X-CSRF-Token": csrf}).status_code == 201
            invite_response = owner.post("/api/auth/household/invite",
                                         headers={"X-CSRF-Token": csrf})
            assert invite_response.status_code == 200
            invite_token = invite_response.json()["token"]
            result = owner.get("/api/reports", params={
                "start": "2026-01-01", "end": "2026-01-31", "currency": "EUR",
            })
            assert result.status_code == 200
            assert Decimal(str(result.json()["total"])) == Decimal("15.00")

            other_registration = other.post("/api/auth/register", json={
                "email": "other@example.com", "password": "another-secure-password",
                "display_name": "Other", "household_name": "Other home",
            })
            assert other_registration.status_code == 201
            assert other.get("/api/expenses").json() == []
            assert other.get("/api/documents/" + document_key).status_code == 404

            family_member = other.post("/api/auth/register", json={
                "email": "member@example.com", "password": "member-secure-password-123",
                "display_name": "Member", "invite_token": invite_token,
            })
            assert family_member.status_code == 201
            family_expenses = other.get("/api/expenses").json()
            assert [item["merchant"] for item in family_expenses] == ["Market"]
            assert other.get("/api/documents/" + document_key).status_code == 200
            assert Decimal(str(other.get("/api/reports", params={
                "start": "2026-01-01", "end": "2026-01-31", "currency": "EUR",
            }).json()["total"])) == Decimal("10.00")
            reused_invite = other.post("/api/auth/register", json={
                "email": "third@example.com", "password": "third-secure-password-123",
                "display_name": "Third", "invite_token": invite_token,
            })
            assert reused_invite.status_code == 400

            owner.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
            recovered = owner.post("/api/auth/login", json={
                "email": "owner@example.com", "password": "a-secure-test-password",
                "totp_code": recovery_code,
            })
            assert recovered.status_code == 200
            assert owner.post("/api/auth/logout", headers={
                "X-CSRF-Token": recovered.json()["csrf_token"],
            }).status_code == 200
            assert owner.post("/api/auth/login", json={
                "email": "owner@example.com", "password": "a-secure-test-password",
                "totp_code": recovery_code,
            }).status_code == 401
    finally:
        main.app.dependency_overrides.clear()
        engine.dispose()


def test_household_admin_can_manage_members_and_preserve_expenses(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine)
    household = Household(name="Home")
    db.add(household)
    db.flush()
    admin = User(email="admin@example.com", password_hash="hash", display_name="Admin",
                 household_id=household.id, is_admin=True)
    member = User(email="member@example.com", password_hash="hash", display_name="Member",
                  household_id=household.id, is_admin=False)
    db.add_all([admin, member])
    db.flush()
    expense = Expense(
        household_id=household.id, created_by=member.id, amount=Decimal("12.50"),
        currency="EUR", spent_on=date(2026, 1, 5), merchant="Market",
        visibility="shared", review_status="reviewed",
        document_key="0123456789abcdef0123456789abcdef.jpg",
        items=[{"description": "Discount", "quantity": "1",
                "unit_price": "-1.00", "total": "-1.00"}],
    )
    db.add(expense)
    db.commit()
    admin_id, member_id, expense_id = admin.id, member.id, expense.id
    admin_token, admin_csrf = create_session(db, admin)
    member_token, member_csrf = create_session(db, member)

    def override_db():
        yield db

    main.app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main.settings, "secure_cookies", False)
    try:
        with TestClient(main.app) as admin_client, TestClient(main.app) as member_client:
            admin_client.cookies.set(SESSION_COOKIE, admin_token)
            member_client.cookies.set(SESSION_COOKIE, member_token)
            admin_headers = {"X-CSRF-Token": admin_csrf}
            member_headers = {"X-CSRF-Token": member_csrf}

            category_response = admin_client.post(
                "/api/categories",
                json={"name": "Animali", "color": "#aabbcc"},
                headers=admin_headers,
            )
            assert category_response.status_code == 201
            expense.category_id = category_response.json()["id"]
            db.commit()
            assert admin_client.delete(
                f"/api/categories/{expense.category_id}", headers=admin_headers
            ).status_code == 204
            db.refresh(expense)
            assert expense.category_id is None
            assert expense.id == expense_id

            assert admin_client.get("/api/household/members").status_code == 200
            assert member_client.get("/api/household/members").status_code == 403
            assert admin_client.post("/api/auth/household/invite",
                                     headers=admin_headers).status_code == 200
            assert member_client.post("/api/auth/household/invite",
                                      headers=member_headers).status_code == 403

            updated = admin_client.put(
                f"/api/household/members/{member_id}",
                json={"display_name": "Membro aggiornato", "email": "updated@example.com"},
                headers=admin_headers,
            )
            assert updated.status_code == 200
            assert updated.json()["display_name"] == "Membro aggiornato"
            assert updated.json()["email"] == "updated@example.com"

            assert admin_client.delete(
                f"/api/household/members/{admin_id}", headers=admin_headers
            ).status_code == 400
            assert admin_client.delete(
                f"/api/household/members/{member_id}", headers=admin_headers
            ).status_code == 204
            assert db.get(User, member_id) is None
            preserved = db.get(Expense, expense_id)
            assert preserved is not None
            assert preserved.created_by == admin_id
            assert preserved.document_key == "0123456789abcdef0123456789abcdef.jpg"
            assert preserved.items[0]["total"] == "-1.00"
            assert db.query(UserSession).filter_by(user_id=member_id).count() == 0
    finally:
        main.app.dependency_overrides.clear()
        db.close()
        engine.dispose()
