"""proplib — a tiny, stdlib-only property-based testing harness.

Hypothesis-grade ergonomics without the dependency: declare a property as a
function of generated inputs and the harness throws edge cases first, then
random inputs, and *shrinks* any counterexample toward a minimal failing
case. Used by the coding verifier's property-test phase and by scaffolded
tests (``trishula/coding/testgen.py``).

Example:
    from trishula.coding.proplib import check, ints, lists
    check(lambda xs: sorted(sorted(xs)) == sorted(xs), lists(ints(-50, 50)))
"""

from __future__ import annotations

import random
import string
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, List, Sequence, Tuple

# ── strategies ───────────────────────────────────────────────────────────────
#
# A strategy is a callable ``(rng) -> value``. ``edge_cases`` lists values to
# try before random generation (boundaries and degenerate inputs).


@dataclass
class Strategy:
    gen: Callable[[random.Random], Any]
    edges: List[Any] = field(default_factory=list)

    def __call__(self, rng: random.Random) -> Any:
        return self.gen(rng)


def ints(lo: int = -1000, hi: int = 1000) -> Strategy:
    return Strategy(
        lambda r: r.randint(lo, hi),
        [0, 1, -1, lo, hi, min(hi, max(lo, 2)), max(lo, min(hi, -2))],
    )


def positive_ints(hi: int = 10_000) -> Strategy:
    return ints(1, hi)


def floats(lo: float = -1e6, hi: float = 1e6) -> Strategy:
    return Strategy(
        lambda r: round(r.uniform(lo, hi), 6),
        [0.0, -0.0, 1.0, -1.0, lo, hi, 1e-9],
    )


def booleans() -> Strategy:
    return Strategy(lambda r: r.choice((True, False)), [True, False])


def strs(maxlen: int = 24, *, unicode_ok: bool = True) -> Strategy:
    alphabet = string.ascii_letters + string.digits + " _-.!?" + (
        "éλΩ☤中" if unicode_ok else "")

    def _gen(r: random.Random) -> str:
        return "".join(r.choice(alphabet) for _ in range(r.randint(0, maxlen)))

    return Strategy(_gen, ["", " ", "a", "0", "\n"])


def lists(elem: Strategy, maxlen: int = 20) -> Strategy:
    def _gen(r: random.Random) -> list:
        return [elem(r) for _ in range(r.randint(0, maxlen))]

    edges_lists: list = [[], [elem.gen(random.Random(0))]]
    return Strategy(_gen, edges_lists)


def dicts(key: Strategy, val: Strategy, maxlen: int = 8) -> Strategy:
    def _gen(r: random.Random) -> dict:
        return {key(r): val(r) for _ in range(r.randint(0, maxlen))}

    return Strategy(_gen, [{}])


def choice(*values: Any) -> Strategy:
    return Strategy(lambda r: r.choice(list(values)), list(values))


# ── shrinking ────────────────────────────────────────────────────────────────


