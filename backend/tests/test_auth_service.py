import asyncio
import base64
import os
from urllib.parse import parse_qs, urlparse

import pytest

from app.auth.memory_repo import MemoryAuthRepository
from app.auth.service import AuthService
from app.config import Settings
from app.errors import BadRequest, Forbidden, TooManyRequests, Unauthorized

KEY = base64.b64encode(os.urandom(32)).decode()


class FakeMailer:
    def __init__(self):
        self.verify, self.reset = [], []

    async def send_verification(self, to, link):
        self.verify.append((to, link))

    async def send_password_reset(self, to, link):
        self.reset.append((to, link))


def token_of(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def make(**overrides):
    values = dict(CRM_DATABASE_URL="postgresql://x/y", CRM_MASTER_KEY=KEY, CRM_JWT_SECRET="j" * 40,
                  CRM_REFRESH_REUSE_GRACE_SECONDS=0)
    values.update(overrides)
    settings = Settings(_env_file=None, **values)
    repo, mailer = MemoryAuthRepository(), FakeMailer()
    return AuthService(repo, settings, mailer), repo, mailer


PW = "correct horse battery"


async def register_and_verify(svc, mailer, email="a@example.com"):
    await svc.register(email=email, password=PW, display_name="A", workspace_name="Acme", ip="1.1.1.1", user_agent="t")
    await svc.verify_email(token_of(mailer.verify[-1][1]), "1.1.1.1")


def run(coro):
    return asyncio.run(coro)


def test_register_verify_login_and_me():
    async def go():
        svc, repo, mailer = make()
        await register_and_verify(svc, mailer)
        issued = await svc.login(email="a@example.com", password=PW, ip="1.1.1.1", user_agent="t")
        claims = await svc.authenticate_access(issued.access_token)
        assert claims.tenant_id == issued.tenant_id
    run(go())


def test_login_blocked_until_email_verified():
    async def go():
        svc, _, _ = make()
        await svc.register(email="a@example.com", password=PW, display_name="", workspace_name="W", ip="i", user_agent="t")
        with pytest.raises(Forbidden):
            await svc.login(email="a@example.com", password=PW, ip="i", user_agent="t")
    run(go())


def test_duplicate_registration_is_silent_and_weak_password_rejected():
    async def go():
        svc, _, mailer = make()
        await register_and_verify(svc, mailer)
        await svc.register(email="a@example.com", password=PW, display_name="", workspace_name="W", ip="i", user_agent="t")
        assert len(mailer.verify) == 1
        with pytest.raises(BadRequest):
            await svc.register(email="b@example.com", password="short", display_name="", workspace_name="W", ip="i", user_agent="t")
    run(go())


def test_lockout_after_repeated_failures_even_with_right_password():
    async def go():
        svc, _, mailer = make(CRM_LOGIN_MAX_FAILURES=3)
        await register_and_verify(svc, mailer)
        for _ in range(3):
            with pytest.raises(Unauthorized):
                await svc.login(email="a@example.com", password="wrong-password-1", ip="i", user_agent="t")
        with pytest.raises(TooManyRequests):
            await svc.login(email="a@example.com", password=PW, ip="i", user_agent="t")
    run(go())


def test_refresh_rotates_and_reuse_revokes_session():
    async def go():
        svc, _, mailer = make()
        await register_and_verify(svc, mailer)
        first = await svc.login(email="a@example.com", password=PW, ip="i", user_agent="t")
        second = await svc.refresh(first.refresh_token, "i")
        assert second.refresh_token != first.refresh_token
        with pytest.raises(Unauthorized):  # replay of the old token
            await svc.refresh(first.refresh_token, "i")
        with pytest.raises(Unauthorized):  # the whole session is now dead, including the new token
            await svc.refresh(second.refresh_token, "i")
        with pytest.raises(Unauthorized):
            await svc.authenticate_access(second.access_token)
    run(go())


def test_logout_revokes_access_token():
    async def go():
        svc, _, mailer = make()
        await register_and_verify(svc, mailer)
        issued = await svc.login(email="a@example.com", password=PW, ip="i", user_agent="t")
        await svc.logout(issued.refresh_token, None)
        with pytest.raises(Unauthorized):
            await svc.authenticate_access(issued.access_token)
    run(go())


def test_password_reset_revokes_sessions_and_token_is_single_use():
    async def go():
        svc, _, mailer = make()
        await register_and_verify(svc, mailer)
        issued = await svc.login(email="a@example.com", password=PW, ip="i", user_agent="t")
        await svc.request_reset("a@example.com", "i")
        token = token_of(mailer.reset[-1][1])
        await svc.reset_password(token, "a brand new passphrase", "i")
        with pytest.raises(Unauthorized):
            await svc.authenticate_access(issued.access_token)
        with pytest.raises(BadRequest):
            await svc.reset_password(token, "another passphrase here", "i")
        await svc.login(email="a@example.com", password="a brand new passphrase", ip="i", user_agent="t")
    run(go())


def test_reset_request_for_unknown_email_sends_nothing():
    async def go():
        svc, _, mailer = make()
        await svc.request_reset("nobody@example.com", "i")
        assert mailer.reset == []
    run(go())
