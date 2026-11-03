"""Guards for the window's DPI scaling (Change 5 of
docs/specs/2026-11-02-gui-dpi-awareness-design.md).

Every pixel constant in claude_code_sessions_gui.py is a logical pixel and
must reach Tk through the window's one factor - `self.px(n)`, `app.px(n)`
or `_px(n, scale)`. A constant that skips it is invisible at 100 % and
wrong at 250 %, on a machine the CI never runs, so this test reads the
source instead: pure ast, nothing imported from the window, no tkinter.

What it does not catch, said plainly: a literal first bound to a name or
an attribute and then passed (`padx=self.GAP`). Review catches that.
"""
import ast
import os

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "claude_code_sessions_gui.py")

# Keyword arguments whose values are pixels.
PIXEL_KW = {"padx", "pady", "ipadx", "ipady", "padding", "wraplength",
            "yscrollincrement", "xscrollincrement", "borderwidth",
            "highlightthickness", "margin"}

# The functions whose geometry arithmetic the spec's inventory found, plus
# the module's size functions: a literal added, subtracted, passed to
# max/min or given as `default=` there is a pixel.
GEOMETRY = {"_wrap_to", "_holds_min_px", "_clamp_sash", "_render_holds",
            "_fit", "_initial_geometry", "_min_size", "_work_area"}

# Scaled call sites in widget code when DPI awareness landed (87: 85
# rewritten literals and PAD uses, _Dialog's px(wrap), and the vista tab
# inset). A floor that only ever rises, so a refactor that hides sites from
# this walk cannot pass by finding nothing.
PX_FLOOR = 87


def _annotate(tree):
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node
    return tree


def _is_helper(node):
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "_px")
        or (isinstance(node.func, ast.Attribute) and node.func.attr == "px"))


def _ancestors(node):
    p = getattr(node, "_parent", None)
    while p is not None:
        yield p
        p = getattr(p, "_parent", None)


def _scaled(node):
    return any(_is_helper(a) for a in _ancestors(node))


def _counts_not_pixels(node):
    """Inside an Entry/Text `width=`/`height=` - characters and lines,
    which the font scales (min(len(mappings), 14) among them)."""
    for a in _ancestors(node):
        if isinstance(a, ast.keyword) and a.arg in ("width", "height"):
            call = getattr(a, "_parent", None)
            func = getattr(call, "func", None)
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in ("Entry", "Text"):
                return True
    return False


def _nonzero_number(node):
    return (isinstance(node, ast.Constant) and not isinstance(node.value, bool)
            and isinstance(node.value, (int, float)) and node.value != 0)


def violations(source):
    """(line, column, rule, detail) for every pixel constant that skips the
    factor."""
    tree = _annotate(ast.parse(source))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg not in PIXEL_KW:
                continue
            for sub in ast.walk(kw.value):
                bad = _nonzero_number(sub) or (
                    isinstance(sub, ast.Name) and sub.id == "PAD")
                if bad and not _scaled(sub):
                    out.append((sub.lineno, sub.col_offset, "pixel argument",
                                kw.arg))
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name in GEOMETRY):
            continue
        for node in ast.walk(fn):
            loose = []
            if isinstance(node, ast.BinOp) and isinstance(node.op,
                                                          (ast.Add, ast.Sub)):
                loose += [node.left, node.right]
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("max", "min")):
                loose += list(node.args)
                loose += [k.value for k in node.keywords if k.arg == "default"]
            for operand in loose:
                if (_nonzero_number(operand) and not _scaled(operand)
                        and not _counts_not_pixels(operand)):
                    out.append((operand.lineno, operand.col_offset,
                                "geometry arithmetic", fn.name))
    return sorted(set(out))


def px_sites(source):
    """The `.px(...)` calls - the scaled sites in widget code."""
    return [n for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "px"]


def _source():
    with open(SRC, encoding="utf-8") as fh:
        return fh.read()


def test_the_guard_fires_on_unscaled_constants():
    """The rules are live: a bad source is caught, its scaled twin is not."""
    bad = ("def build(w):\n"
           "    w.pack(padx=6, pady=(0, PAD))\n"
           "def _clamp_sash(self, lo, total):\n"
           "    return max(lo, total - 8, default=40)\n")
    found = violations(bad)
    assert [(line, rule) for line, _col, rule, _d in found] == [
        (2, "pixel argument"), (2, "pixel argument"),
        (4, "geometry arithmetic"), (4, "geometry arithmetic")]
    good = ("def build(w, self):\n"
            "    w.pack(padx=self.px(6), pady=(0, self.px(PAD)))\n"
            "    t = Text(w, height=min(n, 14), width=100, borderwidth=self.px(1))\n"
            "def _clamp_sash(self, lo, total):\n"
            "    return max(lo, total - self.px(8), default=self.px(40))\n")
    assert violations(good) == []


def test_every_pixel_constant_goes_through_the_factor():
    found = violations(_source())
    assert found == [], "\n".join("line %d col %d: %s (%s)" % f for f in found)


def test_the_factor_reaches_every_landed_site():
    assert len(px_sites(_source())) >= PX_FLOOR
