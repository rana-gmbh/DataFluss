"""Password generator. Look-alike characters are excluded for every profile."""
import secrets

from django.utils.translation import gettext_lazy as _

VOWELS = "aeiu"
CONSONANTS = "bcdfghkmnprstvwz"
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
LOWER = "abcdefghijkmnpqrstuvwxyz"
DIGITS = "23456789"
SYMBOLS = "-_!?+"

PROFILES = {
    "einfach": _("Einfach (Grundschule): gut lesbar, z. B. Tomika-47"),
    "mittel": _("Mittel (ab Klasse 5): 12 Zeichen, gut lesbar"),
    "stark": _("Stark (Oberstufe, Personal): 16 Zeichen mit Sonderzeichen"),
}


def _pronounceable(syllables):
    return "".join(secrets.choice(CONSONANTS) + secrets.choice(VOWELS) for _ in range(syllables))


def generate(profile="mittel"):
    if profile == "einfach":
        word = _pronounceable(3).capitalize()
        return f"{word}-{secrets.choice(DIGITS)}{secrets.choice(DIGITS)}"
    if profile == "stark":
        pool = UPPER + LOWER + DIGITS + SYMBOLS
        required = [secrets.choice(s) for s in (UPPER, LOWER, DIGITS, SYMBOLS)]
        rest = [secrets.choice(pool) for _ in range(12)]
    else:
        pool = UPPER + LOWER + DIGITS
        required = [secrets.choice(s) for s in (UPPER, LOWER, DIGITS)]
        rest = [secrets.choice(pool) for _ in range(9)]
    chars = required + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def profile_for(person):
    if person is None or person.kind == "staff":
        return "stark"
    level = person.grade_level or 0
    if person.school_type in ("Kiga", "GS") or level <= 4:
        return "einfach"
    return "stark" if level >= 10 else "mittel"
