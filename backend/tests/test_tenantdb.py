"""Stage 2: pure-logic tests for tenant database provisioning (no live database needed)."""
import asyncio

import pytest

from app import migrator
from app.tenantdb import provisioner
from app.tenantdb.provisioner import DatabaseSetupError, parse_url


def run(coro):
    return asyncio.run(coro)


# --- URL parsing ---------------------------------------------------------------------------
def test_parse_url_extracts_validated_parts():
    t = parse_url("postgresql://bot%40x:p%40ss@db.example.com:6543/mydb?sslmode=require")
    assert (t.host, t.port, t.user, t.password, t.database) == ("db.example.com", 6543, "bot@x", "p@ss", "mydb")
    assert t.sslmode == "require"


def test_parse_url_defaults_to_tls_and_default_port():
    t = parse_url("postgres://u:p@db.example.com/postgres")
    assert t.port == 5432 and t.sslmode == "require"


def test_parse_url_ignores_dangerous_query_params():
    t = parse_url("postgresql://u:p@db.example.com/d?host=127.0.0.1&sslrootcert=/etc/passwd&options=-c%20x")
    assert t.host == "db.example.com"


@pytest.mark.parametrize("bad", [
    "", "mysql://u:p@h/d", "postgresql://:p@h/d", "postgresql:///d", "not a url",
    "postgresql://u:p@h/d?sslmode=bogus", "postgresql://u:p@h/" + "x" * 3000,
])
def test_parse_url_rejects_bad_input(bad):
    with pytest.raises(DatabaseSetupError) as exc:
        parse_url(bad)
    assert exc.value.code == "invalid_url"


def test_error_messages_never_echo_the_password():
    with pytest.raises(DatabaseSetupError) as exc:
        parse_url("mysql://user:SuperSecret123@h/d")
    assert "SuperSecret123" not in exc.value.message


# --- SSRF guard ----------------------------------------------------------------------------
@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "localhost"])
def test_private_hosts_are_rejected(host):
    with pytest.raises(DatabaseSetupError) as exc:
        run(provisioner.resolve_public(host, 5432, allow_private=False))
    assert exc.value.code == "host_not_allowed"


def test_private_hosts_allowed_when_explicitly_enabled():
    assert "127.0.0.1" in run(provisioner.resolve_public("127.0.0.1", 5432, allow_private=True))


def test_plain_tls_off_is_refused_for_public_mode():
    t = parse_url("postgresql://u:p@127.0.0.1/d?sslmode=disable")
    with pytest.raises(DatabaseSetupError) as exc:
        run(provisioner.connect(t, allow_private=False))
    assert exc.value.code in ("host_not_allowed", "ssl_required")


# --- Migrations ----------------------------------------------------------------------------
def test_tenant_migrations_are_ordered_unique_and_checksummed():
    ms = migrator.load_migrations(provisioner.TENANT_MIGRATIONS)
    assert ms, "no tenant migrations found"
    versions = [m.version for m in ms]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)
    assert all(m.checksum and m.sql.strip() for m in ms)


def test_manual_sql_fallback_contains_every_migration():
    bundle = provisioner.manual_sql()
    for m in migrator.load_migrations(provisioner.TENANT_MIGRATIONS):
        assert m.version in bundle
    assert provisioner.latest_version() in bundle


def test_preflight_report_shape():
    p = provisioner.Preflight(problems=["x"])
    assert p.ok is False and p.as_dict()["problems"] == ["x"]
    assert provisioner.Preflight().ok is True
