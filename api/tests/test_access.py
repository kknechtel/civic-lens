"""Private deployments (access.mode: invite_only): the API gate and signup rules."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ACCESS_MODE", "invite_only")
    import municipality
    municipality.get_municipality.cache_clear()
    import main
    yield TestClient(main.app)
    monkeypatch.delenv("ACCESS_MODE")
    municipality.get_municipality.cache_clear()


def test_long_beach_profile_is_private():
    from municipality import load_deployment
    assert load_deployment("long-beach-ny").access.mode == "invite_only"
    assert load_deployment("atlantic-highlands-nj").access.mode == "approval"


def test_anonymous_requests_are_refused(client):
    for path in ("/api/documents", "/api/search/facets", "/api/integrity/flags", "/api/presentations/public/x"):
        r = client.get(path)
        assert r.status_code == 401, (path, r.status_code)


def test_bad_token_refused(client):
    r = client.get("/api/documents", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_sign_in_surface_stays_open(client):
    r = client.get("/api/municipality")
    assert r.status_code == 200 and r.json()["access_mode"] == "invite_only"
    # Wrong credentials are rejected by the login route itself, not the gate.
    r = client.post("/api/auth/login", json={"email": "nobody@example.org", "password": "y"})
    assert r.status_code == 401 and r.json()["detail"] != "Sign in required"


def test_signup_closed(client):
    r = client.post("/api/auth/signup", json={"email": "new@example.org", "password": "secret123"})
    assert r.status_code == 403 and "invite-only" in r.json()["detail"]


def test_active_users_pass_inactive_refused(client):
    import uuid

    from auth import create_access_token
    try:
        from database import SessionLocal, init_db
        init_db()
        db = SessionLocal()
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"postgres unavailable: {exc}")
    from models.user import User
    tag = uuid.uuid4().hex[:8]
    u = User(email=f"a{tag}@example.org", username=f"a{tag}", is_active=True, is_admin=False)
    db.add(u)
    db.commit()
    try:
        tok = {"Authorization": f"Bearer {create_access_token({'sub': str(u.id)})}"}
        assert client.get("/api/documents", headers=tok).status_code == 200
        u.is_active = False
        db.commit()
        assert client.get("/api/documents", headers=tok).status_code == 403
    finally:
        db.delete(u)
        db.commit()
        db.close()
