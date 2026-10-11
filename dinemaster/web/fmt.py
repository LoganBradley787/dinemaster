"""Text formatters shared by pages and templates. `register(env)` adds them as Jinja filters.

In a template: `{{ x|num }}`, `{{ x|money }}`, `{{ x|cents }}`, `{{ d|day }}`, `{{ n|ago }}`,
`{{ p|chance }}`, `{{ n|plural("meal") }}`, `{{ x|signed }}`, `{{ x|pct }}`, `{{ d|weekday }}`.
In Python: `from dinemaster.web import fmt` then `fmt.money(12.5)`.
"""

from __future__ import annotations

from datetime import date

from ..tiles import ago, cents, chance, clip, day, money, num, plural, span

__all__ = ["ago", "cents", "chance", "clip", "day", "money", "num", "plural", "span", "signed", "pct", "weekday", "between", "register"]


def signed(x: float | None, digits: int = 1) -> str:
    """A difference with its sign: "+3.2", "-1", "0"; "n/a" for None."""
    if x is None:
        return "n/a"
    text = num(abs(x), digits)
    if float(text.replace(",", "")) == 0:
        return "0"
    return ("+" if x > 0 else "-") + text


def pct(x: float | None) -> str:
    """A 0..1 share as a whole percent: "43%"."""
    return "n/a" if x is None else f"{x * 100:.0f}%"


def weekday(d: date) -> str:
    """"Sunday, Oct 11"."""
    return f"{d:%A}, {day(d)}"


def between(lo: float, hi: float, each=lambda x: num(x, 0)) -> str:
    """A range in words: "20 to 31", or just "20" when both ends read the same."""
    a, b = each(lo), each(hi)
    return a if a == b else f"{a} to {b}"


def register(env) -> None:
    env.filters.update(
        num=num, money=money, cents=cents, day=day, ago=ago, chance=chance, plural=plural, span=span,
        signed=signed, pct=pct, weekday=weekday,
    )  # fmt: skip
