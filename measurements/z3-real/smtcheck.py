# An SMT-LIB 2 evaluator that shares no code with Z3: it reads the benchmark file itself and
# evaluates every `assert` under a given model, in exact arithmetic (Python int and Fraction).
# This is what makes a `sat` verdict checkable: the model either satisfies the original file's
# assertions, as written, or it does not.
#
# Scope: the theories of the logics this measurement runs (QF_IDL, QF_RDL, QF_LRA): the Core theory
# (and or not => xor = distinct ite, let, :named), Ints and Reals (+ - * / div mod abs <= < >= >
# to_real to_int is_int), declare-fun/declare-const of arity 0, and define-fun. Anything else
# raises Unsupported, which the caller reports as "not checked" -- never as a pass.
#
#   evaluate(path, model) -> list of bools, one per `assert`, in file order
#   declarations(path)    -> {name: sort} for every declared constant
import re
import sys
import threading
from fractions import Fraction

TOK = re.compile(r'\s+|;[^\n]*|(\()|(\))|(\|[^|]*\|)|("(?:[^"]|"")*")|([^\s()|";]+)')
NUM = re.compile(r"[0-9]+$")
DEC = re.compile(r"[0-9]+\.[0-9]+$")


class Unsupported(Exception):
    pass


class Undetermined(Exception):
    """A value the model does not fix in this evaluator (division by zero is uninterpreted)."""


def parse(text):
    """Iterative s-expression reader: deep `let` nesting must not hit a recursion limit."""
    stack, cur = [], []
    for m in TOK.finditer(text):
        lp, rp, q, s, a = m.groups()
        if lp:
            stack.append(cur)
            cur = []
        elif rp:
            if not stack:
                raise Unsupported("unbalanced ')'")
            done, cur = cur, stack.pop()
            cur.append(done)
        elif q is not None:
            cur.append(("Q", q[1:-1]))      # |quoted| symbol: the same symbol as unquoted
        elif s is not None:
            cur.append(("S", s))
        elif a is not None:
            cur.append(a)
    if stack:
        raise Unsupported("unbalanced '('")
    return cur


def name(t):
    if isinstance(t, tuple) and t[0] == "Q":
        return t[1]
    if isinstance(t, str):
        return t
    raise Unsupported("expected a symbol, got %r" % (t,))


def sortname(t):
    if isinstance(t, list):
        raise Unsupported("parametric sort %r" % (t,))
    return name(t)


def edivmod(a, b):
    """SMT-LIB Ints div/mod: a = b*q + r with 0 <= r < |b|."""
    if b == 0:
        raise Undetermined("div/mod by zero")
    q = a // b if b > 0 else -((-a) // b)
    r = a - b * q
    if r < 0:   # b < 0 and floor division left a negative remainder
        q, r = q + 1, r - b
    return q, r


