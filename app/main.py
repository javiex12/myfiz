import base64
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from fastapi import FastAPI, Header, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from googleapiclient.errors import HttpError

from app.config import settings
from app.parsers.bcp import parse_bcp
from app.parsers.telegram_msg import parse_manual
from app.services.gmail import GmailClient
from app.services.sheets import SheetsClient
from app.services.telegram import TelegramClient

logger = structlog.get_logger()

_LIMA_TZ = ZoneInfo("America/Lima")

# Consecutive parse-failure counter (in-memory; resets on restart — acceptable for V1)
_consecutive_parse_errors: int = 0


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
    ]
    if settings.ENVIRONMENT == "prod":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(processors=processors)
    logger.info("startup", environment=settings.ENVIRONMENT)

    app.state.gmail = GmailClient()
    app.state.sheets = SheetsClient()
    app.state.telegram = TelegramClient()

    yield
    logger.info("shutdown")


app = FastAPI(title="Finbot", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Gmail webhook ─────────────────────────────────────────────────────────────

@app.post("/gmail-webhook")
async def gmail_webhook(request: Request) -> dict[str, Any]:
    global _consecutive_parse_errors

    if settings.ENVIRONMENT != "dev":
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning("gmail_webhook_missing_token")
            raise HTTPException(status_code=403, detail="Missing Bearer token")
        token = auth_header.removeprefix("Bearer ")
        expected_audience = settings.CLOUD_RUN_URL.rstrip("/") + "/gmail-webhook"
        try:
            id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                audience=expected_audience,
            )
        except Exception:
            logger.warning("gmail_webhook_invalid_jwt", expected_audience=expected_audience)
            raise HTTPException(status_code=403, detail="Invalid JWT")

    body = await request.json()
    raw_data = body.get("message", {}).get("data", "")
    if not raw_data:
        logger.warning("gmail_webhook_no_data", body_keys=list(body.keys()))
        return {"ok": True}

    pubsub_data = json.loads(base64.b64decode(raw_data + "==").decode())
    history_id: str = str(pubsub_data.get("historyId", ""))
    logger.info("gmail_webhook_received", history_id=history_id)

    gmail: GmailClient = request.app.state.gmail
    sheets: SheetsClient = request.app.state.sheets
    telegram: TelegramClient = request.app.state.telegram
    owner_chat_id: int = settings.TELEGRAM_ALLOWED_CHAT_IDS[0]

    last_history_id = sheets.get_last_history_id()
    logger.info("gmail_webhook_history_ids", notification=history_id, last_stored=last_history_id)
    message_ids = gmail.get_new_message_ids(history_id, last_history_id)
    sheets.set_last_history_id(history_id)

    # Read processed_emails once per webhook call instead of per-message
    # to avoid Sheets API quota (60 read req/min/user).
    processed_ids = sheets.get_processed_ids()

    for message_id in message_ids:
        if message_id in processed_ids:
            logger.info("gmail_webhook_duplicate_skipped", message_id=message_id)
            continue

        try:
            html, subject, mid = gmail.get_message(message_id)
        except HttpError as e:
            if e.status_code == 404:
                logger.warning("gmail_webhook_message_not_found", message_id=message_id)
                continue
            raise
        result = parse_bcp(html, subject, mid)

        if result and result.expense:
            sheets.append_expense(result.expense)
            sheets.mark_processed(message_id)
            processed_ids.add(message_id)
            _consecutive_parse_errors = 0
            exp = result.expense
            amount_str = f"S/ {exp.monto}" if exp.moneda == "PEN" else f"$ {exp.monto}"
            telegram.send_message(
                owner_chat_id, f"✅ {exp.concepto} {amount_str} ({exp.modalidad})"
            )
            logger.info(
                "gmail_webhook_expense_saved",
                concepto=exp.concepto,
                monto=str(exp.monto),
            )
        else:
            _consecutive_parse_errors += 1
            sheets.log_error("parse_failed", "bcp_no_parser_matched", html[:400])
            alert = f"⚠️ No pude parsear email: {subject}\n\n{html[:200]}"
            if _consecutive_parse_errors >= 3:
                alert = (
                    f"⚠️ Parser fallando {_consecutive_parse_errors} veces seguidas. "
                    f"¿Cambió el formato BCP?\n\nÚltimo: {subject}"
                )
            telegram.send_message(owner_chat_id, alert)
            logger.warning(
                "gmail_webhook_parse_failed",
                subject=subject,
                consecutive=_consecutive_parse_errors,
            )

    return {"ok": True}


# ── Renew Gmail Watch ─────────────────────────────────────────────────────────

