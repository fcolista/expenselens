from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.database import Base, get_db
from app.models import Category, Expense, TelegramUser, User
from app.notifications import budget_alerts, due_summaries
from app.telegram_i18n import MESSAGES, from_telegram, tr


def make_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def register(client, email="a@example.com"):
    reply = client.post("/api/auth/register", json={
        "email": email, "password": "a-secure-test-password",
        "display_name": "A", "household_name": "Home"})
    assert reply.status_code == 201, reply.text
    return reply.json()["csrf_token"]


def test_catalogs_have_same_keys_and_language_detection():
    assert set(MESSAGES["it"]) == set(MESSAGES["en"])
    assert from_telegram("en-GB") == "en" and from_telegram("it") == "it" and from_telegram(None) == "it"
    assert "12.50" in tr("en", "saved", amount="12.50", currency="EUR")


def test_sessions_budgets_and_rates(monkeypatch):
    engine = make_engine()

    def override_db():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main.settings, "secure_cookies", False)
    try:
        with TestClient(main.app) as first, TestClient(main.app) as second:
            csrf = register(first)
            first_headers = {"X-CSRF-Token": csrf}
            login = second.post("/api/auth/login", json={
                "email": "a@example.com", "password": "a-secure-test-password"},
                headers={"User-Agent": "Phone"})
            assert login.status_code == 200

            sessions = first.get("/api/auth/sessions").json()
            assert len(sessions) == 2
            assert sum(item["current"] for item in sessions) == 1
            other = next(item for item in sessions if not item["current"])
            assert other["user_agent"] == "Phone"
            assert first.delete(f"/api/auth/sessions/{other['id']}", headers=first_headers).status_code == 204
            assert second.get("/api/auth/me").status_code == 401
            assert first.delete("/api/auth/sessions/nope", headers=first_headers).status_code == 404

            category = next(c for c in first.get("/api/categories").json() if c["name"] == "Casa")
            budget = first.put(f"/api/categories/{category['id']}/budget", json={"monthly_budget": "100"},
                               headers=first_headers)
            assert budget.status_code == 200 and Decimal(budget.json()["monthly_budget"]) == 100
            for amount, currency in (("60.00", "EUR"), ("10.00", "USD")):
                created = first.post("/api/expenses", json={
                    "amount": amount, "currency": currency, "spent_on": date.today().isoformat(),
                    "merchant": "Shop", "category_id": category["id"]}, headers=first_headers)
                assert created.status_code == 201, created.text
            status = first.get("/api/budgets").json()["items"][0]
            assert Decimal(status["spent"]) == 60

            assert first.put("/api/rates/USD", json={"rate": "0.5"}, headers=first_headers).status_code == 200
            assert first.put("/api/rates/EUR", json={"rate": "1"}, headers=first_headers).status_code == 422
            status = first.get("/api/budgets").json()["items"][0]
            assert Decimal(status["spent"]) == 65
            total = first.get("/api/rates/total", params={
                "start": date.today().replace(day=1).isoformat(), "end": date.today().isoformat()}).json()
            assert Decimal(total["total"]) == 65 and total["missing_rates"] == []
            assert first.post("/api/rates/refresh", headers=first_headers).status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_telegram_preferences_summaries_and_budget_alerts():
    engine = make_engine()
    with Session(engine) as db:
        from app.models import Household
        household = Household(name="Home")
        db.add(household)
        db.flush()
        user = User(email="a@example.com", password_hash="x", display_name="A",
                    household_id=household.id, is_admin=True)
        db.add(user)
        db.flush()
        category = Category(household_id=household.id, name="Casa", monthly_budget=Decimal("100"))
        db.add(category)
        db.add(TelegramUser(telegram_id=1, user_id=user.id, language="en", notify_period="weekly"))
        db.flush()
        today = date.today()
        db.add(Expense(household_id=household.id, created_by=user.id, amount=Decimal("85"),
                       spent_on=today, merchant="Shop", category_id=category.id))
        db.commit()

        alerts = budget_alerts(db, today)
        assert len(alerts) == 1 and "85%" in alerts[0][1]
        assert budget_alerts(db, today) == []
        db.add(Expense(household_id=household.id, created_by=user.id, amount=Decimal("30"),
                       spent_on=today, merchant="Shop", category_id=category.id))
        db.commit()
        alerts = budget_alerts(db, today)
        assert len(alerts) == 1 and "exceeded" in alerts[0][1]

        now = datetime.combine(today, datetime.min.time()).replace(hour=9)
        assert due_summaries(db, now, 8) == []  # first activation only records the period
        link = db.get(TelegramUser, 1)
        assert link.last_summary_key
        link.last_summary_key = "W2000-01"
        db.commit()
        sent = due_summaries(db, now, 8)
        assert len(sent) == 1 and sent[0][0] == 1 and "Weekly summary" in sent[0][1]
        assert due_summaries(db, now, 8) == []
        assert due_summaries(db, now.replace(hour=3) + timedelta(days=7), 8) == []


def test_change_password(monkeypatch):
    main.app.state.limiter.reset()
    engine = make_engine()

    def override_db():
        with Session(engine) as session:
            yield session

    main.app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(main.settings, "secure_cookies", False)
    old, new = "a-secure-test-password", "another-secure-password"
    try:
        with TestClient(main.app) as first, TestClient(main.app) as second:
            csrf = register(first)
            headers = {"X-CSRF-Token": csrf}
            assert second.post("/api/auth/login", json={"email": "a@example.com", "password": old}).status_code == 200

            wrong = first.post("/api/auth/password", headers=headers,
                               json={"current_password": "nope-nope-nope", "new_password": new})
            assert wrong.status_code == 400
            same = first.post("/api/auth/password", headers=headers,
                              json={"current_password": old, "new_password": old})
            assert same.status_code == 422
            short = first.post("/api/auth/password", headers=headers,
                               json={"current_password": old, "new_password": "short"})
            assert short.status_code == 422

            ok = first.post("/api/auth/password", headers=headers,
                            json={"current_password": old, "new_password": new})
            assert ok.status_code == 200 and ok.json() == {"revoked": 1}
            assert first.get("/api/auth/me").status_code == 200
            assert second.get("/api/auth/me").status_code == 401
            with TestClient(main.app) as third:
                assert third.post("/api/auth/login", json={"email": "a@example.com", "password": old}).status_code == 401
                assert third.post("/api/auth/login", json={"email": "a@example.com", "password": new}).status_code == 200
    finally:
        main.app.dependency_overrides.clear()
