"""Разбор HTML-форм для автоматических проверок: какие поля есть и что отправит браузер по умолчанию."""
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

TEXT_TYPES = {"text", "search", "email", "url", "tel", "number", "date", "time", None, ""}


@dataclass
class Field:
    name: str
    tag: str                      # input | select | textarea
    type: str | None = None       # для input
    value: str = ""
    checked: bool = False
    options: list = field(default_factory=list)   # для select и группы radio: [(значение, выбрано)]


@dataclass
class Form:
    action: str
    method: str
    fields: list

    def names(self) -> list[str]:
        out = []
        for f in self.fields:
            if f.name not in out:
                out.append(f.name)
        return out

    def radios(self) -> dict[str, list[tuple[str, bool]]]:
        groups: dict[str, list[tuple[str, bool]]] = {}
        for f in self.fields:
            if f.tag == "input" and f.type == "radio":
                groups.setdefault(f.name, []).append((f.value, f.checked))
        return groups

    def selects(self) -> dict[str, list[tuple[str, bool]]]:
        return {f.name: f.options for f in self.fields if f.tag == "select"}

    def text_fields(self) -> list[Field]:
        return [f for f in self.fields if (f.tag == "textarea" or (f.tag == "input" and f.type in TEXT_TYPES))]

    def chosen(self, name: str) -> str | None:
        """Что сейчас выбрано в группе radio или в select."""
        radios = self.radios().get(name)
        if radios:
            return next((v for v, on in radios if on), None)
        options = self.selects().get(name)
        if options:
            return next((v for v, on in options if on), options[0][0])
        return None

    def text_value(self, name: str) -> str | None:
        for f in self.text_fields():
            if f.name == name:
                return f.value
        return None

    def defaults(self) -> dict[str, str]:
        """Данные, которые браузер отправит без изменений."""
        data: dict[str, str] = {}
        for f in self.fields:
            if f.tag == "input" and f.type in ("radio", "checkbox"):
                if f.checked:
                    data[f.name] = f.value
            elif f.tag == "input" and f.type in ("submit", "button", "reset", "file", "image"):
                continue
            elif f.tag == "select":
                sel = [v for v, on in f.options if on] or [v for v, _ in f.options[:1]]
                if sel:
                    data[f.name] = sel[0]
            else:
                data[f.name] = f.value
        return data


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self._form: Form | None = None
        self._select: Field | None = None
        self._option: list | None = None
        self._textarea: Field | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._form = Form(action=a.get("action", ""), method=(a.get("method") or "get").lower(), fields=[])
            self.forms.append(self._form)
            return
        if self._form is None or (tag in ("input", "select", "textarea") and not a.get("name")):
            if tag == "option" and self._select is not None:
                self._option = [a.get("value"), "selected" in a, ""]
            return
        if tag == "input":
            self._form.fields.append(Field(a["name"], "input", (a.get("type") or "text").lower(), a.get("value") or "",
                                           "checked" in a))
        elif tag == "select":
            self._select = Field(a["name"], "select")
            self._form.fields.append(self._select)
        elif tag == "option" and self._select is not None:
            self._option = [a.get("value"), "selected" in a, ""]
        elif tag == "textarea":
            self._textarea = Field(a["name"], "textarea")
            self._form.fields.append(self._textarea)

    def handle_data(self, data):
        if self._textarea is not None:
            self._textarea.value += data
        if self._option is not None:
            self._option[2] += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "option" and self._option is not None and self._select is not None:
            value, selected, text = self._option
            self._select.options.append((value if value is not None else text.strip(), selected))
            self._option = None
        elif tag == "textarea":
            if self._textarea is not None:
                self._textarea.value = self._textarea.value.removeprefix("\n")
            self._textarea = None


def parse_forms(html: str) -> list[Form]:
    p = _Parser()
    p.feed(html)
    return p.forms


def form_key(f: Form) -> tuple:
    """Одна и та же форма на разных страницах (например, у каждой заявки) считается один раз."""
    return f.method, re.sub(r"\d+", "N", f.action.split("?")[0]), tuple(sorted(f.names()))
