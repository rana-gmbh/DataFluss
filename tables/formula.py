"""Computed fields of own tables: a small formula language (docs/TABLES-PLAN.md §5).

Parsed and evaluated here, in Python, on the server and in a computer's sidecar alike: no eval, no access
to Python, files or the network. Formulas are stored with field keys and shown with field names, so a
rename or another language never breaks one. Values are computed when read and never stored.

    {Preis} * {Menge}                     fields of the record
    {Vorname} & " " & {Nachname}          & joins text
    if({Rückgabe} = null, days({Ausleihe}, today()), 0)
    count({Ausleihen})                    a link field, or the records of another table linking here
    sum({Positionen}.{Betrag})            a field of the linked records

Stored form of references: {f_key} (a field), {f_link}.{f_key} (a field of linked records), {@table.f_link}
(records of `table` whose link field f_link points here).
"""
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.utils import timezone
from django.utils.translation import gettext as _

MAX_LENGTH = 2000
MAX_DEPTH = 32
MAX_STEPS = 10_000
MAX_LINKED = 1000


class FormulaError(Exception):
    """A formula that cannot be read or computed; `position` points into its text when known."""

    def __init__(self, message, position=None):
        super().__init__(message)
        self.message, self.position = message, position


class Hidden(Exception):
    """The formula reads records the user may not see."""


# ---------------------------------------------------------------- reading

TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<ref>\{[^{}]*\})
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|!=|[=<>+\-*/&(),.])
""", re.VERBOSE)
WORDS = {"and", "or", "not", "true", "false", "null"}


def tokens(text):
    if len(text) > MAX_LENGTH:
        raise FormulaError(_("Die Formel ist zu lang (höchstens %(n)s Zeichen).") % {"n": MAX_LENGTH})
    out, position = [], 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if match is None:
            raise FormulaError(_("Unerwartetes Zeichen „%(c)s“.") % {"c": text[position]}, position)
        kind = match.lastgroup
        if kind != "space":
            out.append((kind, match.group(), position))
        position = match.end()
    out.append(("end", "", len(text)))
    return out


class Parser:
    def __init__(self, text):
        self.items = tokens(text)
        self.index = 0
        self.depth = 0

    def peek(self):
        return self.items[self.index]

    def take(self, kind=None, value=None):
        token = self.items[self.index]
        if (kind and token[0] != kind) or (value and token[1] != value):
            expected = value or {"ref": "{Feld}", "name": _("Name")}.get(kind, kind)
            raise FormulaError(_("Erwartet: %(what)s.") % {"what": expected}, token[2])
        self.index += 1
        return token

    def at(self, value):
        token = self.peek()
        return token[0] in ("op", "name") and token[1] == value

    def parse(self):
        if self.peek()[0] == "end":
            raise FormulaError(_("Die Formel ist leer."), 0)
        tree = self.expression()
        token = self.peek()
        if token[0] != "end":
            raise FormulaError(_("Unerwartet: „%(t)s“.") % {"t": token[1]}, token[2])
        return tree

    def nested(self, parse):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise FormulaError(_("Die Formel ist zu tief verschachtelt."), self.peek()[2])
        try:
            return parse()
        finally:
            self.depth -= 1

    def expression(self):
        return self.nested(self.either)

    def either(self):
        left = self.both()
        while self.at("or"):
            position = self.take()[2]
            left = ("or", left, self.both(), position)
        return left

    def both(self):
        left = self.negation()
        while self.at("and"):
            position = self.take()[2]
            left = ("and", left, self.negation(), position)
        return left

    def negation(self):
        if self.at("not"):
            position = self.take()[2]
            return ("not", self.nested(self.negation), position)
        return self.comparison()

    def comparison(self):
        left = self.joined()
        if self.peek()[0] == "op" and self.peek()[1] in ("=", "!=", "<", "<=", ">", ">="):
            op, position = self.take()[1:]
            return ("cmp", op, left, self.joined(), position)
        return left

    def joined(self):
        left = self.sum()
        while self.at("&"):
            position = self.take()[2]
            left = ("join", left, self.sum(), position)
        return left

    def sum(self):
        left = self.product()
        while self.at("+") or self.at("-"):
            op, position = self.take()[1:]
            left = ("arith", op, left, self.product(), position)
        return left

    def product(self):
        left = self.unary()
        while self.at("*") or self.at("/"):
            op, position = self.take()[1:]
            left = ("arith", op, left, self.unary(), position)
        return left

    def unary(self):
        if self.at("-"):
            position = self.take()[2]
            return ("neg", self.nested(self.unary), position)
        return self.primary()

    def primary(self):
        kind, text, position = self.peek()
        if kind == "number":
            self.take()
            return ("value", Decimal(text), position)
        if kind == "string":
            self.take()
            return ("value", re.sub(r"\\(.)", r"\1", text[1:-1]), position)
        if kind == "ref":
            self.take()
            first = text[1:-1].strip()
            if self.at(".") and self.items[self.index + 1][0] == "ref":
                self.take()
                second = self.take("ref")[1][1:-1].strip()
                return ("path", first, second, position)
            return ("ref", first, position)
        if kind == "name":
            self.take()
            word = text.lower()
            if word in ("true", "false"):
                return ("value", word == "true", position)
            if word == "null":
                return ("value", None, position)
            if word in WORDS:
                raise FormulaError(_("Unerwartet: „%(t)s“.") % {"t": text}, position)
            self.take("op", "(")
            args = []
            if not self.at(")"):
                args.append(self.expression())
                while self.at(","):
                    self.take()
                    args.append(self.expression())
            self.take("op", ")")
            return ("call", word, args, position)
        if self.at("("):
            self.take()
            inner = self.expression()
            self.take("op", ")")
            return inner
        raise FormulaError(_("Unerwartet: „%(t)s“.") % {"t": text} if text else _("Die Formel endet zu früh."), position)


def parse(text):
    return Parser(text).parse()


# ---------------------------------------------------------------- names and keys

def _ref_tokens(text):
    """(start, end, inner text) of every {…} reference outside strings."""
    return [(position, position + len(value), value[1:-1].strip()) for kind, value, position in tokens(text) if kind == "ref"]


def _replace_refs(text, swap):
    """`text` with each reference replaced by swap(inner, previous inner or None); paths pass their first part."""
    out, last, previous_end, previous = [], 0, None, None
    for start, end, inner in _ref_tokens(text):
        is_second = previous_end is not None and text[previous_end:start].strip() == "."
        out.append(text[last:start])
        try:
            out.append("{" + swap(inner, previous if is_second else None) + "}")
        except FormulaError as exc:
            exc.position = start if exc.position is None else exc.position
            raise
        last, previous_end, previous = end, end, inner
    out.append(text[last:])
    return "".join(out)


def _norm(name):
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


class Schema:
    """The fields a formula of `table` may name, by name and by key, with the tables they lead to."""

    def __init__(self, table):
        from . import links
        from .models import Field, Table

        self.table = table
        self.fields = {f.key: f for f in Field.objects.filter(table=table, archived=False)}
        self.reverse = {}  # "@table.f_link" -> (source table, link field)
        for link in Field.objects.filter(type="link", archived=False).exclude(table=table).select_related("table"):
            if links.kind_of(link) == "record" and (link.config or {}).get("table") == table.key and not link.table.archived:
                self.reverse[f"@{link.table.key}.{link.key}"] = (link.table, link)
        self._tables = {}
        self._Table, self._Field = Table, Field

    def target(self, link_field):
        """The fields of the table a link field points to."""
        key = (link_field.config or {}).get("table", "")
        if key not in self._tables:
            table = self._Table.objects.filter(key=key).first()
            self._tables[key] = (table, {f.key: f for f in self._Field.objects.filter(table=table, archived=False)} if table else {})
        return self._tables[key]

    def reverse_name(self, key):
        source, link = self.reverse[key]
        return (link.config or {}).get("reverse") or source.display_name

    def by_name(self, name, fields):
        wanted = _norm(name)
        found = [f for f in fields.values() if wanted in (_norm(f.display_label), _norm(f.label))]
        return found[0] if len(found) == 1 else None

    def own_key(self, name, position=None):
        if name in self.fields or name in self.reverse:
            return name
        field = self.by_name(name, self.fields)
        if field is not None:
            return field.key
        reverse = [k for k in self.reverse if _norm(self.reverse_name(k)) == _norm(name)]
        if len(reverse) == 1:
            return reverse[0]
        raise FormulaError(_("Unbekanntes Feld {%(name)s}.") % {"name": name}, position)

    def path_fields(self, first_key, position=None):
        """(table, fields) behind the first part of {a}.{b}: a link field of this table, or the records of
        another table that link here ({@table.f_link})."""
        if first_key in self.reverse:
            source = self.reverse[first_key][0]
            if first_key not in self._tables:
                self._tables[first_key] = (source, {f.key: f for f in self._Field.objects.filter(table=source, archived=False)})
            return self._tables[first_key]
        link = self.fields.get(first_key)
        if link is None or link.type != "link" or (link.config or {}).get("target") != "record":
            raise FormulaError(_("{%(name)s} ist keine Verknüpfung zu einer Tabelle.") % {"name": first_key}, position)
        return self.target(link)

    def path_key(self, first_key, name, position=None):
        table, fields = self.path_fields(first_key, position)
        if name in fields:
            return name
        field = self.by_name(name, fields)
        if field is None:
            raise FormulaError(_("Unbekanntes Feld {%(name)s}.") % {"name": name}, position)
        return field.key


def to_keys(text, schema):
    """The formula as typed (field names) in its stored form (field keys)."""
    parse(text)  # syntax first, with positions in the typed text

    def swap(inner, first):
        return schema.path_key(schema.own_key(first), inner) if first is not None else schema.own_key(inner)
    return _replace_refs(text, swap)


def to_names(text, schema):
    """The stored formula as a person reads it. Keys that are gone stay as they are, so the check names them."""
    def swap(inner, first):
        if first is not None:
            try:
                fields = schema.path_fields(first)[1]
            except FormulaError:
                fields = {}
            return fields[inner].display_label if inner in fields else inner
        if inner in schema.fields:
            return schema.fields[inner].display_label
        if inner in schema.reverse:
            return schema.reverse_name(inner)
        return inner
    return _replace_refs(text, swap)


# ---------------------------------------------------------------- types

NUMBER, TEXT, DATE, DATETIME, BOOL, NULL, ANY, LIST = "number", "text", "date", "datetime", "bool", "null", "any", "list"
FIELD_TYPES = {"number": NUMBER, "text": TEXT, "textarea": TEXT, "email": TEXT, "phone": TEXT, "url": TEXT, "choice": TEXT,
               "multichoice": TEXT, "date": DATE, "datetime": DATETIME, "bool": BOOL}


def _list(item):
    return f"{LIST}:{item}"


def _item(kind):
    return kind.split(":", 1)[1] if kind.startswith(LIST + ":") else None


def _fits(kind, *wanted):
    return kind in (ANY, NULL) or kind in wanted


class Checker:
    """Infers the result type and refuses what cannot work, before anything is saved."""

    def __init__(self, schema, field_key=None):
        self.schema = schema
        self.stack = [(schema.table.pk, field_key)] if field_key else []  # (table id, key) against loops

    def field_type(self, field, position):
        if field.type == "secret":
            raise FormulaError(_("Geheime Felder können nicht berechnet werden."), position)
        if field.type == "formula":
            marker = (field.table_id, field.key)
            if marker in self.stack:
                raise FormulaError(_("Die Formel hängt von sich selbst ab (über {%(name)s}).") % {"name": field.display_label}, position)
            text = (field.config or {}).get("expression", "")
            self.stack.append(marker)
            try:
                schema = self.schema if field.table_id == self.schema.table.pk else Schema(field.table)
                return Checker(schema).with_stack(self.stack).check(parse(text)) if text else NULL
            except FormulaError as exc:
                if "selbst" in exc.message:
                    raise FormulaError(exc.message, position)
                raise FormulaError(_("{%(name)s} enthält einen Fehler.") % {"name": field.display_label}, position)
            finally:
                self.stack.pop()
        if field.type in ("link", "file", "image"):
            return _list(TEXT)
        return FIELD_TYPES.get(field.type, TEXT)

    def with_stack(self, stack):
        self.stack = stack
        return self

    def check(self, node):
        kind = node[0]
        position = node[-1]
        if kind == "value":
            value = node[1]
            return NULL if value is None else BOOL if isinstance(value, bool) else NUMBER if isinstance(value, Decimal) else TEXT
        if kind == "ref":
            name = node[1]
            if name in self.schema.reverse:
                return _list(TEXT)
            field = self.schema.fields.get(name)
            if field is None:
                raise FormulaError(_("Unbekanntes Feld {%(name)s}.") % {"name": name}, position)
            return self.field_type(field, position)
        if kind == "path":
            table, fields = self.schema.path_fields(node[1], position)
            target = fields.get(node[2])
            if target is None:
                raise FormulaError(_("Unbekanntes Feld {%(name)s}.") % {"name": node[2]}, position)
            inner = Checker(Schema(table)).with_stack(self.stack).field_type(target, position)
            return _list(_item(inner) or inner)
        if kind in ("and", "or"):
            for side in node[1:3]:
                self.check(side)
            return BOOL
        if kind == "not":
            self.check(node[1])
            return BOOL
        if kind == "neg":
            if not _fits(self.check(node[1]), NUMBER):
                raise FormulaError(_("Minus passt nur vor eine Zahl."), position)
            return NUMBER
        if kind == "join":
            self.check(node[1]), self.check(node[2])
            return TEXT
        if kind == "cmp":
            left, right = self.check(node[2]), self.check(node[3])
            if node[1] not in ("=", "!=") and not (left == right or ANY in (left, right) or NULL in (left, right)):
                raise FormulaError(_("Verglichen werden nur Werte derselben Art."), position)
            return BOOL
        if kind == "arith":
            op, left, right = node[1], self.check(node[2]), self.check(node[3])
            if _fits(left, NUMBER) and _fits(right, NUMBER):
                return NUMBER
            if op in "+-" and left in (DATE, DATETIME) and _fits(right, NUMBER):
                return left
            if op == "-" and left == right == DATE:
                return NUMBER
            raise FormulaError(_("Mit „%(op)s“ werden nur Zahlen gerechnet (und Tage zu einem Datum gezählt).") % {"op": op}, position)
        if kind == "call":
            name, args = node[1], node[2]
            spec = FUNCTIONS.get(name)
            if spec is None:
                raise FormulaError(_("Unbekannte Funktion %(name)s().") % {"name": name}, position)
            low, high = spec.arity
            if not low <= len(args) <= (high if high is not None else len(args)):
                raise FormulaError(_("%(name)s() erwartet %(n)s Angaben.") % {"name": name, "n": spec.arity_text}, position)
            return spec.result([self.check(a) for a in args], position)
        raise FormulaError(_("Unbekannter Ausdruck."), position)


def check(text, schema, field_key=None):
    """The result type of a formula (stored form), or FormulaError."""
    return Checker(schema, field_key).check(parse(text))


# ---------------------------------------------------------------- functions

def _number_result(kinds, position, name):
    for kind in kinds:
        if not (_fits(kind, NUMBER) or _item(kind) == NUMBER):
            raise FormulaError(_("%(name)s() rechnet nur mit Zahlen.") % {"name": name}, position)
    return NUMBER


def _need_list(kind, position, name):
    if _item(kind) is None and kind != ANY:
        raise FormulaError(_("%(name)s() braucht eine Verknüpfung, etwa %(example)s.") % {"name": name, "example": "{Ausleihen}"}, position)
    return _item(kind) or ANY


def _need(kind, wanted, position, name):
    if not _fits(kind, *wanted):
        raise FormulaError(_("%(name)s(): Diese Angabe passt nicht.") % {"name": name}, position)


@dataclass
class Function:
    arity: tuple
    result: object
    run: object
    lazy: bool = dataclass_field(default=False)

    @property
    def arity_text(self):
        low, high = self.arity
        return str(low) if low == high else f"{low}–{high}" if high is not None else f"{low}+"


def _decimal(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise FormulaError(_("Hier wird eine Zahl erwartet."))
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise FormulaError(_("Hier wird eine Zahl erwartet."))


def _round(value, places=Decimal(0)):
    value = _decimal(value)
    if value is None:
        return None
    return value.quantize(Decimal(1).scaleb(-int(places or 0)), rounding=ROUND_HALF_UP)


def _flat(values):
    out = []
    for value in values:
        out.extend(value if isinstance(value, list) else [value])
    return out


def _numbers(values):
    return [_decimal(v) for v in _flat(values) if v is not None]


def _text(value):
    from core import fieldtypes as ft

    if isinstance(value, list):
        return ", ".join(_text(v) for v in value if v is not None)
    if isinstance(value, Item):
        return value.title
    if isinstance(value, Decimal):  # as many places as the value has, in the language on screen: 12,50 · 24
        from django.utils import formats

        places = 0 if value == value.to_integral() else max(0, -value.as_tuple().exponent)
        return formats.number_format(value, places, use_l10n=True)
    if value is None:
        return ""
    return str(ft.display(_Spec(TEXT), value, localized=True))


def _days(a, b):
    if a is None or b is None:
        return None
    a = a.date() if isinstance(a, datetime) else a
    b = b.date() if isinstance(b, datetime) else b
    return Decimal((b - a).days)


def _age(born, at=None):
    if born is None:
        return None
    born = born.date() if isinstance(born, datetime) else born
    at = at or timezone.localdate()
    return Decimal(at.year - born.year - ((at.month, at.day) < (born.month, born.day)))


def _minmax(pick):
    def run(*values):
        found = [v for v in _flat(values) if v is not None]
        return pick(found) if found else None
    return run


def _if_type(kinds, position):
    then, otherwise = kinds[1], kinds[2]
    if then == NULL or then == otherwise:
        return otherwise
    if otherwise == NULL:
        return then
    return _same_kind([then, otherwise], position, "if")


def _same_kind(kinds, position, name):
    plain = [(_item(k) or k) for k in kinds if k not in (ANY, NULL)]
    if len(set(plain)) > 1:
        raise FormulaError(_("%(name)s(): Die Angaben sind verschiedener Art.") % {"name": name}, position)
    return plain[0] if plain else ANY


FUNCTIONS = {
    "if": Function((3, 3), lambda k, p: _if_type(k, p), None, lazy=True),
    "isempty": Function((1, 1), lambda k, p: BOOL, lambda v: v in (None, "", [])),
    "coalesce": Function((1, None), lambda k, p: _same_kind(k, p, "coalesce"), lambda *v: next((x for x in v if x not in (None, "", [])), None)),
    "round": Function((1, 2), lambda k, p: _number_result(k, p, "round"), lambda v, n=Decimal(0): _round(v, n)),
    "floor": Function((1, 1), lambda k, p: _number_result(k, p, "floor"), lambda v: None if v is None else _decimal(v).to_integral_value(rounding="ROUND_FLOOR")),
    "ceil": Function((1, 1), lambda k, p: _number_result(k, p, "ceil"), lambda v: None if v is None else _decimal(v).to_integral_value(rounding="ROUND_CEILING")),
    "abs": Function((1, 1), lambda k, p: _number_result(k, p, "abs"), lambda v: None if v is None else abs(_decimal(v))),
    "min": Function((1, None), lambda k, p: _same_kind(k, p, "min"), _minmax(min)),
    "max": Function((1, None), lambda k, p: _same_kind(k, p, "max"), _minmax(max)),
    "len": Function((1, 1), lambda k, p: NUMBER, lambda v: Decimal(len(_text(v)))),
    "upper": Function((1, 1), lambda k, p: TEXT, lambda v: _text(v).upper()),
    "lower": Function((1, 1), lambda k, p: TEXT, lambda v: _text(v).lower()),
    "trim": Function((1, 1), lambda k, p: TEXT, lambda v: _text(v).strip()),
    "concat": Function((1, None), lambda k, p: TEXT, lambda *v: "".join(_text(x) for x in v)),
    "contains": Function((2, 2), lambda k, p: BOOL, lambda a, b: _text(b).casefold() in _text(a).casefold()),
    "today": Function((0, 0), lambda k, p: DATE, lambda: timezone.localdate()),
    "now": Function((0, 0), lambda k, p: DATETIME, lambda: timezone.now()),
    "year": Function((1, 1), lambda k, p: (_need(k[0], (DATE, DATETIME), p, "year"), NUMBER)[1], lambda d: None if d is None else Decimal(d.year)),
    "month": Function((1, 1), lambda k, p: (_need(k[0], (DATE, DATETIME), p, "month"), NUMBER)[1], lambda d: None if d is None else Decimal(d.month)),
    "day": Function((1, 1), lambda k, p: (_need(k[0], (DATE, DATETIME), p, "day"), NUMBER)[1], lambda d: None if d is None else Decimal(d.day)),
    "days": Function((2, 2), lambda k, p: ([_need(x, (DATE, DATETIME), p, "days") for x in k], NUMBER)[1], _days),
    "age": Function((1, 1), lambda k, p: (_need(k[0], (DATE, DATETIME), p, "age"), NUMBER)[1], _age),
    "format": Function((1, 1), lambda k, p: TEXT, lambda v: _text(v)),
    "count": Function((1, 1), lambda k, p: (_need_list(k[0], p, "count"), NUMBER)[1], lambda v: Decimal(len([x for x in (v or []) if x is not None]))),
    "sum": Function((1, 1), lambda k, p: (_need_list(k[0], p, "sum"), _number_result([_item(k[0]) or ANY], p, "sum"))[1], lambda v: sum(_numbers([v]), Decimal(0))),
    "avg": Function((1, 1), lambda k, p: (_need_list(k[0], p, "avg"), _number_result([_item(k[0]) or ANY], p, "avg"))[1],
                    lambda v: (sum(_numbers([v]), Decimal(0)) / len(_numbers([v]))) if _numbers([v]) else None),
    "first": Function((1, 1), lambda k, p: _need_list(k[0], p, "first"), lambda v: next((x for x in (v or []) if x is not None), None)),
    "join": Function((1, 2), lambda k, p: (_need_list(k[0], p, "join"), TEXT)[1], lambda v, sep=", ": str(sep).join(_text(x) for x in (v or []) if x not in (None, ""))),
}


# ---------------------------------------------------------------- computing

@dataclass(frozen=True)
class Item:
    """A linked target in a list: a record of a table, a person or a device."""
    kind: str
    id: object
    title: str


class _Spec:
    def __init__(self, kind, places=None):
        self.type = {NUMBER: "number", DATE: "date", DATETIME: "datetime", BOOL: "bool"}.get(kind, "text")
        self.config = {"decimals": places} if places else {}
        self.required, self.display_label, self.option_values = False, "", []

    def option_label(self, value):
        return value


class Context:
    """What one round of computing needs: the user (whose rights limit what links may read), caches of
    fields, links and target records, and the steps taken. Lists prefetch for all their records at once."""

    def __init__(self, user=None):
        self.user = user
        self.schemas = {}
        self.links = {}      # (record id, field key) -> [target ids]
        self.reverse = {}    # (source table key, link key, target id) -> [records]
        self.records = {}    # id -> Record
        self.readable = None
        self.steps = 0
        self.computing = []  # (table id, field key) of the formulas being computed, against loops
        self.prefetched = set()
        self.trees = {}
        self.target_tables = {}  # link field id -> target table
        self.resolved = {}       # (kind, table id, target id) -> links.resolve entry

    def schema(self, table):
        if table.pk not in self.schemas:
            self.schemas[table.pk] = Schema(table)
        return self.schemas[table.pk]

    def may_read(self, table):
        if self.user is None:
            return True
        if self.readable is None:
            from . import services

            self.readable = {t.pk for t in services.readable(self.user, include_archived=True)}
        return table.pk in self.readable

    def step(self, n=1):
        self.steps += n
        if self.steps > MAX_STEPS:
            raise FormulaError(_("Die Berechnung braucht zu viele Schritte."))

    def prefetch(self, records):
        """Links of these records, and the records of other tables that link to them, in a few queries."""
        from .models import Link

        ids = [r.pk for r in records]
        for record in records:
            self.records[record.pk] = record
        for link in Link.objects.filter(record_id__in=ids).exclude(target_id=None).order_by("order", "created_at"):
            self.links.setdefault((link.record_id, link.field), []).append(link.target_id)
        self.prefetched.update(ids)
        reverse = Link.objects.filter(target_kind="record", target_id__in=ids, record__deleted_at=None).select_related("record__table")
        for link in reverse:
            self.reverse.setdefault((link.record.table.key, link.field, link.target_id), []).append(link.record)
            self.records[link.record_id] = link.record
        for rid in ids:
            self.reverse.setdefault(("*", "*", rid), [])

    def targets(self, record, key):
        cache = (record.pk, key)
        if cache not in self.links:
            if record.pk in self.prefetched:
                return []
            from .models import Link

            self.links[cache] = [l.target_id for l in Link.objects.filter(record=record, field=key).exclude(target_id=None).order_by("order", "created_at")]
        return self.links[cache]

    def linking(self, table_key, link_key, record):
        cache = (table_key, link_key, record.pk)
        if cache not in self.reverse:
            if ("*", "*", record.pk) in self.reverse:
                return []
            from .models import Link

            rows = Link.objects.filter(target_kind="record", target_id=record.pk, field=link_key, record__table__key=table_key,
                                       record__deleted_at=None).select_related("record__table")
            self.reverse[cache] = [l.record for l in rows]
        return self.reverse[cache]

    def target_table(self, field):
        from . import links

        if field.pk not in self.target_tables:
            self.target_tables[field.pk] = links.target_table(field)
        return self.target_tables[field.pk]

    def resolve(self, field, ids):
        """Titles of a link's targets. The first call for a field resolves the targets of every prefetched
        record at once, so a list computes a link formula without a query per row."""
        from . import links

        kind, table = links.kind_of(field), self.target_table(field)
        tid = table.pk if table else None
        missing = {i for i in ids if (kind, tid, i) not in self.resolved}
        if missing:
            missing |= {t for (rid, key), found in self.links.items() if key == field.key for t in found if (kind, tid, t) not in self.resolved}
            missing = list(missing)
            for start in range(0, len(missing), 500):
                for i, entry in links.resolve(self.user, kind, missing[start:start + 500], table).items():
                    self.resolved[(kind, tid, i)] = entry
        return {i: self.resolved[(kind, tid, i)] for i in ids}

    def fetch(self, ids, table):
        from .models import Record

        missing = [i for i in ids if i not in self.records]
        if missing:
            for record in Record.objects.filter(pk__in=missing, table=table).select_related("table"):
                self.records[record.pk] = record
        return [self.records[i] for i in ids if i in self.records and self.records[i].table_id == table.pk]


def _value_of(record, field, context):
    from . import links, services

    if field.type == "formula":
        return compute(record, field, context)
    if field.type == "link":
        ids = context.targets(record, field.key)
        if len(ids) > MAX_LINKED:
            raise FormulaError(_("Zu viele verknüpfte Datensätze."))
        context.step(len(ids))
        found = context.resolve(field, ids)
        return [Item(links.kind_of(field), i, str(found[i]["title"])) for i in ids]
    if field.type in ("file", "image"):
        return [a.name for a in links.files(record, field)]
    if field.type == "multichoice":
        return ", ".join(str(field.option_label(v)) for v in (services.value(record, field) or []))
    if field.type == "choice":
        raw = services.value(record, field)
        return str(field.option_label(raw)) if raw else None
    if field.type == "secret":
        raise FormulaError(_("Geheime Felder können nicht berechnet werden."))
    value = services.value(record, field)
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str) and field.type in ("date", "datetime", "number"):
        raise FormulaError(_("{%(name)s} enthält einen ungültigen Wert.") % {"name": field.display_label})
    return value


def _evaluate(node, record, context):
    context.step()
    kind = node[0]
    position = node[-1]
    try:
        if kind == "value":
            return node[1]
        schema = context.schema(record.table)
        if kind == "ref":
            name = node[1]
            if name in schema.reverse:
                source, link = schema.reverse[name]
                if not context.may_read(source):
                    raise Hidden()
                rows = context.linking(source.key, link.key, record)
                if len(rows) > MAX_LINKED:
                    raise FormulaError(_("Zu viele verknüpfte Datensätze."))
                context.step(len(rows))
                return [Item("record", r.pk, r.title) for r in rows]
            field = schema.fields.get(name)
            if field is None:
                raise FormulaError(_("Unbekanntes Feld {%(name)s}.") % {"name": name})
            return _value_of(record, field, context)
        if kind == "path":
            table, fields = schema.path_fields(node[1])
            target = fields.get(node[2])
            if table is None or target is None:
                raise FormulaError(_("Unbekanntes Feld {%(name)s}.") % {"name": node[2]})
            if not context.may_read(table):
                raise Hidden()
            if node[1] in schema.reverse:  # the records of another table that link here
                others = context.linking(table.key, schema.reverse[node[1]][1].key, record)
            else:
                ids = context.targets(record, node[1])
                if len(ids) > MAX_LINKED:
                    raise FormulaError(_("Zu viele verknüpfte Datensätze."))
                others = context.fetch(ids, table)
            if len(others) > MAX_LINKED:
                raise FormulaError(_("Zu viele verknüpfte Datensätze."))
            values = []
            for other in others:
                context.step()
                value = _value_of(other, target, context)
                values.extend(value if isinstance(value, list) else [value])
            return values
        if kind == "and":
            return bool(_evaluate(node[1], record, context)) and bool(_evaluate(node[2], record, context))
        if kind == "or":
            return bool(_evaluate(node[1], record, context)) or bool(_evaluate(node[2], record, context))
        if kind == "not":
            return not _evaluate(node[1], record, context)
        if kind == "neg":
            value = _evaluate(node[1], record, context)
            return None if value is None else -_decimal(value)
        if kind == "join":
            return _text(_evaluate(node[1], record, context)) + _text(_evaluate(node[2], record, context))
        if kind == "cmp":
            return _compare(node[1], _evaluate(node[2], record, context), _evaluate(node[3], record, context))
        if kind == "arith":
            return _arith(node[1], _evaluate(node[2], record, context), _evaluate(node[3], record, context))
        if kind == "call":
            name, args = node[1], node[2]
            if name == "if":
                return _evaluate(args[1] if _evaluate(args[0], record, context) else args[2], record, context)
            return FUNCTIONS[name].run(*[_evaluate(a, record, context) for a in args])
    except FormulaError as exc:
        if exc.position is None:
            exc.position = position
        raise
    except (InvalidOperation, ArithmeticError, OverflowError, TypeError, ValueError):
        raise FormulaError(_("Diese Werte lassen sich nicht so berechnen."), position)
    raise FormulaError(_("Unbekannter Ausdruck."), position)


def _comparable(value):
    if isinstance(value, list):
        return _text(value)
    if isinstance(value, Item):
        return value.title
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    return value


def _compare(op, left, right):
    left, right = _comparable(left), _comparable(right)
    if left in ("", []):
        left = None
    if right in ("", []):
        right = None
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if left is None or right is None:
        return False
    if isinstance(left, str) or isinstance(right, str):
        left, right = _text(left).casefold(), _text(right).casefold()
    return {"<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[op]


def _arith(op, left, right):
    if left is None or right is None:
        return None
    if isinstance(left, (date, datetime)) and op in "+-" and not isinstance(right, (date, datetime)):
        shift = timedelta(days=int(_decimal(right)))
        return left + shift if op == "+" else left - shift
    if isinstance(left, date) and isinstance(right, date) and op == "-":
        return _days(right, left)
    a, b = _decimal(left), _decimal(right)
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        raise FormulaError(_("Teilen durch null."))
    return a / b


def compute(record, field, context=None):
    """The value of a formula field for one record. Raises FormulaError or Hidden."""
    context = context or Context()
    marker = (field.table_id, field.key)
    if marker in context.computing:
        raise FormulaError(_("Die Formel hängt von sich selbst ab (über {%(name)s}).") % {"name": field.display_label})
    expression = (field.config or {}).get("expression", "")
    if not expression:
        return None
    if not context.computing:
        context.steps = 0  # the limit is per value: a list shares one context across all its records
    context.computing.append(marker)
    try:
        if expression not in context.trees:
            context.trees[expression] = parse(expression)
        return _evaluate(context.trees[expression], record, context)
    finally:
        context.computing.pop()


def display(record, field, context=None):
    """(text, problem): the value as shown, or "#Fehler" with the reason, or "nicht sichtbar"."""
    try:
        value = compute(record, field, context)
    except Hidden:
        return _("nicht sichtbar"), ""
    except FormulaError as exc:
        return "#Fehler", exc.message
    return shown(field, value), ""


def shown(field, value):
    from core import fieldtypes as ft

    result = (field.config or {}).get("result", TEXT)
    if value is None or value == []:
        return ""
    if isinstance(value, list) or isinstance(value, Item):
        return _text(value)
    if isinstance(value, Decimal):
        places = (field.config or {}).get("decimals")
        if places is None:
            places = 0 if value == value.to_integral() else 2
        return str(ft.display(_Spec(NUMBER, places), _round(value, places), localized=True)) if places else str(ft.display(_Spec(NUMBER), int(_round(value)), localized=True))
    if result == BOOL or isinstance(value, bool):
        return str(ft.display(_Spec(BOOL), bool(value), localized=True))
    return str(ft.display(_Spec(TEXT), value, localized=True))


def grid_value(field, value):
    """The value as a list column wants it: numbers as numbers, dates as ISO text, everything else as text."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    return shown(field, value) if value is not None else None


