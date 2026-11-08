from digdown.redactor import Redactor, SecretRegistry, MASK


def test_registered_secret_is_masked_even_inside_a_log_line():
    reg = SecretRegistry()
    reg.register("hunter2supersecret")
    r = Redactor(reg)
    out = r.redact("DB connect failed for user=admin pass=hunter2supersecret retrying")
    assert "hunter2supersecret" not in out
    assert MASK in out


def test_trivial_values_are_not_registered():
    reg = SecretRegistry()
    reg.register("1")          # too short
    reg.register("true")       # common literal
    reg.register("")           # empty
    r = Redactor(reg)
    assert r.redact("flag=true count=1") == "flag=true count=1"


def test_assignment_patterns_are_masked():
    r = Redactor(SecretRegistry())
    out = r.redact("API_KEY=sk-abc123def456ghi789 and PASSWORD: p@ssw0rd-longish")
    assert "sk-abc123def456ghi789" not in out
    assert "p@ssw0rd-longish" not in out


def test_jwt_and_connection_string_masked():
    r = Redactor(SecretRegistry())
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123signaturepart"
    conn = "postgres://app:s3cretPassw0rd@db:5432/main"
    out = r.redact(f"token={jwt} url={conn}")
    assert jwt not in out
    assert "s3cretPassw0rd" not in out


def test_redact_counts_hits():
    reg = SecretRegistry()
    reg.register("topsecretvalue1")
    r = Redactor(reg)
    redacted, n = r.redact_counted("x=topsecretvalue1 y=topsecretvalue1")
    assert n >= 2
    assert "topsecretvalue1" not in redacted


def test_redact_handles_non_string():
    r = Redactor(SecretRegistry())
    assert r.redact(None) is None
    assert r.redact(137) == 137


def test_registry_from_env_mapping():
    reg = SecretRegistry()
    reg.register_env({"DB_HOST": "db", "DB_PASSWORD": "verysecretpw123", "PORT": "8080"})
    r = Redactor(reg)
    out = r.redact("config: db / verysecretpw123 / 8080")
    assert "verysecretpw123" not in out
    assert "db" in out          # short, non-sensitive-looking value kept
    assert "8080" in out


# ---- found in real use: masking must not eat container names ----

def _from_env_file(tmp_path, text):
    from digdown.main import _build_redactor
    env = tmp_path / ".env"
    env.write_text(text)
    return _build_redactor(str(tmp_path / "missing.yml"), str(env))


def test_ordinary_settings_in_an_env_file_stay_readable(tmp_path):
    redactor = _from_env_file(tmp_path, "APP_NAME=wiki\nMODE=server\nPOSTGRES_USER=wiki\n")
    assert redactor.redact("shop-wiki_server-1 failed") == "shop-wiki_server-1 failed"


def test_secret_settings_in_an_env_file_are_masked(tmp_path):
    redactor = _from_env_file(tmp_path, "DB_PASSWORD=correcthorse\nSTRIPE_KEY=plainvalue\n")
    assert "correcthorse" not in redactor.redact("login correcthorse")
    assert "plainvalue" not in redactor.redact("key plainvalue")


def test_a_random_looking_value_is_masked_whatever_its_name(tmp_path):
    redactor = _from_env_file(tmp_path, "WEBHOOK_ID=zq7fake0value9rnd3xw\n")
    assert "zq7fake0value9rnd3xw" not in redactor.redact("sent to zq7fake0value9rnd3xw")
