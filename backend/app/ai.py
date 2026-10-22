import base64
import json
import re
import time
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Protocol
from urllib.parse import urlsplit

import httpx
import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes

from app.config import settings
from app.schemas import ReceiptExtraction


class ReceiptExtractor(Protocol):
    def extract(self, content: bytes, content_type: str) -> ReceiptExtraction: ...


def infer_payment_method(text: str) -> str | None:
    normalized = text.casefold()
    methods = set()
    if re.search(
        r"\b(satispay|paypal|apple\s*pay|google\s*pay|wallet|pagamento elettronico)\b",
        normalized,
    ):
        methods.add("Elettronico")
    if re.search(r"\b(contanti|contante|cash)\b", normalized):
        methods.add("Contanti")
    if re.search(
        r"\b(visa|mastercard|maestro|bancomat|credit card|debit card|card|"
        r"carta di credito|carta di debito|contactless|pos)\b",
        normalized,
    ):
        methods.add("Carta")
    return next(iter(methods)) if len(methods) == 1 else None


def description_from_items(items: list[dict]) -> str:
    names = dict.fromkeys(
        item["description"].strip()
        for item in items
        if isinstance(item.get("description"), str) and item["description"].strip()
    )
    return ", ".join(names)[:500]


class TesseractExtractor:
    def extract(self, content: bytes, content_type: str) -> ReceiptExtraction:
        if content_type == "application/pdf":
            images = convert_from_bytes(content, first_page=1, last_page=5, dpi=180)
            text = "\n".join(pytesseract.image_to_string(image, lang="ita+eng") for image in images)
        else:
            image = Image.open(BytesIO(content)).convert("RGB")
            text = pytesseract.image_to_string(image, lang="ita+eng")
        return ReceiptExtraction(payment_method=infer_payment_method(text))


class VisionAPIExtractor:
    def extract(self, content: bytes, content_type: str) -> ReceiptExtraction:
        if not settings.vision_api_url or not settings.vision_api_key or not settings.vision_model:
            raise RuntimeError("Configurazione provider vision incompleta")
        prompt = (
            "Estrai solo informazioni leggibili dallo scontrino. Non calcolare importi. "
            "Rispondi esclusivamente con un oggetto JSON conforme a questo schema: "
            "spent_on (YYYY-MM-DD o null), merchant (stringa o null), amount (numero o null), "
            "currency (codice ISO 4217), tax_amount (numero o null), "
            "suggested_category (stringa o null), description (breve riepilogo degli articoli acquistati, "
            "mai nome esercente, indirizzo o testo grezzo; stringa vuota se non deducibile), "
            "payment_method (uno tra 'Contanti', 'Carta', 'Elettronico' o null; valorizzalo solo "
            "se indicato chiaramente nello scontrino), "
            "items (array di oggetti con description, quantity, unit_price e total; "
            "per i valori non leggibili usa null)."
        )
        if content_type == "application/pdf":
            pages = convert_from_bytes(content, first_page=1, last_page=1, dpi=150)
            image = BytesIO()
            pages[0].save(image, format="JPEG", quality=85)
            content = image.getvalue()
            content_type = "image/jpeg"
        response = httpx.post(
            settings.vision_api_url,
            headers={"Authorization": f"Bearer {settings.vision_api_key}"},
            json={"model": settings.vision_model, "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{content_type};base64,{base64.b64encode(content).decode()}"}},
            ]}], "response_format": {"type": "json_object"}},
            timeout=45,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        extraction = ReceiptExtraction.model_validate(json.loads(raw))
        extraction.description = description_from_items(
            [item.model_dump() for item in extraction.items]
        )
        if not extraction.payment_method:
            extraction.payment_method = infer_payment_method(raw)
        return extraction