class Evaluator:
    def __init__(self, model):
        self.g = dict(model)      # declared constants -> value; then :named terms
        self.funs = {}            # define-fun name -> (params, body)
        self.local = {}           # let / define-fun parameter scope

    def lookup(self, n):
        if n in self.local:
            return self.local[n]
        if n in self.g:
            return self.g[n]
        if n in self.funs and not self.funs[n][0]:
            return self.call(n, [])
        if n == "true":
            return True
        if n == "false":
            return False
        raise Unsupported("unbound symbol %r" % n)

    def call(self, n, args):
        params, body = self.funs[n]
        if len(params) != len(args):
            raise Unsupported("arity of %s" % n)
        saved = self.local
        self.local = dict(zip(params, args))
        try:
            return self.ev(body)
        finally:
            self.local = saved

    def ev(self, t):
        if isinstance(t, str):
            if NUM.match(t):
                return int(t)
            if DEC.match(t):
                return Fraction(t)
            return self.lookup(t)
        if isinstance(t, tuple):
            if t[0] == "Q":
                return self.lookup(t[1])
            raise Unsupported("string literal")
        if not t:
            raise Unsupported("empty application")
        h = t[0]
        if isinstance(h, list):
            raise Unsupported("indexed or qualified identifier %r" % (h,))
        op = name(h)
        a = t[1:]
        if op == "let":
            vals = [(name(b[0]), self.ev(b[1])) for b in a[0]]   # parallel let: all in the outer scope
            saved = {}
            for k, v in vals:
                saved[k] = self.local.get(k, _MISSING)
                self.local[k] = v
            try:
                return self.ev(a[1])
            finally:
                for k, v in saved.items():
                    if v is _MISSING:
                        del self.local[k]
                    else:
                        self.local[k] = v
        if op == "!":
            v = self.ev(a[0])
            for i in range(1, len(a) - 1):
                if a[i] == ":named":
                    self.g[name(a[i + 1])] = v
            return v
        if op == "ite":
            return self.ev(a[1]) if self.ev(a[0]) else self.ev(a[2])
        if op == "and":
            for x in a:
                if not self.ev(x):
                    return False
            return True
        if op == "or":
            for x in a:
                if self.ev(x):
                    return True
            return False
        if op in ("forall", "exists"):
            raise Unsupported("quantifier")
        v = [self.ev(x) for x in a]
        if op == "not":
            return not v[0]
        if op == "=>":
            r = v[-1]
            for x in reversed(v[:-1]):
                r = (not x) or r
            return r
        if op == "xor":
            r = v[0]
            for x in v[1:]:
                r = r != x
            return r
        if op == "=":
            return all(v[i] == v[i + 1] for i in range(len(v) - 1))
        if op == "distinct":
            return all(v[i] != v[j] for i in range(len(v)) for j in range(i + 1, len(v)))
        if op in ("<=", "<", ">=", ">"):
            cmp = {"<=": lambda x, y: x <= y, "<": lambda x, y: x < y,
                   ">=": lambda x, y: x >= y, ">": lambda x, y: x > y}[op]
            return all(cmp(v[i], v[i + 1]) for i in range(len(v) - 1))
        if op == "+":
            r = 0
            for x in v:
                r += x
            return r
        if op == "-":
            if len(v) == 1:
                return -v[0]
            r = v[0]
            for x in v[1:]:
                r -= x
            return r
        if op == "*":
            r = 1
            for x in v:
                r *= x
            return r
        if op == "/":
            r = Fraction(v[0])
            for x in v[1:]:
                if x == 0:
                    raise Undetermined("real division by zero")
                r /= x
            return r
        if op == "div":
            r = v[0]
            for x in v[1:]:
                r = edivmod(r, x)[0]
            return r
        if op == "mod":
            return edivmod(v[0], v[1])[1]
        if op == "abs":
            return abs(v[0])
        if op == "to_real":
            return Fraction(v[0])
        if op == "to_int":
            return int(Fraction(v[0]).__floor__())
        if op == "is_int":
            return Fraction(v[0]).denominator == 1
        if op in self.funs:
            return self.call(op, v)
        raise Unsupported("operator %r" % op)


_MISSING = object()


def commands(path):
    with open(path, encoding="utf-8", errors="strict") as fh:
        return parse(fh.read())


def declarations(path, cmds=None):
    out = {}
    for c in cmds if cmds is not None else commands(path):
        if not isinstance(c, list) or not c:
            continue
        k = c[0]
        if k == "declare-fun":
            if c[2]:
                raise Unsupported("declare-fun with arguments (uninterpreted function)")
            out[name(c[1])] = sortname(c[3])
        elif k == "declare-const":
            out[name(c[1])] = sortname(c[2])
    return out


def evaluate(path, model, cmds=None):
    """One bool per `assert`, in file order, under `model` (name -> int/Fraction/bool)."""
    e = Evaluator(model)
    res = []
    for c in cmds if cmds is not None else commands(path):
        if not isinstance(c, list) or not c:
            raise Unsupported("top-level atom")
        k = c[0]
        if k == "assert":
            res.append(bool(e.ev(c[1])))
        elif k == "define-fun":
            e.funs[name(c[1])] = ([name(p[0]) for p in c[2]], c[4])
        elif k in ("set-info", "set-logic", "set-option", "declare-fun", "declare-const",
                   "check-sat", "exit", "get-model", "get-value", "get-info", "get-unsat-core"):
            continue
        else:
            raise Unsupported("command %r" % (k,))
    return res


def run_deep(fn, *args):
    """Run fn in a thread with a large stack and recursion limit (nested terms recurse in ev)."""
    sys.setrecursionlimit(1_000_000)
    threading.stack_size(1 << 30)
    box = {}

    def go():
        try:
            box["r"] = fn(*args)
        except BaseException as ex:  # re-raised in the caller's thread
            box["e"] = ex
    t = threading.Thread(target=go)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box["r"]
