"""
BCP parser tests against real anonymized fixtures.
Each fixture must produce the expected ParsedEmail.
"""
import email
import email.header
from decimal import Decimal
from pathlib import Path

import pytest

from app.parsers.bcp import parse_bcp

FIXTURES = Path(__file__).parent / "fixtures" / "bcp"


def _load(path: Path) -> tuple[str, str, str]:
    """Return (decoded_html, decoded_subject, message_id) from an .eml file."""
    msg = email.message_from_bytes(path.read_bytes())

    # Subject
    subject = str(email.header.make_header(email.header.decode_header(msg["Subject"])))

    # Message-ID
    message_id = (msg["Message-ID"] or "").strip().strip("<>")

    # HTML body — walk multipart or read single part
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="replace")
                break
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        html = payload.decode(charset, errors="replace")

    return html, subject, message_id


# ── Credit ─────────────────────────────────────────────────────────────────────

class TestCredit:
    def test_panificadora(self):
        html, subject, mid = _load(
            FIXTURES / "credit" /
            "Realizaste un consumo con tu Tarjeta de Crédito BCP - Servicio de Notificaciones BCP.eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp is not None
        assert exp.monto == Decimal("13.00")
        assert "PANIFICADORA" in exp.concepto.upper()
        assert exp.modalidad == "credito"
        assert exp.moneda == "PEN"
        assert exp.fuente == "auto"

    def test_pedidosya(self):
        html, subject, mid = _load(
            FIXTURES / "credit" /
            "Realizaste un consumo con tu Tarjeta de Crédito BCP - Servicio de Notificaciones BCP (1).eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp.monto == Decimal("43.90")
        assert "PEDIDOSYA" in exp.concepto.upper() or "Superpet" in exp.concepto
        assert exp.modalidad == "credito"

    def test_rutas_norteas(self):
        html, subject, mid = _load(
            FIXTURES / "credit" /
            "Realizaste un consumo con tu Tarjeta de Crédito BCP - Servicio de Notificaciones BCP (2).eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp.monto == Decimal("20.00")
        assert "RUTAS" in exp.concepto.upper()
        assert exp.modalidad == "credito"


# ── Debit / Plin ───────────────────────────────────────────────────────────────

class TestDebit:
    def test_plin_450(self):
        html, subject, mid = _load(
            FIXTURES / "debit" /
            "Realizaste un consumo con tu Tarjeta de Débito BCP - Servicio de Notificaciones BCP.eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp.monto == Decimal("450.00")
        assert exp.modalidad == "plin"
        assert "SARA" in exp.concepto.upper()
        assert not exp.concepto.upper().startswith("PLIN-")

    def test_plin_400(self):
        html, subject, mid = _load(
            FIXTURES / "debit" /
            "Realizaste un consumo con tu Tarjeta de Débito BCP - Servicio de Notificaciones BCP (1).eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp.monto == Decimal("400.00")
        assert exp.modalidad == "plin"


# ── Transfers ──────────────────────────────────────────────────────────────────

class TestTransfer:
    def test_terceros(self):
        html, subject, mid = _load(
            FIXTURES / "transfer" /
            "Constancia de Transferencia a Terceros BCP - Servicio de Notificaciones BCP.eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp.monto == Decimal("3280.64")
        assert exp.modalidad == "transferencia"
        assert exp.concepto  # beneficiary name present

    def test_pago_tc(self):
        html, subject, mid = _load(
            FIXTURES / "transfer" /
            "Constancia de Pago de Tarjeta de Crédito Propia - Servicio de Notificaciones BCP.eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp.monto == Decimal("2336.65")
        assert exp.modalidad == "transferencia"
        assert "TC" in exp.concepto
        assert "7581" in exp.concepto


# ── Yape ───────────────────────────────────────────────────────────────────────

class TestYape:
    def test_yape_sent(self):
        html, subject, mid = _load(
            FIXTURES / "yape_sent" /
            "Por tu seguridad, te notificaremos por cada yapeo que realices.eml"
        )
        result = parse_bcp(html, subject, mid)
        assert result is not None
        exp = result.expense
        assert exp.monto == Decimal("16.00")
        assert exp.modalidad == "yape"
        assert exp.concepto  # beneficiary name present
