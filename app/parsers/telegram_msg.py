import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models import Expense

_LIMA_TZ = ZoneInfo("America/Lima")

# Matches: <concepto> <monto> [moneda] [modalidad]
# monto is the anchor — everything before it is concepto
_PATTERN = re.compile(
    r"^(?P<concepto>.+?)\s+"
    r"(?P<monto>\d{1,3}(?:[,\.]\d{3})*(?:[.,]\d+)?|\d+[.,]\d+|\d+)"
    r"(?:\s+(?P<moneda>PEN|USD))?"
    r"(?:\s+(?P<modalidad>efectivo|debito|credito|yape|plin|transferencia))?",
    re.IGNORECASE,
)


def parse_manual(text: str) -> Expense | None:
    """Parse a manual expense message. Format: 'concepto monto [moneda] [modalidad]'."""
    text = text.strip()
    m = _PATTERN.match(text)
    if not m:
        return None

    concepto = m.group("concepto").strip()
    monto_raw = m.group("monto").replace(",", ".")
    # Handle thousands separator: "1.000.50" → only last dot is decimal
    # Normalize: if more than one dot, remove all but the last
    parts = monto_raw.split(".")
    if len(parts) > 2:
        monto_raw = "".join(parts[:-1]) + "." + parts[-1]

    try:
        monto = Decimal(monto_raw)
    except Exception:
        return None

    moneda = (m.group("moneda") or "PEN").upper()
    modalidad = (m.group("modalidad") or "efectivo").lower()

    return Expense(
        timestamp=datetime.now(tz=_LIMA_TZ),
        concepto=concepto,
        monto=monto,
        moneda=moneda,
        modalidad=modalidad,
        fuente="manual",
        message_id="",
        raw_excerpt="",
    )
