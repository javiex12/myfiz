from app.services.sheets import SheetsClient


def is_duplicate(message_id: str, sheets: SheetsClient) -> bool:
    """Return True if the message has already been processed."""
    return sheets.is_processed(message_id)
