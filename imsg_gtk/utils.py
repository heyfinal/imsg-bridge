def initials(name_or_identifier: str) -> str:
    text = (name_or_identifier or "").strip()
    if not text:
        return "?"
    cleaned = text.replace("@", " ").replace(".", " ").replace("_", " ")
    parts = [part for part in cleaned.split() if part]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return text[:2].upper()
