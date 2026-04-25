from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

VALID_MODALITIES: frozenset[str] = frozenset(
    {"efectivo", "debito", "credito", "yape", "plin", "transferencia", "wallet"}
)


@dataclass
class Expense:
    timestamp: datetime
    concepto: str
    monto: Decimal
    moneda: str
    modalidad: str
    fuente: str
    message_id: str


@dataclass
class ParsedEmail:
    message_id: str
    subject: str
    expense: Expense | None