def uses_links(field):
    """Whether a formula reads links, files or other tables (then it cannot name records: its value changes
    when other records change, and a record's cached title would go stale)."""
    from .models import Field

    expression = (field.config or {}).get("expression", "")
    if not expression or "{@" in expression or re.search(r"\}\s*\.\s*\{", expression):
        return bool(expression)
    keys = {inner for _start, _end, inner in _ref_tokens(expression)}
    for other in Field.objects.filter(table_id=field.table_id, key__in=keys):
        if other.type in ("link", "file", "image") or (other.type == "formula" and other.pk != field.pk and uses_links(other)):
            return True
    return False


def presets(table):
    """Ready-made computations for the field dialog: (label, formula as typed). Each can be changed afterwards."""
    schema = Schema(table)
    fields = list(schema.fields.values())
    name = lambda f: "{" + f.display_label + "}"
    out = []
    for link in [f for f in fields if f.type == "link"]:
        out.append((_("Anzahl verknüpfter Datensätze: %(field)s") % {"field": link.display_label}, f"count({name(link)})"))
        if (link.config or {}).get("target") == "record":
            for target in schema.target(link)[1].values():
                if target.type == "number":
                    out.append((_("Summe: %(link)s → %(field)s") % {"link": link.display_label, "field": target.display_label},
                                f"sum({name(link)}.{name(target)})"))
    for key in schema.reverse:
        label = schema.reverse_name(key)
        out.append((_("Anzahl verknüpfter Datensätze: %(field)s") % {"field": label}, f"count({{{label}}})"))
    dates = [f for f in fields if f.type in ("date", "datetime")]
    if len(dates) >= 2:
        out.append((_("Tage zwischen zwei Daten"), f"days({name(dates[0])}, {name(dates[1])})"))
    elif dates:
        out.append((_("Tage bis heute"), f"days({name(dates[0])}, today())"))
    for born in dates[:1]:
        out.append((_("Alter aus Geburtsdatum"), f"age({name(born)})"))
    texts = [f for f in fields if f.type == "text"]
    if len(texts) >= 2:
        out.append((_("Text zusammensetzen"), f'{name(texts[0])} & " " & {name(texts[1])}'))
    return out


def preview(table, expression, limit=3, user=None):
    """The first records' values for the dialog: [(record title, text, problem)]."""
    from .models import Field, Record

    probe = Field(table=table, key="__preview__", label="", type="formula", config={"expression": expression})
    context = Context(user)
    out = []
    for record in Record.objects.filter(table=table).select_related("table").order_by("title")[:limit]:
        text, problem = display(record, probe, context)
        out.append((record.title, text, problem))
    return out