def _shrink(value: Any) -> List[Any]:
    """Produce strictly-smaller candidates for ``value`` (best first)."""
    if isinstance(value, bool):  # bool is an int subclass — check first
        return [] if value is False else [False]
    if isinstance(value, int):
        if value == 0:
            return []
        candidates = [0, 1, -1, abs(value) // 2, value - (1 if value > 0 else -1)]
        return [c for c in candidates if c != value]
    if isinstance(value, float):
        if value == 0:
            return []
        as_int = int(value)
        out = [0.0]
        if float(as_int) != value:
            out.append(as_int)
        return out
    if isinstance(value, str):
        if not value:
            return []
        return ["", "a", value[: len(value) // 2]]
    if isinstance(value, list) and value:
        half = value[: len(value) // 2]
        return [c for c in ([value[:1], half] + [[v] for v in value[:3]]) if c != value]
    if isinstance(value, dict) and value:
        k = next(iter(value))
        return [{}, {k: value[k]}]
    return []


def _fails(property_fn: Callable, args: tuple) -> bool:
    try:
        result = property_fn(*args)
    except AssertionError:
        return True
    except Exception:  # noqa: BLE001 - any raise = property violated
        return True
    return result is False


def _shrink_args(property_fn: Callable, args: tuple) -> tuple:
    """Greedily shrink each argument while the property still fails.

    Terminates when a full pass produces no smaller failing value.
    """
    args = list(args)
    changed = True
    while changed:
        changed = False
        for i, value in enumerate(args):
            for candidate in _shrink(value):
                if candidate == value:
                    continue
                trial = list(args)
                trial[i] = candidate
                if _fails(property_fn, tuple(trial)):
                    args = trial
                    changed = True
                    break
    return tuple(args)


# ── runner ───────────────────────────────────────────────────────────────────


@dataclass
class PropertyResult:
    name: str
    ok: bool
    iterations: int = 0
    counterexample: tuple = ()
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "ok": self.ok, "iterations": self.iterations,
            "counterexample": [repr(c) for c in self.counterexample],
            "error": self.error,
        }


def check(
    property_fn: Callable,
    *strategies: Strategy,
    iterations: int = 100,
    name: str = "",
    seed: int = 0,
) -> PropertyResult:
    """Check ``property_fn(*generated_args)`` holds for many inputs.

    Returns a :class:`PropertyResult`; raises nothing on failure.
    """
    rng = random.Random(seed)
    label = name or getattr(property_fn, "__name__", "property")
    n = 0
    # 1. edge cases first: for each argument position, try each of that
    # strategy's boundary values while the other positions use a fixed
    # benign value (first edge).
    edge_matrix: List[tuple] = []
    if strategies:
        base = tuple((s.edges[0] if s.edges else s(rng)) for s in strategies)
        edge_matrix.append(base)
        for si, strat in enumerate(strategies):
            for edge in strat.edges:
                row = tuple(edge if j == si else base[j]
                            for j in range(len(strategies)))
                edge_matrix.append(row)
    else:
        edge_matrix = [()]
    for args in edge_matrix:
        n += 1
        if _fails(property_fn, args):
            shrunk = _shrink_args(property_fn, args) if args else args
            return PropertyResult(label, False, n, shrunk,
                                  error=f"counterexample after {n} cases: {shrunk!r}")
    # 2. random cases
    for _ in range(iterations):
        args = tuple(s(rng) for s in strategies)
        n += 1
        if _fails(property_fn, args):
            shrunk = _shrink_args(property_fn, args)
            return PropertyResult(label, False, n, shrunk,
                                  error=f"counterexample after {n} cases: {shrunk!r}")
    return PropertyResult(label, True, n)


# ── file-based discovery (matches @property_test decorated functions) ────────


def property_test(*strategies: Strategy, iterations: int = 100):
    """Decorator marking a function as a property test.

    The decorated function takes generated parameters; zero-arg functions are
    run as plain assertions (standard test style).
    """

    def deco(fn: Callable) -> Callable:
        fn._trishula_property = (strategies, iterations)  # type: ignore[attr-defined]
        return fn

    return deco


def run_property_file(path: str) -> List[PropertyResult]:
    """Exec a test file, run every @property_test / test_* function."""
    namespace: dict = {}
    with open(path, "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), path, "exec"), namespace)  # noqa: S102
    results: List[PropertyResult] = []
    for name, obj in sorted(namespace.items()):
        if not callable(obj):
            continue
        marker = getattr(obj, "_trishula_property", None)
        if marker is not None:
            strategies, iters = marker
            results.append(check(obj, *strategies, iterations=iters, name=name))
        elif name.startswith("test_") and not marker:
            try:
                obj()
                results.append(PropertyResult(name, True, 1))
            except Exception as exc:  # noqa: BLE001
                results.append(PropertyResult(name, False, 1, error=f"{type(exc).__name__}: {exc}"))
    return results
