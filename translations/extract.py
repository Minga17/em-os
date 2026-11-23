"""List every text marked for translation:   python translations/extract.py

Finds _("..."), _n("...", "..."), N_("..."), translate(lang, "...") and translate_n(lang, "...", "...")
in templates/ and the Python files. Tests use keys() to check every language has every text.
"""
import ast
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_CALL = re.compile(r'''(?<![\w.])(_n|_)\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')'''
                           r'''(?:\s*,\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'))?''', re.S)
FIRST_TEXT = {"_": 0, "N_": 0, "_n": 0, "translate": 1, "translate_n": 1}


def from_python(path, found):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        if name not in FIRST_TEXT:
            continue
        i = FIRST_TEXT[name]
        args = node.args
        if len(args) <= i or not (isinstance(args[i], ast.Constant) and isinstance(args[i].value, str)):
            continue
        plural = None
        if name in ("_n", "translate_n"):
            if len(args) <= i + 1 or not isinstance(args[i + 1], ast.Constant):
                continue
            plural = args[i + 1].value
        found.setdefault(args[i].value, plural)


def from_template(path, found):
    text = open(path, encoding="utf-8").read()
    for m in TEMPLATE_CALL.finditer(text):
        kind, first, second = m.groups()
        try:
            singular = ast.literal_eval(first)
            plural = ast.literal_eval(second) if second and kind == "_n" else None
        except (ValueError, SyntaxError):
            continue
        if kind == "_n" and plural is None:
            continue
        found.setdefault(singular, plural)


def keys():
    """{english text: english plural or None}"""
    found = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "*.py"))):
        from_python(path, found)
    for path in sorted(glob.glob(os.path.join(ROOT, "templates", "**", "*.html"), recursive=True)):
        from_template(path, found)
    return found


if __name__ == "__main__":
    for k, v in keys().items():
        print(repr(k) + (f"  || {v!r}" if v else ""))
