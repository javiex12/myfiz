import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

from app.config import settings
from app.models import Expense

LIMA_TZ = ZoneInfo("America/Lima")
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetsClient:
    def __init__(self) -> None:
        creds_dict = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=_SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(settings.SHEET_ID)
        self._expenses = spreadsheet.worksheet("expenses")
        self._processed = spreadsheet.worksheet("processed_emails")
        self._errors = spreadsheet.worksheet("errors")
        try:
            self._config = spreadsheet.worksheet("config")
        except gspread.exceptions.WorksheetNotFound:
            self._config = spreadsheet.add_worksheet("config", rows=10, cols=2)
            self._config.append_row(["key", "value"])

    def append_expense(self, expense: Expense) -> None:
        row = [
            expense.timestamp.isoformat(),
            expense.concepto,
            str(expense.monto),
            expense.moneda,
            expense.modalidad,
            expense.fuente,
            expense.message_id,
        ]
        self._expenses.append_row(row, value_input_option="USER_ENTERED")

    def get_processed_ids(self) -> set[str]:
        """Read the entire processed_emails message_id column in a single API call."""
        col = self._processed.col_values(1)
        return set(col[1:]) if len(col) > 1 else set()

    def mark_processed(self, message_id: str) -> None:
        now = datetime.now(tz=LIMA_TZ).isoformat()
        self._processed.append_row([message_id, now])

    def get_last_history_id(self) -> str | None:
        rows = self._config.get_all_values()
        for row in rows[1:]:
            if row and row[0] == "last_history_id":
                return row[1] if len(row) > 1 and row[1] else None
        return None

    def set_last_history_id(self, history_id: str) -> None:
        rows = self._config.get_all_values()
        for i, row in enumerate(rows, start=1):
            if row and row[0] == "last_history_id":
                self._config.update_cell(i, 2, history_id)
                return
        self._config.append_row(["last_history_id", history_id])

    def log_error(self, type: str, detail: str, raw: str) -> None:
        now = datetime.now(tz=LIMA_TZ).isoformat()
        self._errors.append_row([now, type, detail, raw])

    def query_by_date(self, target_date: date) -> list[Expense]:
        rows = self._expenses.get_all_values()
        if len(rows) <= 1:
            return []
        date_str = target_date.isoformat()
        return [_row_to_expense(row) for row in rows[1:] if row[0].startswith(date_str)]

    def get_last_n(self, n: int) -> list[Expense]:
        rows = self._expenses.get_all_values()
        data_rows = rows[1:] if len(rows) > 1 else []
        return [_row_to_expense(row) for row in data_rows[-n:]]

    def query_by_category(self, category: str) -> list[Expense]:
        rows = self._expenses.get_all_values()
        if len(rows) <= 1:
            return []
        category_lower = category.lower()
        return [
            _row_to_expense(row)
            for row in rows[1:]
            if category_lower in row[1].lower()
        ]


def _row_to_expense(row: list[str]) -> Expense:
    return Expense(
        timestamp=datetime.fromisoformat(row[0]),
        concepto=row[1],
        monto=Decimal(row[2]),
        moneda=row[3],
        modalidad=row[4],
        fuente=row[5],
        message_id=row[6],
    )
