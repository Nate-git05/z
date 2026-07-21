"""Intentionally fragile parser for review-only tasks."""


def parse_ints(text: str):
    # Broad except absorbs failures — review should flag this.
    try:
        return [int(x) for x in text.split(",")]
    except Exception:
        return []
