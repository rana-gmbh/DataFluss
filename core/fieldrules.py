"""Field rules of a role: a field of pupils, staff or an own table is "edit" (as the area allows), "read"
(seen, never changed) or "hidden" (not on pages, lists, search, prints, exports, history, formulas, imports,
nor on the role's computers). Stored on the role (core.models.UserRole.fields, {entity: {key: rule}});
entities are "pupil", "staff" and "table:<key>". IT has no rules. Without a user (system work) nothing is
hidden.

Some fields cannot be hidden, because a record would have no name otherwise: first and last name of a
person, and a table's title field with the fields its title formula uses (`locked`).
"""
EDIT, READ, HIDDEN = "edit", "read", "hidden"
RULES = (EDIT, READ, HIDDEN)
PERSON_NAMES = ("first_name", "last_name")


def rules(user, entity):
    """{field key: "read" | "hidden"} of the user's role for this entity, kept on the user for the request."""
    if user is None or not getattr(user, "is_authenticated", False):
        return {}
    if (own := getattr(user, "field_rules", None)) is not None:  # a publication: everything not chosen is hidden (publish/viewer.py)
        return own(entity)
    from .models import Role, UserRole

    if user.role == Role.IT:
        return {}
    cached = getattr(user, "_field_rules", None)
    if cached is None or cached[0] != user.role:
        row = UserRole.objects.filter(key=user.role).values_list("fields", flat=True).first()
        cached = (user.role, row or {})
        user._field_rules = cached
    return cached[1].get(entity, {})


def hidden(user, entity):
    return {key for key, rule in rules(user, entity).items() if rule == HIDDEN}


def read_only(user, entity):
    return {key for key, rule in rules(user, entity).items() if rule in (READ, HIDDEN)}


def visible(user, entity, key):
    return rules(user, entity).get(key) != HIDDEN


def may_change(user, entity, key):
    return rules(user, entity).get(key) not in (READ, HIDDEN)


def table_entity(table):
    return f"table:{table.key}"


def locked(entity):
    """Keys that may be read-only but never hidden."""
    if entity in ("pupil", "staff"):
        return set(PERSON_NAMES)
    if entity.startswith("table:"):
        from tables import formula, services
        from tables.models import Table

        table = Table.objects.filter(key=entity.removeprefix("table:")).first()
        if table is None:
            return set()
        named = services.title_field(table, services.fields_of(table))
        if named is None:
            return set()
        keys = {named.key}
        if named.type == "formula":
            keys |= formula.used_fields((named.config or {}).get("expression", ""))
        return keys
    return set()


def clean(entity, posted):
    """{key: rule} as stored: "edit" is the default and not stored; locked keys cannot be hidden."""
    fixed = locked(entity)
    out = {}
    for key, rule in posted.items():
        if rule == HIDDEN and key in fixed:
            rule = READ
        if rule in (READ, HIDDEN):
            out[key] = rule
    return out
