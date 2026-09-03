from __future__ import annotations


def parse(input_text: str) -> tuple[str, bool]:
    text = input_text.strip()
    if not text.startswith("/"):
        return "", False
    if text == "/":
        return "", True
    body = text[1:]
    if body[:1].isspace():
        return "", True
    parts = body.split(maxsplit=1)
    if not parts or not parts[0]:
        return "", True
    if len(parts) > 1 and parts[1].strip():
        return "", True
    return parts[0].lower(), True
