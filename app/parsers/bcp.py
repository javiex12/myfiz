"""
BCP and Yape email parser.

Assumes `html` is already decoded (QP/base64 decoding done by the caller).
All parsers return None on any extraction failure; caller logs the miss.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import structlog

from app.models import Expense, ParsedEmail

log = structlog.get_logger()
LIMA_TZ = ZoneInfo("America/Lima")

_MONTHS: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

# ── Public entry point ─────────────────────────────────────────────────────────

def parse_bcp(html: str, subject: str, message_id: str) -> ParsedEmail | None:
    """Try each parser in order. Returns None if no parser matches."""
    for fn in (
        _parse_credit,
        _parse_debit,
        _parse_transfer_terceros,
        _parse_pago_tc,
        _parse_yape,
    ):
        result = fn(html, subject, message_id)
        if result is not None:
            return result
    log.warning("bcp_no_parser_matched", subject=subject, message_id=message_id)
    return None


# ── Individual parsers ─────────────────────────────────────────────────────────

def _parse_credit(html: str, subject: str, message_id: str) -> ParsedEmail | None:
    if "consumo" not in subject.lower() or "rédito" not in subject:
        return None

    amount = _consumo_amount(html)
    merchant = _consumo_merchant(html, "rédito")
    ts = _bcp_date(html)

    if not (amount and merchant and ts):
        log.warning("bcp_credit_fields_missing", message_id=message_id)
        return None

    return _make_parsed(
        message_id=message_id,
        subject=subject,
        ts=ts,
        concepto=_clean_merchant(merchant),
        monto=amount,
        modalidad="credito",

    )


def _parse_debit(html: str, subject: str, message_id: str) -> ParsedEmail | None:
    if "consumo" not in subject.lower() or "bito" not in subject:
        return None

    amount = _consumo_amount(html)
    merchant = _consumo_merchant(html, "bito")
    ts = _bcp_date(html)

    if not (amount and merchant and ts):
        log.warning("bcp_debit_fields_missing", message_id=message_id)
        return None

    raw_merchant = _clean_merchant(merchant)

    # Plin payments appear as debit with a "PLIN-" prefix in the merchant name
    if raw_merchant.upper().startswith("PLIN-"):
        concepto = raw_merchant[5:].strip()
        modalidad = "plin"
    else:
        concepto = raw_merchant
        modalidad = "debito"

    return _make_parsed(
        message_id=message_id,
        subject=subject,
        ts=ts,
        concepto=concepto,
        monto=amount,
        modalidad=modalidad,

    )


def _parse_transfer_terceros(html: str, subject: str, message_id: str) -> ParsedEmail | None:
    if "Transferencia a Terceros" not in subject:
        return None

    amount_m = re.search(
        r"Realizaste una transferencia de\s*<b>S/\s*([\d,.]+)</b>",
        html, re.IGNORECASE,
    )
    dest_m = re.search(
        r"Enviado a\s*</td>\s*<td[^>]*>\s*<b>([^<]+)</b>",
        html, re.DOTALL,
    )
    ts = _bcp_date(html)

    if not (amount_m and dest_m and ts):
        log.warning("bcp_transfer_terceros_fields_missing", message_id=message_id)
        return None

    return _make_parsed(
        message_id=message_id,
        subject=subject,
        ts=ts,
        concepto=dest_m.group(1).strip(),
        monto=_amount(amount_m.group(1)),
        modalidad="transferencia",

    )


def _parse_pago_tc(html: str, subject: str, message_id: str) -> ParsedEmail | None:
    if "Pago de Tarjeta" not in subject:
        return None

    amount_m = re.search(
        r"Realizaste un pago a tu tarjeta de\s*<b>S/\s*([\d,.]+)</b>",
        html, re.IGNORECASE,
    )
    dest_m = re.search(
        r"Pagado a\s*</td>\s*<td[^>]*>.*?\*\*\*\*\s*(\d+)",
        html, re.DOTALL,
    )
    ts = _bcp_date(html)

    if not (amount_m and ts):
        log.warning("bcp_pago_tc_fields_missing", message_id=message_id)
        return None

    last4 = dest_m.group(1) if dest_m else "????"
    return _make_parsed(
        message_id=message_id,
        subject=subject,
        ts=ts,
        concepto=f"Pago TC {last4}",
        monto=_amount(amount_m.group(1)),
        modalidad="transferencia",

    )


def _parse_yape(html: str, subject: str, message_id: str) -> ParsedEmail | None:
    if "yapeo" not in subject.lower():
        return None

    amount_m = re.search(r"font-size:50px[^>]*>\s*([\d.]+)\s*</td>", html)
    beneficiary_m = re.search(
        r"Nombre del Beneficiario</td>\s*<td[^>]*>\s*([^<]+?)\s*</td>",
        html, re.DOTALL,
    )
    date_m = re.search(
        r"Fecha y Hora de la operaci[oó]n</td>\s*<td[^>]*>\s*([^<]+?)\s*</td>",
        html, re.DOTALL,
    )

    if not (amount_m and beneficiary_m and date_m):
        log.warning("bcp_yape_fields_missing", message_id=message_id)
        return None

    ts = _yape_date(date_m.group(1).strip())
    if not ts:
        log.warning("bcp_yape_date_parse_failed", message_id=message_id)
        return None

    return _make_parsed(
        message_id=message_id,
        subject=subject,
        ts=ts,
        concepto=beneficiary_m.group(1).strip(),
        monto=_amount(amount_m.group(1)),
        modalidad="yape",

    )


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_parsed(
    *,
    message_id: str,
    subject: str,
    ts: datetime,
    concepto: str,
    monto: Decimal | None,
    modalidad: str,
) -> ParsedEmail | None:
    if monto is None:
        return None
    return ParsedEmail(
        message_id=message_id,
        subject=subject,
        expense=Expense(
            timestamp=ts,
            concepto=concepto,
            monto=monto,
            moneda="PEN",
            modalidad=modalidad,
            fuente="auto",
            message_id=message_id,
        ),
    )


def _consumo_amount(html: str) -> Decimal | None:
    m = re.search(
        r"Realizaste un consumo de\s*<b>S/\s*([\d,.]+)</b>",
        html, re.IGNORECASE,
    )
    return _amount(m.group(1)) if m else None


def _consumo_merchant(html: str, card_suffix: str) -> str | None:
    """Extract merchant from the saludo paragraph.

    card_suffix is the unique tail of the card type: 'rédito' or 'bito'.
    Avoids putting accented chars in the pattern literal.
    """
    m = re.search(
        rf"Tarjeta de [A-Za-z\u00C0-\u00FF]+{re.escape(card_suffix)} BCP</b> en <b>([^<]+)</b>",
        html, re.IGNORECASE,
    )
    return m.group(1) if m else None


def _clean_merchant(raw: str) -> str:
    """Strip trailing period and extra whitespace from merchant names."""
    return raw.strip().rstrip(".")


def _bcp_date(html: str) -> datetime | None:
    """Parse BCP date format: '21 de abril de 2026 - 07:56 PM'."""
    m = re.search(
        r"(\d{1,2}) de (\w+) de (\d{4}) - (\d{1,2}):(\d{2}) ([AP]M)",
        html, re.IGNORECASE,
    )
    if not m:
        return None
    day, month_str, year, hour, minute, ampm = m.groups()
    month = _MONTHS.get(month_str.lower())
    if not month:
        return None
    h = int(hour)
    if ampm.upper() == "PM" and h != 12:
        h += 12
    elif ampm.upper() == "AM" and h == 12:
        h = 0
    return datetime(int(year), month, int(day), h, int(minute), tzinfo=LIMA_TZ)


def _yape_date(text: str) -> datetime | None:
    """Parse Yape date format: '20 abril 2026 - 01:30 p. m.'"""
    m = re.search(
        r"(\d{1,2}) (\w+) (\d{4}) - (\d{1,2}):(\d{2}) (a|p)\. m\.",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    day, month_str, year, hour, minute, ampm = m.groups()
    month = _MONTHS.get(month_str.lower())
    if not month:
        return None
    h = int(hour)
    if ampm.lower() == "p" and h != 12:
        h += 12
    elif ampm.lower() == "a" and h == 12:
        h = 0
    return datetime(int(year), month, int(day), h, int(minute), tzinfo=LIMA_TZ)


def _amount(text: str) -> Decimal | None:
    """Parse amount string like '13.00' or '3,280.64' (strip thousands comma)."""
    try:
        return Decimal(text.strip().replace(",", ""))
    except InvalidOperation:
        return None
