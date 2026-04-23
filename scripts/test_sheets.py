"""
Smoke test for SheetsClient. Requires real credentials.

Usage:
    1. Copy .env.example to .env and fill in real values.
    2. python scripts/test_sheets.py
"""
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models import Expense
from app.services.sheets import SheetsClient

LIMA_TZ = ZoneInfo("America/Lima")


def main() -> None:
    print("Connecting to Google Sheets...")
    client = SheetsClient()
    print("Connected.\n")

    test_id = f"smoke-test-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    test_expense = Expense(
        timestamp=datetime.now(tz=LIMA_TZ),
        concepto="Smoke test",
        monto=Decimal("0.01"),
        moneda="PEN",
        modalidad="efectivo",
        fuente="manual",
        message_id=test_id,
        raw_excerpt="smoke test row — safe to delete",
    )

    # 1. New ID should not be processed yet
    assert not client.is_processed(test_id), "FAIL: is_processed should be False for new ID"
    print("PASS: is_processed → False for new ID")

    # 2. Append expense
    client.append_expense(test_expense)
    print("PASS: append_expense wrote row to 'expenses'")

    # 3. Mark processed and verify
    client.mark_processed(test_id)
    assert client.is_processed(test_id), "FAIL: is_processed should be True after mark_processed"
    print("PASS: mark_processed + is_processed → True")

    # 4. Log error
    client.log_error("smoke_test", "this is a test error", "raw data")
    print("PASS: log_error wrote row to 'errors'")

    print("\nAll checks passed.")
    print("Please manually delete the smoke-test rows from the Sheet:")
    print(f"  expenses        → message_id = {test_id}")
    print(f"  processed_emails → message_id = {test_id}")
    print("  errors           → type = smoke_test")


if __name__ == "__main__":
    main()
