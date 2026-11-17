from __future__ import annotations

from pmagent.evidence.redaction import Redactor


def test_redacts_coordinates() -> None:
    redactor = Redactor()
    result = redactor.redact("用户位置 39.9042, 116.4074 附近丢瓦片")
    assert "39.9042" not in result.text
    assert "116.4074" not in result.text
    assert "<COORD:1>" in result.text
    assert "coord" in result.rules_applied


def test_redacts_device_id_keeping_name() -> None:
    result = Redactor().redact("device_id=abc123def456 crashed")
    assert result.text == "device_id=<DEVICE:1> crashed"


def test_redacts_api_key() -> None:
    result = Redactor().redact("api_key=sk-1234567890abcdef")
    assert result.text == "api_key=<TOKEN:1>"
    assert "credential" in result.rules_applied


def test_redacts_bearer_token() -> None:
    result = Redactor().redact("Authorization: Bearer abcdefghijklmnop123456")
    assert "abcdefghijklmnop123456" not in result.text
    assert "<TOKEN:1>" in result.text


def test_redacts_windows_path() -> None:
    result = Redactor().redact(r"fatal at C:\Users\alice\work\src\a.cc:12")
    assert "alice" not in result.text
    assert "<PATH:1>" in result.text


def test_redacts_posix_home_path() -> None:
    result = Redactor().redact("open /home/bob/work/b.cc failed")
    assert "bob" not in result.text
    assert "<PATH:1>" in result.text


def test_restore_returns_original() -> None:
    redactor = Redactor()
    original = "device_id=abc123def456 coords 39.9042,116.4074"
    result = redactor.redact(original)
    assert redactor.restore(result.text) == original


def test_redaction_is_idempotent() -> None:
    redactor = Redactor()
    once = redactor.redact("api_key=abcdef123456").text
    twice = redactor.redact(once).text
    assert once == twice


def test_disabled_redactor_passthrough() -> None:
    text = "api_key=abcdef123456"
    result = Redactor(enabled=False).redact(text)
    assert result.text == text
    assert not result.changed


def test_double_point_helpers() -> None:
    redactor = Redactor()
    assert "<DEVICE:1>" in redactor.redact_for_storage("device_id=abc123def456").text
    assert "<DEVICE:2>" in redactor.redact_for_egress("device_id=zzz999yyy888").text
