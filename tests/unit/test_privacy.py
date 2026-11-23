"""Detection and redaction of personal or secret data before it reaches Habitica."""

from __future__ import annotations

import pytest

from habitica_mcp.privacy import find_sensitive, redact, redact_body, scan_body


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Llamar a la doctora al 300 123 4567", "a phone number"),
        ("WhatsApp +57 3001234567", "a phone number"),
        ("Call (601) 234-5678 ext", "a phone number"),
        ("Escribir a juan.perez@gmail.com", "an email address"),
        ("Pagar tarjeta 4111 1111 1111 1111", "a card number"),
        ("Cédula 1.023.456.789", "an identity document number"),
        ("passport: X1234567", "an identity document number"),
        ("Cuenta de ahorros 123-456789-01", "an account number"),
        ("IBAN ES91 2100 0418 4502 0005 1332", "an IBAN"),
        ("wifi clave: perro123", "a password or secret"),
        ("mi contraseña es Hunter2!", "a password or secret"),
        ("token=abc123def", "a password or secret"),
        ("key sk-abcdefghijklmnop1234", "an API key or access token"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123", "an API key or access token"),
        ("https://drive.example.com/file?id=1&access_token=xyz", "a link with an access key"),
        ("https://example.com/s?sig=abc123", "a link with an access key"),
    ],
)
def test_sensitive_data_is_detected(text: str, kind: str) -> None:
    assert [finding.kind for finding in find_sensitive(text)] == [kind]


@pytest.mark.parametrize(
    "text",
    [
        "Reto 150: flexiones, abdominales, sentadillas y lazo",
        "Series 10 12 15 20 25",
        "Pagar $1.500.000 el 2026-11-28",
        "Rutina 6:00-10:00 PM",
        "Swing 3x15, plancha 3x30 s",
        "La clave para el éxito es dormir bien",
        "Pedido 4512 de la app de domicilios",
        "Llamar al 123",
        "Leer el capítulo 12 del libro",
        "Descuento del 15% por función pública",
        "Reunión en la sala 402 a las 14:30",
        "Token economy reading list",
    ],
)
def test_everyday_task_text_passes(text: str) -> None:
    assert find_sensitive(text) == []


def test_findings_are_masked() -> None:
    (finding,) = find_sensitive("Llamar al 300 123 4567")
    assert finding.masked == "***67"
    assert "4567" not in finding.masked


def test_redact_replaces_only_the_sensitive_part() -> None:
    text = "Llamar al 300 123 4567 o escribir a ana@example.com"
    assert redact(text) == "Llamar al [redacted] o escribir a [redacted]"


def test_bodies_are_scanned_field_by_field() -> None:
    body = {
        "text": "Llamar al banco",
        "notes": "Cuenta 123-456789-01",
        "checklist": [{"text": "Clave: x9!abc"}, {"text": "Preguntar horario"}],
        "priority": 1,
    }

    findings = [(field, finding.kind) for field, finding in scan_body(body)]

    assert findings == [("notes", "an account number"), ("checklist", "a password or secret")]
    assert redact_body(body) == {
        "text": "Llamar al banco",
        "notes": "[redacted]",
        "checklist": [{"text": "[redacted]"}, {"text": "Preguntar horario"}],
        "priority": 1,
    }
