"""Who pays for AI: user keys, sponsored features, caps. Needs Postgres (skips without)."""
import uuid

import pytest


@pytest.fixture()
def db():
    try:
        from database import SessionLocal, init_db
        init_db()
        s = SessionLocal()
        s.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception as exc:  # no database in this environment
        pytest.skip(f"postgres unavailable: {exc}")
    from sqlalchemy import text
    s.execute(text("DELETE FROM ai_api_keys; DELETE FROM ai_settings; DELETE FROM llm_usage"))
    s.commit()
    yield s
    s.execute(text("DELETE FROM ai_api_keys; DELETE FROM ai_settings; DELETE FROM llm_usage"))
    s.commit()
    s.close()


@pytest.fixture()
def user(db):
    from models.user import User
    tag = uuid.uuid4().hex[:8]
    u = User(email=f"t{tag}@example.org", username=f"t{tag}", is_admin=False, is_active=True)
    db.add(u)
    db.commit()
    yield u
    db.delete(u)
    db.commit()


@pytest.fixture(autouse=True)
def no_env_keys(monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "VOYAGE_API_KEY"):
        monkeypatch.delenv(v, raising=False)


def test_keys_are_encrypted_and_masked(db):
    from services.ai import router as ai
    row = ai.store_key(db, "anthropic", "sk-ant-secret-value-1234")
    assert "secret" not in row.ciphertext and row.last4 == "1234"
    assert ai.deployment_key("anthropic", db) == "sk-ant-secret-value-1234"


def test_user_key_wins_and_user_pays(db, user):
    from services.ai import router as ai
    ai.store_key(db, "anthropic", "sk-ant-deployment-0000")
    ai.store_key(db, "gemini", "AIza-user-own-key-9999", user_id=user.id)
    r = ai.resolve("chat", user.id, db)
    assert (r.provider, r.payer, r.key) == ("gemini", "user", "AIza-user-own-key-9999")


def test_unsponsored_feature_needs_own_key(db, user):
    from services.ai import NoAIAccess, router as ai
    ai.store_key(db, "anthropic", "sk-ant-deployment-0000")
    with pytest.raises(NoAIAccess, match="own API key"):
        ai.resolve("chat", user.id, db)  # chat is not sponsored by default
    ai.set_setting(db, "sponsored", ["chat"])
    r = ai.resolve("chat", user.id, db)
    assert (r.payer, r.model) == ("deployment", "claude-haiku-5-5")  # never Opus by default


def test_user_model_choice_never_spends_deployment_money(db, user):
    from services.ai import router as ai
    ai.store_key(db, "anthropic", "sk-ant-deployment-0000")
    ai.set_setting(db, "sponsored", ["chat"])
    ai.set_setting(db, f"user_prefs:{user.id}", {"routes": {"chat": {"provider": "anthropic", "model": "claude-opus-5-5"}}})
    assert ai.resolve("chat", user.id, db).model == "claude-haiku-5-5"
    ai.store_key(db, "anthropic", "sk-ant-user-own-1111", user_id=user.id)
    r = ai.resolve("chat", user.id, db)
    assert (r.model, r.payer) == ("claude-opus-5-5", "user")


def test_caps_stop_sponsored_use(db, user):
    from services.ai import NoAIAccess, router as ai
    from services.usage import record_usage
    ai.store_key(db, "anthropic", "sk-ant-deployment-0000")
    ai.set_setting(db, "sponsored", ["chat"])
    ai.set_setting(db, "caps", {"user_monthly_usd": 1.0, "deployment_monthly_usd": 100})
    record_usage(db, source="chat", model="claude-haiku-5-5", estimated_cost_usd=1.5, user_id=user.id)
    with pytest.raises(NoAIAccess, match="allowance"):
        ai.resolve("chat", user.id, db)
    record_usage(db, source="chat", model="x", estimated_cost_usd=50, user_id=user.id, payer="user")
    ai.store_key(db, "gemini", "AIza-user-own-key-9999", user_id=user.id)
    assert ai.resolve("chat", user.id, db).payer == "user"  # own key ignores caps


def test_background_features_use_deployment_or_env(db, monkeypatch):
    from services.ai import NoAIAccess, router as ai
    with pytest.raises(NoAIAccess):
        ai.resolve("enrichment")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-env-key-0000")
    r = ai.resolve("enrichment")
    assert (r.provider, r.model, r.payer) == ("gemini", "gemini-2.5-flash-lite", "deployment")
