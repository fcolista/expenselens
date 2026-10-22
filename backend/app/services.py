import re
from datetime import date
from decimal import Decimal, InvalidOperation


def parse_amount(value: str) -> Decimal:
    raw = value.strip().replace(",", ".")
    if not re.fullmatch(r"(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?", raw):
        raise ValueError("Formato importo non valido")
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Formato importo non valido") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("L'importo deve essere maggiore di zero")
    return amount


def parse_spesa_details(value: str) -> tuple[str, str | None, date]:
    fields = [field.strip() for field in value.split("|")]
    if not fields[0] or len(fields) > 3:
        raise ValueError("Formato dettagli spesa non valido")
    category = fields[1] if len(fields) > 1 and fields[1] != "-" else None
    spent_on = date.today()
    if len(fields) > 2 and fields[2] != "-":
        try:
            spent_on = date.fromisoformat(fields[2])
        except ValueError as exc:
            raise ValueError("La data deve essere nel formato AAAA-MM-GG") from exc
    return fields[0][:160], category, spent_on