class AzureDocumentIntelligenceExtractor:
    analyze_url = (
        "{endpoint}/documentintelligence/documentModels/prebuilt-receipt:analyze"
    )
    max_free_tier_document_bytes = 4 * 1024 * 1024
    max_poll_seconds = 60

    def extract(self, content: bytes, content_type: str) -> ReceiptExtraction:
        endpoint = settings.azure_document_intelligence_endpoint.rstrip("/")
        subscription_key = settings.azure_document_intelligence_key
        if not endpoint or not subscription_key:
            raise RuntimeError("Configurazione Azure Document Intelligence incompleta")
        if len(content) > self.max_free_tier_document_bytes:
            raise ValueError("Con il piano Azure F0 il documento non può superare 4 MiB")

        headers = {"Ocp-Apim-Subscription-Key": subscription_key}
        response = httpx.post(
            self.analyze_url.format(endpoint=endpoint),
            params={
                "_overload": "analyzeDocument",
                "api-version": "2024-11-30",
                "locale": "it-IT",
            },
            headers=headers,
            json={"base64Source": base64.b64encode(content).decode("ascii")},
            timeout=30,
        )
        response.raise_for_status()
        operation_url = response.headers.get("Operation-Location")
        if not operation_url:
            raise RuntimeError("Azure non ha restituito l'URL dell'operazione OCR")
        endpoint_parts = urlsplit(endpoint)
        operation_parts = urlsplit(operation_url)
        if (
            operation_parts.scheme != "https"
            or operation_parts.netloc.lower() != endpoint_parts.netloc.lower()
        ):
            raise RuntimeError("Azure ha restituito un URL di polling non valido")

        deadline = time.monotonic() + self.max_poll_seconds
        while True:
            result_response = httpx.get(operation_url, headers=headers, timeout=15)
            result_response.raise_for_status()
            result = result_response.json()
            status = result.get("status")
            if status == "succeeded":
                return self._to_receipt_extraction(result.get("analyzeResult", {}))
            if status == "failed":
                error = result.get("error", {})
                code = error.get("code", "errore sconosciuto")
                raise RuntimeError(f"Azure Document Intelligence: analisi non riuscita ({code})")
            if status not in {"notStarted", "running"}:
                raise RuntimeError("Azure ha restituito uno stato OCR non riconosciuto")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timeout durante l'analisi dello scontrino con Azure")
            try:
                retry_after = float(result_response.headers.get("Retry-After", "1"))
            except ValueError:
                retry_after = 1
            time.sleep(min(max(retry_after, 0.1), 5, remaining))

    @staticmethod
    def _field_value(fields: dict, name: str, value_key: str):
        return fields.get(name, {}).get(value_key)

    @classmethod
    def _currency_amount(cls, fields: dict, name: str) -> Decimal | None:
        value = cls._field_value(fields, name, "valueCurrency")
        if not isinstance(value, dict) or value.get("amount") is None:
            return None
        return Decimal(str(value["amount"]))

    @classmethod
    def _to_receipt_extraction(cls, analyze_result: dict) -> ReceiptExtraction:
        documents = analyze_result.get("documents") or []
        fields = documents[0].get("fields", {}) if documents else {}
        items = []
        for item in cls._field_value(fields, "Items", "valueArray") or []:
            item_fields = item.get("valueObject", {})
            description = cls._field_value(item_fields, "Description", "valueString")
            if not isinstance(description, str) or not description.strip():
                continue
            quantity = cls._field_value(item_fields, "Quantity", "valueNumber")
            items.append({
                "description": description.strip(),
                "quantity": quantity if quantity and quantity > 0 else None,
                "unit_price": cls._currency_amount(item_fields, "Price"),
                "total": cls._currency_amount(item_fields, "TotalPrice"),
            })

        transaction_date = cls._field_value(fields, "TransactionDate", "valueDate")
        try:
            parsed_date = date.fromisoformat(transaction_date) if transaction_date else None
        except (TypeError, ValueError):
            parsed_date = None
        total = cls._currency_amount(fields, "Total")
        total_field = fields.get("Total", {}).get("valueCurrency", {})
        currency = total_field.get("currencyCode") or "EUR"
        content = analyze_result.get("content", "")
        return ReceiptExtraction(
            merchant=cls._field_value(fields, "MerchantName", "valueString"),
            spent_on=parsed_date,
            amount=total,
            currency=currency,
            tax_amount=cls._currency_amount(fields, "TotalTax"),
            items=items,
            description=description_from_items(items),
            payment_method=(
                infer_payment_method(
                    str(cls._field_value(fields, "PaymentMethod", "valueString") or "")
                    + "\n" + content
                )
            ),
        )


def get_extractor() -> ReceiptExtractor:
    if settings.ocr_provider == "azure_document_intelligence":
        return AzureDocumentIntelligenceExtractor()
    if settings.ocr_provider == "vision":
        return VisionAPIExtractor()
    if settings.ocr_provider == "tesseract":
        return TesseractExtractor()
    raise RuntimeError(
        "OCR_PROVIDER deve essere azure_document_intelligence, vision oppure tesseract"
    )
