"""Last-line regex PII scrubber for values written to the shared facts store.

Scope (F8): this runs ONLY on publish_fact values. It does not touch chat-history,
workflow checkpoints or the live handoff transcript. Those hold raw user text (F12) and
are governed by retention TTLs (infra/cosmos/main.tf) and the PII decision in the README.

For production, put Azure AI Language PII detection in front of this; keep the regexes as a
safety net. Order matters: more specific patterns run first so labels stay accurate.
"""
import re


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        total, alt = total + n, not alt
    return total % 10 == 0


# Thai national ID: 13 digits, plain or dashed 1-2345-67890-12-3
_THAI_ID = re.compile(r"(?<![\d-])\d-?\d{4}-?\d{5}-?\d{2}-?\d(?![\d-])")
# Card: 13-19 digits, optional single space/dash between digits; never consumes trailing space
_CARD = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_AADHAAR = re.compile(r"(?<!\d)\d{4} ?\d{4} ?\d{4}(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PHONE = re.compile(r"(?<!\d)\+?\d(?:[ -]?\d){8,13}(?!\d)")


def _card(m: re.Match) -> str:
    digits = re.sub(r"\D", "", m.group())
    return "[CARD]" if _luhn_ok(digits) else m.group()


def redact(text: str) -> str:
    text = _EMAIL.sub("[EMAIL]", text)
    text = _CARD.sub(_card, text)           # only Luhn-valid numbers are cards
    text = _THAI_ID.sub("[NATIONAL_ID]", text)
    text = _AADHAAR.sub("[NATIONAL_ID]", text)
    text = _PAN.sub("[PAN]", text)
    text = _PHONE.sub("[PHONE]", text)
    return text
