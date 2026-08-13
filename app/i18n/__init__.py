from app.i18n.strings import t


def display_name(name_en: str, name_ko: str) -> str:
    """Return one label for identical names, otherwise a bilingual label."""
    if name_en.strip().casefold() == name_ko.strip().casefold():
        return name_en
    return f"{name_en} / {name_ko}"


__all__ = ["display_name", "t"]