@app.post("/renew-watch")
async def renew_watch(request: Request) -> dict[str, Any]:
    gmail: GmailClient = request.app.state.gmail
    sheets: SheetsClient = request.app.state.sheets
    result = gmail.renew_watch(settings.PUBSUB_TOPIC)
    sheets.set_last_history_id(result["historyId"])
    logger.info("gmail_watch_renewed", history_id=result["historyId"], expiration=result["expiration"])
    return {"ok": True, "historyId": result["historyId"], "expiration": result["expiration"]}


# ── Telegram webhook ──────────────────────────────────────────────────────────

@app.post("/telegram-webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, Any]:
    if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET.strip():
        logger.warning("telegram_webhook_invalid_secret")
        raise HTTPException(status_code=403, detail="Invalid secret token")

    body = await request.json()
    message = body.get("message") or body.get("edited_message") or {}
    chat_id: int | None = message.get("chat", {}).get("id")

    if chat_id not in settings.TELEGRAM_ALLOWED_CHAT_IDS:
        logger.warning("telegram_webhook_unauthorized_chat", chat_id=chat_id)
        raise HTTPException(status_code=403, detail="Unauthorized chat_id")

    text: str = (message.get("text") or "").strip()
    logger.info("telegram_webhook_received", chat_id=chat_id, text=text[:80])

    sheets: SheetsClient = request.app.state.sheets
    telegram: TelegramClient = request.app.state.telegram

    if text.startswith("/"):
        reply = _handle_command(text, sheets)
    else:
        reply = _handle_manual_expense(text, sheets)

    telegram.send_message(chat_id, reply)
    return {"ok": True}


def _handle_command(text: str, sheets: SheetsClient) -> str:
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/hoy":
        expenses = sheets.query_by_date(date.today())
        if not expenses:
            return "No hay gastos hoy."
        total = sum(e.monto for e in expenses)
        lines = [f"• {e.concepto}: S/ {e.monto}" for e in expenses]
        lines.append(f"\nTotal: S/ {total}")
        return "\n".join(lines)

    if cmd == "/ultimo":
        expenses = sheets.get_last_n(5)
        if not expenses:
            return "No hay gastos registrados."
        return "\n".join(
            f"• {e.concepto}: S/ {e.monto} ({e.modalidad})" for e in expenses
        )

    if cmd == "/resumen":
        today = datetime.now(tz=_LIMA_TZ)
        month_start = today.replace(day=1).date()
        expenses = sheets.query_by_date(month_start)
        # query_by_date filters by exact date — need full month
        all_rows = sheets._expenses.get_all_values()
        month_prefix = today.strftime("%Y-%m")
        from app.services.sheets import _row_to_expense
        month_expenses = [
            _row_to_expense(row)
            for row in all_rows[1:]
            if row[0].startswith(month_prefix)
        ]
        if not month_expenses:
            return "No hay gastos este mes."
        totals: dict[str, Decimal] = defaultdict(Decimal)
        for e in month_expenses:
            totals[e.modalidad] += e.monto
        grand = sum(totals.values())
        lines = [f"• {mod}: S/ {amt}" for mod, amt in sorted(totals.items())]
        lines.append(f"\nTotal mes: S/ {grand}")
        return "\n".join(lines)

    if cmd.startswith("/categoria"):
        arg = arg or cmd.removeprefix("/categoria").lstrip("_")
        if not arg:
            return "Uso: /categoria_<nombre>"
        expenses = sheets.query_by_category(arg)
        if not expenses:
            return f"No hay gastos con categoría '{arg}'."
        total = sum(e.monto for e in expenses)
        lines = [f"• {e.concepto}: S/ {e.monto}" for e in expenses]
        lines.append(f"\nTotal: S/ {total}")
        return "\n".join(lines)

    return "Comandos: /hoy, /ultimo, /resumen, /categoria <nombre>"


def _handle_manual_expense(text: str, sheets: SheetsClient) -> str:
    expense = parse_manual(text)
    if expense is None:
        modalities = "efectivo|debito|credito|yape|plin|transferencia"
        return (
            "No entendí el gasto. Formato esperado:\n"
            f"  concepto monto [PEN|USD] [{modalities}]\n"
            "Ejemplo: Candies 2.40 PEN efectivo"
        )
    sheets.append_expense(expense)
    if expense.moneda == "PEN":
        amount_str = f"S/ {expense.monto}"
    else:
        amount_str = f"$ {expense.monto}"
    return f"✅ Guardado: {expense.concepto} {amount_str} ({expense.modalidad})"
