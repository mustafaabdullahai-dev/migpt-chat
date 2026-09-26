"""Graph node: dynamic universal calculation engine.

Resolves a natural-language request into one of several computation plans and
executes it through SAFE tools (AST-whitelisted math evaluator, pint unit
converter). Never executes untrusted code.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.agents.nodes.helpers import run_tool
from app.agents.state import AgentState
from app.core.config import settings
from app.models.schemas import CalculationResult, ToolRun
from app.services.llm import get_llm
from app.services.cache import cache_key, get_cache
from app.services.stream import emit_status

_CALC_TTL = 86400


def _calc_cache_key(query: str) -> str:
    return cache_key("calc", " ".join(query.lower().split()))


def _calc_cache_get(query: str) -> dict | None:
    """Repeated-calculations cache: identical queries run once are served from cache."""
    cache = get_cache()
    if cache is None:
        return None
    hit = cache.get(_calc_cache_key(query))
    if not isinstance(hit, dict) or "answer" not in hit:
        return None
    calc = None
    if hit.get("calculation") is not None:
        try:
            calc = CalculationResult.model_validate(hit["calculation"])
        except Exception:
            calc = None
    return {
        "calculation": calc,
        "answer": hit["answer"],
        "confidence": float(hit.get("confidence", 0.4) or 0.4),
        "errors": [],
        "tool_runs": [],
    }


def _calc_cache_put(query: str, result: dict) -> None:
    cache = get_cache()
    if cache is None:
        return
    cache.set(
        _calc_cache_key(query),
        {
            "answer": result["answer"],
            "confidence": result["confidence"],
            "calculation": result["calculation"].model_dump(mode="json") if result.get("calculation") else None,
        },
        ttl=_CALC_TTL,
    )

_NUM = r"\d+(?:[,.]\d+)*"
_PURE = re.compile(r"^[0-9().+\-*/%^!,=<>\s]+$")
_OPS = re.compile(r"[+\-*/%^]")
_TERMS = re.compile(r"[\d][\d ,.]*")


class _CalcPlan(BaseModel):
    """Structured output from the LLM when the deterministic fast path can't parse a query."""

    type: Literal["expression", "unit", "loan", "none"] = Field(
        description="expression | unit | loan | none"
    )
    expression: str | None = Field(
        default=None,
        description="Python-safe expression using * / ** ( ) and allowed functions/numpy",
    )
    label: str | None = Field(default=None, description="Short human label of what was computed")
    assumptions: list[str] | None = Field(
        default=None,
        description="Values/models/assumptions you DECIDED, especially when the request used placeholders",
    )
    value: float | None = None
    from_unit: str | None = None
    to_unit: str | None = None
    principal: float | None = None
    rate: float | None = None
    months: int | None = None
    payments_per_year: int | None = Field(default=12)


# --------------------------------------------------------------------------
# Plan building (deterministic fast-path first, LLM as fallback)
# --------------------------------------------------------------------------
def plan_calculation(query: str) -> dict:
    q = query.strip().lower()
    if not q:
        return _plan_none("Empty request.")

    # 1) pure math expression (allow ^, !, commas-as-thousands)
    if _PURE.match(q) and _OPS.search(q):
        expr = _translate_math(q.replace(",", ""))
        return {"kind": "math", "expression": expr, "label": expr}

    # 2) "17.5% of 85000"
    if m := re.search(rf"([\d.,]+)\s*%\s*of\s*({_NUM})\b", q):
        pct = _num(m.group(1))
        base = _num(m.group(2))
        r = (pct / 100.0) * base
        return {
            "kind": "math",
            "expression": f"({m.group(1).replace(',','')}/100)*{m.group(2).replace(',','')}",
            "label": f"{pct:g}% of {base:g}",
            "headline": f"{pct:g}% of {base:g}",
            "fixed_result": r,
        }

    # 3) word arithmetic ("2 to the power 10 plus 5")
    word_expr = _words_to_expression(q)
    if word_expr:
        return {"kind": "math", "expression": word_expr, "label": word_expr}

    # 4) unit conversion ("convert 100 km to miles", "how many feet in 2 meters")
    if u := _try_units(q):
        return u

    # 5) loan amortization (French method) — multi-step schedule
    if loan := _try_loan(q):
        return loan

    # 6) common geometry / finance formulas
    if f := _try_formula(q):
        return f

    # 7) numpy list/vector/matrix operations described in words
    if n := _try_numpy(q):
        return n

    # 8) embedded pure subexpression ("calculate 2**10 + 3")
    embedded = _embedded_expression(q)
    if embedded:
        return {"kind": "math", "expression": embedded, "label": embedded}

    # 9) LLM decision: parse the request itself, pick values for placeholders,
    #    and produce a plan (expression / loan / units) with stated assumptions.
    plan = _llm_plan(q)
    if plan:
        return plan

    # 10) structured calc template with placeholders -> ask for real numbers
    if loan_input := _loan_needs_input(q):
        return loan_input
    if needs := _template_needs_input(q):
        return needs

    return _plan_none(
        "I couldn't identify a calculation in your message. Try something like\n"
        "`Calculate 17.5% of 85000` or `sqrt(144) + 8**2` or `convert 100 km to miles`."
    )


def _plan_none(message: str) -> dict:
    return {"kind": "none", "error": message, "label": ""}


# --------------------------------------------------------------------------
# Loan amortization (French / fixed-payment method)
# --------------------------------------------------------------------------
_LOAN_SIGNALS = ("amortiz", "mortgage", "emi", "repay", "repayment", "loan schedule", "payment plan")


def _num_near(q: str, labels_re: str) -> float | None:
    for m in re.finditer(r"([\d][\d.,]*)", q):
        before = q[max(0, m.start() - 50): m.start()].rstrip()
        if re.search(labels_re, before):
            return _num(m.group(1))
    return None


def _try_loan(q: str) -> dict | None:
    term_years = re.search(r"\b([\d.,]+)\s*(?:years?|yrs?|year term)\b", q)
    term_months = re.search(r"\b([\d.,]+)\s*(?:months?|mos?)\b", q)
    has_term = bool(term_years or term_months) or " for " in q

    signaled = any(w in q for w in _LOAN_SIGNALS)
    loan_word = ("loan" in q or "borrow" in q or "mortgage" in q)
    if not (signaled or (loan_word and has_term)):
        return None

    principal = _num_near(q, r"(?:principal|loan amount|amount|borrowed|principal of|of)") or _num_near(q, r"\$|€|£|₹")
    if principal is None:
        nums = [m.group(1) for m in re.finditer(r"([\d][\d.,]*)", q)]
        real = [n for n in nums if "," in n or "." in n and (n.count(".") == 1)]
        principal = _num(real[0]) if real else None
        if principal is None:
            return None

    rate = _num_near(q, r"(?:rate|%|interest|annual)")
    if rate is None or rate > 100:
        if m := re.search(r"([\d.,]+)\s*%", q):
            rate = _num(m.group(1))
    if rate is None or rate <= 0 or rate > 100:
        return None

    payments_per_year = 12
    if "quarterly" in q or "per quarter" in q:
        payments_per_year = 4
    elif "semi" in q and "annual" in q:
        payments_per_year = 2
    elif "weekly" in q:
        payments_per_year = 52
    elif "biweekly" in q or "bi-weekly" in q:
        payments_per_year = 26

    if term_months:
        months = max(1, int(round(_num(term_months.group(1)))))
    elif term_years:
        months = max(1, int(round(_num(term_years.group(1)))) * 12)
    elif m := re.search(r"\b([\d.,]+)\s*(?:payments?)\b", q):
        months = max(1, int(round(_num(m.group(1)))))
    else:
        return None

    return {
        "kind": "loan",
        "principal": principal,
        "rate_pct": rate,
        "payments_per_year": payments_per_year,
        "months": months,
        "label": f"Loan amortization: {_money(principal)} at {rate:g}% ({payments_per_year}x/year) for {months} payments",
        "headline": f"Loan amortization ({_money(principal)} at {rate:g}% annual) for {months} payments",
    }


def _loan_schedule(principal: float, rate_pct: float, payments_per_year: int, months: int) -> tuple[float, float, list[tuple[int, float, float, float, float]]]:
    r = rate_pct / 100.0 / payments_per_year
    if r <= 0:
        payment = principal / months
    else:
        payment = principal * r / (1 - (1 + r) ** -months)
    balance = principal
    total_interest = 0.0
    rows: list[tuple[int, float, float, float, float]] = []
    for period in range(1, months + 1):
        interest = balance * r
        principal_part = max(0.0, payment - interest)
        if period == months:
            principal_part = balance
            payment = principal_part + interest
        balance = max(0.0, balance - principal_part)
        total_interest += interest
        rows.append((period, payment, principal_part, interest, balance))
    return payment, total_interest, rows


def _loan_answer(plan: dict) -> tuple[str, float]:
    payment, total_interest, rows = _loan_schedule(
        plan["principal"], plan["rate_pct"], plan["payments_per_year"], plan["months"]
    )
    total_paid = plan["principal"] + total_interest
    currency = "$"
    for sym in ("£", "€", "₹", "¥"):
        if sym in plan.get("label", ""):
            currency = sym
            break

    show_all = len(rows) <= 48
    display = rows
    note = ""
    if not show_all:
        display = rows[:12] + [("…",)*5] + rows[-12:]
        note = f"\n\nShowing first 12 and last 12 of {len(rows)} payments (full {len(rows)}-row schedule available)."

    lines = [f"**{plan['headline']}**", "", "Fixed payment (French method): "
             f"**{currency}{payment:,.2f}** per period", ""]
    lines.append(f"Number of periods: **{len(rows)}**")
    lines.append(f"Total principal: **{currency}{plan['principal']:,.2f}**")
    lines.append(f"Total interest: **{currency}{total_interest:,.2f}**")
    lines.append(f"Total paid: **{currency}{total_paid:,.2f}**")
    lines.append("")
    lines.append("| Period | Payment | Principal | Interest | Balance |")
    lines.append("|---|---:|---:|---:|---:|")
    for period, pay, pp, ip, bal in display:
        if isinstance(period, str):
            lines.append("| … | … | … | … | … |")
        else:
            lines.append(f"| {period} | {currency}{pay:,.2f} | {currency}{pp:,.2f} | {currency}{ip:,.2f} | {currency}{bal:,.2f} |")
    lines.append(note)
    return "\n".join(lines).strip(), 1.0


# --------------------------------------------------------------------------
# "Give me the real numbers" — structured calc templates with placeholders
# --------------------------------------------------------------------------
_LOAN_VALUE_HINTS = ("principal", "loan amount", "amount borrowed", "borrowed", "variable a")
_LOAN_RATE_HINTS = ("rate", "interest", "variable b")
_LOAN_TERM_HINTS = ("years", "year", "months", "month term", "term", "payments", "variable c")


def _loan_needs_input(q: str) -> dict | None:
    if not (any(w in q for w in _LOAN_SIGNALS) or "loan" in q or "borrow" in q or "mortgage" in q):
        return None
    if re.fullmatch(r"[\s\d.,$€£₹¥%+\-*/^()!<>]+", q):
        return None

    missing = []
    if "principal" in q or "loan amount" in q or "amount" in q or "variable a" in q or "borrowed" in q:
        pass
    else:
        missing.append("the principal (amount borrowed)")
    if any(w in q for w in _LOAN_RATE_HINTS) or "%" in q:
        pass
    else:
        missing.append("the annual interest rate (%)")
    if any(w in q for w in _LOAN_TERM_HINTS):
        pass
    else:
        missing.append("the term (years or number of payments)")

    if re.search(r"\[[^\]]+\]", q) or "value/unit" in q:
        fragment = (
            "it looks like the values are still placeholders "
            "(`[Value/Unit]`, `[number]`) rather than real numbers"
        )
        fill = "Replace the placeholders with concrete values"
    else:
        fragment = "I was missing some inputs"
        fill = "Provide the concrete values"

    if missing:
        return {
            "kind": "needs_input",
            "subject": "loan amortization",
            "answer": (
                f"I'd be happy to compute the loan amortization schedule, but {fragment}, "
                f"so there is nothing to calculate yet.\n\n"
                f"{fill} and resend, e.g.:\n"
                f"- `Variable A = 200000` (principal)\n"
                f"- `Variable B = 6.5` (annual interest rate, %)\n"
                f"- `for 30 years` (term) or `= 360 monthly payments`\n\n"
                f"I'll then produce the step-by-step working and a markdown amortization table."
            ),
            "confidence": 0.6,
        }
    return {
        "kind": "needs_input",
        "subject": "loan amortization",
        "answer": (
            "I parsed this as a loan amortization request, but the variables came through "
            "without values, so I can't compute a schedule yet. Please send the concrete "
            "numbers, e.g. `Variable A = 200000`, `Variable B = 6.5`, `for 30 years`, "
            "and I'll return the full amortization table."
        ),
        "confidence": 0.6,
    }


def _template_needs_input(q: str) -> dict | None:
    placeholders = bool(re.search(r"\[[a-z/0-9 ]+\]", q)) or "value/unit" in q or "value/unit]" in q
    structured = (
        "variable" in q
        or "formula/method" in q
        or "step-by-step" in q
        or ("decimal places" in q and "rounded to" in q)
        or ("markdown table" in q and "schedule" in q)
        or "python snippet" in q
    )
    calc_intent = any(w in q for w in _LOAN_SIGNALS) or "calculate" in q or "compute" in q or "calculation" in q
    if not (calc_intent and (placeholders or structured)):
        return None
    return {
        "kind": "needs_input",
        "subject": "calculation",
        "answer": (
            "I parsed your request as a calculation job, but the data/context came in as "
            "template placeholders (`[Value/Unit]`, `[number]`) instead of real numbers, "
            "so there is nothing to compute yet.\n\n"
            "Fill in the concrete values and resend, e.g.:\n"
            "- `Variable A = 200000`, `Variable B = 6.5`\n"
            "- `Formula/Method = French amortization method, term 30 years`\n"
            "- `rounded to 2 decimal places`\n\n"
            "I'll then run the calculation and return the step-by-step working with a markdown table."
        ),
        "confidence": 0.6,
    }


def _num(s: str) -> float:
    return float(str(s).replace(",", ""))


def _translate_math(expr: str) -> str:
    expr = re.sub(r"\^", "**", expr)
    expr = re.sub(r"(\d+)!", r"factorial(\1)", expr)
    expr = expr.replace(",", "")
    return expr.strip()


def _words_to_expression(q: str) -> str | None:
    low = q
    fill = {
        " to the power of ": "**",
        " to the power ": "**",
        " raised to ": "**",
        " plus ": "+",
        " added to ": "+",
        " minus ": "-",
        " subtract ": "-",
        " subtracted from ": "-",
        " times ": "*",
        " multiplied by ": "*",
        " divided by ": "/",
        " divide ": "/",
        " over ": "/",
        " percent of ": "/100*",
    }
    changed = False
    for word, sym in fill.items():
        if word in low:
            low = low.replace(word, sym)
            changed = True
    if not changed:
        return None
    low = re.sub(r"[a-z]+", "", low)
    low = low.replace("?", "").strip()
    if re.fullmatch(r"[0-9().+\-*/%^!]+", low):
        return _translate_math(low)
    return None


# --------------------------------------------------------------------------
# Unit conversion detection
# --------------------------------------------------------------------------
_UNIT_WORDS = (
    "meter", "meters", "m", "km", "kilometer", "kilometers", "mile", "miles",
    "yard", "yards", "foot", "feet", "inch", "inches", "cm", "centimeter", "centimeters",
    "mm", "millimeter", "millimeters", "nautical", "kg", "kilogram", "kilograms",
    "g", "gram", "grams", "mg", "miligram", "pound", "pounds", "lb", "lbs", "ounce", "ounces",
    "ton", "tons", "liter", "liters", "l", "ml", "milliliter", "gallon", "gallons", "cup", "cups",
    "celsius", "fahrenheit", "kelvin",
    "second", "seconds", "sec", "s", "minute", "minutes", "min", "hour", "hours", "h", "hr",
    "day", "days", "week", "weeks", "month", "months", "year", "years",
    "mph", "kmh", "km/h", "mps", "meter/second", "knot", "knots",
    "bit", "byte", "kb", "kilobyte", "mb", "megabyte", "gb", "gigabyte", "tb", "terabyte",
    "percent",
)


def _try_units(q: str) -> dict | None:
    m = re.search(r"convert\s+([\d.,]+)\s*([a-z0-9/]+)\s*(?:to|into|in)\s*([a-z0-9/]+)", q)
    if not m:
        m = re.search(rf"({_NUM})\s*([a-z0-9/]+)\s*(?:to|into|in)\s*([a-z0-9/]+)", q)
    if m and m.group(2).lower() not in _UNIT_WORDS:
        m = None  # the "unit" isn't a real unit (e.g. "cus" in "customers")
    if not m:
        m = re.search(r"how many\s+([a-z0-9/]+)\s+(?:are|is)?\s+in\s+([\d.,]+)\s*([a-z0-9/]+)", q)
        if m:
            return _unit_plan(_num(m.group(2)), m.group(3), m.group(1))
    if m:
        return _unit_plan(_num(m.group(1)), m.group(2), m.group(3))
    return None


def _unit_plan(value: float, from_unit: str, to_unit: str) -> dict:
    return {
        "kind": "units",
        "value": value,
        "from_unit": from_unit,
        "to_unit": to_unit,
        "label": f"{value:g} {from_unit} to {to_unit}",
        "headline": f"{value:g} {from_unit} to {to_unit}",
    }


# --------------------------------------------------------------------------
# Geometry / finance formulas
# --------------------------------------------------------------------------
def _try_formula(q: str) -> dict | None:
    checks = [
        (r"area\b.*\bcircle\b.*(?:radius)\b.*?([\d.,]+)", "Area of a circle",
         lambda r: f"pi*{r}**2", "square units"),
        (r"(?:circumference|perimeter)\b.*\bcircle\b.*(?:radius)\b.*?([\d.,]+)", "Circumference of a circle",
         lambda r: f"2*pi*{r}", "units"),
        (r"area\b.*\b(?:a |the )?rectangle\b.*?([\d.,]+)\s*(?:[x×*]|by|and)\s*([\d.,]+)", "Area of a rectangle",
         lambda a, b: f"{a}*{b}", "square units"),
        (r"area\b.*\b(?:a |the )?square\b.*?(?:side)?.*?([\d.,]+)", "Area of a square",
         lambda s: f"{s}**2", "square units"),
        (r"area\b.*\b(?:a |the )?triangle\b.*?(?:base)?.*?([\d.,]+).*?(?:height)?.*?([\d.,]+)", "Area of a triangle",
         lambda b, h: f"0.5*{b}*{h}", "square units"),
        (r"volume\b.*\bsphere\b.*(?:radius)\b.*?([\d.,]+)", "Volume of a sphere",
         lambda r: f"(4/3)*pi*{r}**3", "cubic units"),
        (r"volume\b.*\bcylinder\b.*(?:radius)\b.*?([\d.,]+).*?(?:height)\b.*?([\d.,]+)", "Volume of a cylinder",
         lambda r, h: f"pi*{r}**2*{h}", "cubic units"),
        (r"volume\b.*\bcube\b.*(?:side)?.*?([\d.,]+)", "Volume of a cube",
         lambda s: f"{s}**3", "cubic units"),
        (r"volume\b.*\b(?:cuboid|box|rectangular prism)\b.*?([\d.,]+)\s*(?:[x×*]|by|and)\s*([\d.,]+)\s*(?:[x×*]|by|and)\s*([\d.,]+)",
         "Volume of a box", lambda a, b, c: f"{a}*{b}*{c}", "cubic units"),
        (r"(simple|compound)\s+interest\s+on\s+([\d.,]+)\s+at\s+([\d.,]+)%\s+for\s+([\d.,]+)\s*(?:years?|months?)?",
         None, None, None),
        (r"bending moment.*?(?:span|length)\s*(?:of\s*)?([\d.,]+).*?(?:uniform|distributed|point)?\s*load\s*(?:of\s*)?([\d.,]+)",
         "Maximum bending moment of the beam",
         lambda span, w: f"({w}*{span}**2)/8",
         "kN·m"),
        (r"(?:churn rate|churned|customers? lost|lost customers?)\b.*?([\d.,]+).*?\bof\s+([\d.,]+)",
         "Customer churn rate", lambda lost, total: f"({lost}/{total})*100", "percent"),
        (r"([\d.,]+)%\s*(?:discount|off)\s+(?:on|off)?\s*([\d.,]+)", "Discounted price",
         lambda p, d: f"{p}*(1-({d}/100))", "currency"),
        (r"([\d.,]+)%\s*increase\s+(?:of|on)?\s*([\d.,]+)", "Value after increase",
         lambda p, inc: f"{p}*(1+({inc}/100))", "currency"),
    ]
    ql = q
    for pat, headline, expr_fn, unit in checks:
        if headline is None:  # interest handled below
            continue
        m = re.search(pat, ql)
        if not m:
            continue
        try:
            expr_obj = expr_fn(*[g for g in m.groups()])
        except Exception:
            continue
        return _formula_plan(headline, expr_obj, unit, m.groups())

    m = re.search(r"(simple|compound)\s+interest\s+on\s+([\d.,]+)\s+at\s+([\d.,]+)%\s+for\s+([\d.,]+)\s*(?:years?|months?)?", ql)
    if m:
        kind, p, r, t = m.group(1), _num(m.group(2)), _num(m.group(3)), _num(m.group(4))
        if kind.startswith("c"):
            amount = p * (1 + r / 100.0) ** t
            interest = amount - p
            expr = f"{p:g}*(1+({r:g}/100))**{t:g}"
            headline = f"Compound interest on {_money(p)} at {r:g}% for {t:g} years"
        else:
            interest = p * r * t / 100.0
            amount = p + interest
            expr = f"{p:g}*{r:g}*{t:g}/100"
            headline = f"Simple interest on {_money(p)} at {r:g}% for {t:g} years"
        return {
            "kind": "formula",
            "expression": expr,
            "result": round(interest, 2),
            "total": round(amount, 2),
            "label": headline,
            "headline": headline,
            "unit": "currency",
            "is_money": True,
        }
    return None


def _formula_plan(headline: str, expr: str, unit: str, groups: tuple) -> dict:
    return {"kind": "formula", "expression": expr, "label": headline, "headline": headline, "unit": unit}


def _money(x: float) -> str:
    return f"${x:,.2f}"


# --------------------------------------------------------------------------
# Embedded numeric expression in text
# --------------------------------------------------------------------------
def _embedded_expression(q: str) -> str | None:
    for m in re.finditer(r"[0-9(][0-9 ().+\-*/%^!]*[0-9)!]", q):
        cand = _translate_math(m.group(0).strip())
        if re.search(r"[+\-*/%^]", cand):
            return cand
    return None


# --------------------------------------------------------------------------
# numpy list/vector/matrix ops described in words
# --------------------------------------------------------------------------
_STAT_WORDS = {
    "mean": "mean", "average": "mean", "avg": "mean",
    "median": "median", "midpoint": "median",
    "std": "std", "standard deviation": "std", "standard deviation of": "std",
    "variance": "var", "var": "var",
    "sum of": "sum", "product of": "prod", "cumulative sum": "cumsum",
}


def _nums_in(text: str) -> list[str]:
    out: list[str] = []
    for tok in re.findall(r"\b[\d][\d.,]*\b", text):
        for part in tok.split(","):
            part = part.strip()
            if part:
                out.append(part.replace("_", ""))
    return out


def _try_numpy(q: str) -> dict | None:
    # 1) matrix / vector ops  e.g. "determinant of [[1,2],[3,4]]"
    vecs = re.findall(r"\[[\d.,\s\[\]]+\]", q)
    if vecs:
        m = re.search(r"\b(?:determinant|det)\b", q)
        if m and "[[" in q:
            return {"kind": "math", "expression": f"det({vecs[0].strip()})",
                    "label": f"det({vecs[0].strip()})", "headline": "Determinant of the matrix"}
        m = re.search(r"\b(?:inverse|inv)\b", q)
        if m and "[[" in q:
            return {"kind": "math", "expression": f"inv({vecs[0].strip()})",
                    "label": f"inv({vecs[0].strip()})", "headline": "Inverse of the matrix"}
        m = re.search(r"\bdot\b", q)
        if m and len(vecs) >= 2:
            expr = f"dot({vecs[0]},{vecs[1]})"
            return {"kind": "math", "expression": expr, "label": expr, "headline": "Dot product"}
        m = re.search(r"\b(?:norm|magnitude|length of the vector)\b", q)
        if m:
            return {"kind": "math", "expression": f"norm({vecs[0].strip()})",
                    "label": f"norm({vecs[0].strip()})", "headline": "Norm (length) of the vector"}
        m = re.search(r"\btranspose\b", q)
        if m:
            return {"kind": "math", "expression": f"transpose({vecs[0].strip()})",
                    "label": f"transpose({vecs[0].strip()})", "headline": "Transpose of the matrix"}

    # 2) statistics over a numeric list  e.g. "mean of 1,2,3,4,5"
    for word, fn in _STAT_WORDS.items():
        if word in q:
            nums = _nums_in(q)
            if len(nums) >= 2:
                expr = f"{fn}([{','.join(nums)}])"
                return {"kind": "math", "expression": expr, "label": expr,
                        "headline": f"{word} of {','.join(nums)}"}
            if len(nums) == 1:
                return None  # single number, ambiguous — let LLM decide
    return None


# --------------------------------------------------------------------------
# LLM fallback (structed: JSON expression or unit conversion)
# --------------------------------------------------------------------------
_LLM_PLAN_SYSTEM = (
    "You plan computations for a SAFE python evaluator used by a calculator assistant. "
    "Respond with the structured plan only.\n\n"
    "Allowed in expressions: numbers, + - * / ** ( ) and the functions "
    "sqrt, log, log10, log2, sin, cos, tan, factorial, root, cbrt, hypot, gcd, lcm, comb, perm, "
    "pi, e, plus numpy as np.* and np.linalg.* (array, dot, det, inv, solve, norm, transpose, "
    "mean, median, std, var). Lists are fine, e.g. np.mean([10,20,30]) or dot([[1,2],[3,4]],[[1,0],[0,1]]).\n\n"
    "CRITICAL: the 'expression' MUST be a complete, standalone Python expression that computes the "
    "FINAL answer for the requested metric end-to-end, substituting every number given in the text. "
    "Never return a truncated fragment. Examples:\n"
    "- maximum bending moment of a simply supported beam, span 6 m, uniform 2 kN/m, M=wL^2/8 -> "
    "'(2*6**2)/8'\n"
    "- customer churn 120 lost of 9000 -> '(120/9000)*100'\n"
    "- 17.5% of 85000 -> '(17.5/100)*85000'\n\n"
    "If the request is a structured/expert calculation template (e.g. 'Act as an expert… Variable A = …, "
    "Variable B = …, Formula/Method = French amortization method, rounded to [number] decimal places'):\n"
    "- DECIDE the missing values yourself, guided by any examples inside the request. Common defaults: "
    "loan amortization → principal 200000, annual rate 6.5%, term 30 years (French method, monthly); "
    "bending moment of a simply-supported beam with uniform load w over span L → M = w·L²/8; "
    "customer churn rate → (lost customers / total customers) * 100.\n"
    "- Put every value/model you chose in 'assumptions' so the final answer is transparent.\n"
    "- Represent the computation as:\n"
    "  • a single 'expression' (python-safe, end-to-end) with a human 'label' naming the metric, OR\n"
    "  • type 'loan' with principal, annual rate %, months (number of payments), payments_per_year.\n"
    "Otherwise 'unit' for conversions. If there is genuinely nothing numeric to compute, type 'none'."
)


def _llm_plan(q: str) -> dict | None:
    if settings.is_demo or (settings.llm_provider == "groq" and not settings.groq_api_key):
        return None
    try:
        llm = get_llm(temperature=0.0, streaming=False, max_tokens=600)

        obj: _CalcPlan | None = None
        try:
            structured = llm.with_structured_output(_CalcPlan)
            raw = structured.invoke(
                [
                    ("system", _LLM_PLAN_SYSTEM),
                    ("human", f"User request:\n{q}"),
                ]
            )
            obj = raw if isinstance(raw, _CalcPlan) else (_CalcPlan(**raw) if isinstance(raw, dict) else None)
        except Exception:
            pass
        if obj is None:
            llm2 = get_llm(temperature=0.0, streaming=False, max_tokens=600)
            raw = await_or_none(llm2, ChatPromptTemplate.from_messages([("system", _LLM_PLAN_SYSTEM), ("human", "{query}")]).format_messages(query=q))
            obj = _parse_jsonish(raw or "")
            if obj is not None and not isinstance(obj, _CalcPlan):
                try:
                    obj = _CalcPlan(**obj)
                except Exception:
                    obj = None

        if obj is None or not getattr(obj, "type", None):
            return None

        if obj.type == "expression" and (obj.expression or "").strip():
            expr = str(obj.expression).strip()
            return {
                "kind": "math",
                "expression": expr,
                "label": (obj.label or expr).strip(),
                "headline": None,
                "assumptions": obj.assumptions or [],
            }
        if obj.type == "unit":
            try:
                return _unit_plan(float(obj.value), str(obj.from_unit).strip(), str(obj.to_unit).strip())
            except (TypeError, ValueError):
                return None
        if obj.type == "loan":
            try:
                p = float(obj.principal or 0)
                r = float(obj.rate or 0)
                months = int(float(obj.months or 0))
                ppy = int(float(obj.payments_per_year or 12)) or 12
                if p > 0 and r > 0 and months > 0:
                    return {
                        "kind": "loan",
                        "principal": p,
                        "rate_pct": r,
                        "payments_per_year": ppy,
                        "months": months,
                        "label": f"Loan amortization: {_money(p)} at {r:g}% ({ppy}x/year) for {months} payments",
                        "headline": f"Loan amortization ({_money(p)} at {r:g}% annual) for {months} payments",
                        "assumptions": obj.assumptions or [],
                    }
            except (TypeError, ValueError, ZeroDivisionError):
                return None
    except Exception:
        return None
    return None


def _parse_jsonish(text: str) -> dict | None:
    """Tolerant JSON parse: strips code fences, leading words, trailing commas."""
    text = _strip_code_fences(text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start:end + 1]
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    # retry with trailing commas removed before the last brace
    cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


async def await_or_none(llm, messages):
    resp = await llm.ainvoke(messages)
    return getattr(resp, "content", None) or str(resp)


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


# --------------------------------------------------------------------------
# Graph node
# --------------------------------------------------------------------------
async def calculate(state: AgentState) -> dict:
    query = state.get("query", "").strip()
    if not query:
        return _no_calc(state, "No query provided.")

    cached = _calc_cache_get(query)
    if cached is not None:
        return cached

    plan = plan_calculation(query)
    if plan["kind"] == "none":
        return _no_calc(state, plan.get("error", "Could not parse a calculation."))
    if plan["kind"] == "needs_input":
        return _no_calc(state, plan.get("answer", "Please provide the concrete numbers."), confidence=0.6)

    records: list[ToolRun] = []
    calc: CalculationResult | None = None

    if plan["kind"] == "math":
        emit_status("Calculating…")
        result, record = await run_tool("calculator", expression=plan["expression"])
        records.append(record)
        if isinstance(result, dict) and "error" in result:
            emit_status("The calculation could not be performed.")
            calc = None
            answer = plan.get("error", "The calculation could not be performed.")
            confidence = 0.4
        else:
            calc = result
            answer, confidence = _build_text(query, plan, calc)

    elif plan["kind"] == "units":
        emit_status(f"Converting {plan['label']}…")
        result, record = await run_tool(
            "unit_converter",
            value=plan["value"],
            from_unit=plan["from_unit"],
            to_unit=plan["to_unit"],
        )
        records.append(record)
        if isinstance(result, dict) and "error" in result:
            emit_status("That conversion could not be computed.")
            calc = None
            answer, confidence = "I couldn't convert those units. Please check the unit names and try again.", 0.4
        else:
            calc = result
            calc.expression = plan["label"]
            answer, confidence = _build_text(query, plan, calc)

    elif plan["kind"] == "formula":
        emit_status("Computing the formula…")
        result, record = await run_tool("calculator", expression=plan["expression"])
        records.append(record)
        if isinstance(result, dict) and "error" in result:
            emit_status("The calculation could not be performed.")
            calc = None
            answer, confidence = "I couldn't compute that formula. Please check the values and try again.", 0.4
        else:
            calc = result
            answer, confidence = _build_text(query, plan, calc)

    elif plan["kind"] == "loan":
        emit_status("Building the amortization schedule…")
        payment, total_interest, rows = _loan_schedule(
            plan["principal"], plan["rate_pct"], plan["payments_per_year"], plan["months"]
        )
        calc = CalculationResult(
            expression=plan["label"],
            result=round(payment, 2),
            steps=[
                f"Payment = P·r / (1 − (1+r)^−n) = {_money(payment)} ({plan['months']} periods, {plan['payments_per_year']}%/yr → r = {(plan['rate_pct'] / 100 / plan['payments_per_year']):.6g})",
                f"Total interest = {_money(round(total_interest, 2))}",
            ],
        )
        answer, confidence = _loan_answer(plan)
        if plan.get("assumptions"):
            answer += "\n\n**Assumptions / decisions:**\n" + "\n".join(f"- {a}" for a in plan["assumptions"])

    answer = answer or "I attempted the calculation but couldn't produce a verified result."

    result = {
        "calculation": calc,
        "answer": answer,
        "confidence": confidence,
        "errors": [] if calc is not None else [plan.get("error", "No computation parsed")],
        "tool_runs": [r for r in records if r.status == "success"],
    }
    if calc is not None:
        _calc_cache_put(query, result)
    return result


def _no_calc(state: AgentState, message: str, confidence: float = 0.4) -> dict:
    return {
        "calculation": None,
        "answer": message,
        "confidence": confidence,
        "errors": ["No computation parsed"],
        "tool_runs": [],
    }


def _build_text(query: str, plan: dict, calc: CalculationResult | None) -> tuple[str, float]:
    if calc is None:
        return "I attempted the calculation but couldn't produce a verified result. Please check the request and try again.", 0.4

    steps = "\n".join(f"- {s}" for s in calc.steps[-6:])

    if plan["kind"] == "units":
        value = _fmt(calc.result)
        unit = _nm(plan.get("to_unit", ""))
        return (
            f"**{plan['label']}**\n\n"
            f"Result: **{value} {unit}**\n\n"
            f"{'Steps:\n' + steps if steps else ''}".strip(),
            1.0,
        )

    if plan.get("fixed_result") is not None:
        return f"**{plan['headline']} = {_fmt(plan['fixed_result'])}**", 0.99

    if plan["kind"] == "formula":
        value = _fmt(calc.result)
        total = plan.get("total")
        heading = plan.get("headline") or plan.get("label") or "Calculation"
        extra = ""
        if total is not None and plan.get("is_money"):
            extra = f"\n\nTotal after the period: **{_money(total)}**"
        elif total is not None:
            extra = f"\n\nTotal: **{_fmt(total)}**"
        return (
            f"**{heading}**\n\n"
            f"Result: **{value}**{extra}\n\n"
            f"{'Steps:\n' + steps if steps else ''}".strip(),
            0.98,
        )

    expr = plan.get("label") or calc.expression
    value = _fmt(calc.result)
    note = ""
    if plan.get("assumptions"):
        note = "\n\n**Assumptions / decisions:**\n" + "\n".join(f"- {a}" for a in plan["assumptions"])
    return (
        f"The result of `{expr}` is:\n\n**{value}**\n\n"
        f"{'Steps:\n' + steps if steps else ''}".strip()
        + note,
        1.0,
    )


def _fmt(value) -> str:
    if isinstance(value, list):
        return _fmt_matrix(value)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if value == int(value):
        return f"{int(value):,}"
    if abs(value) >= 1e6:
        return f"{value:,.2f}"
    return f"{value:.6g}"


def _fmt_matrix(matrix) -> str:
    if not matrix:
        return "[]"
    if not isinstance(matrix[0], list):
        return "[" + ", ".join(str(x) for x in matrix) + "]"
    rows = "\n".join("`" + "  ".join(str(x) for x in r) + "`" for r in matrix)
    return "[" + "\n" + rows + "\n" + "]"


def _nm(unit: str) -> str:
    return {
        "m": "meters", "km": "km", "miles": "miles", "feet": "feet", "ft": "feet",
        "inches": "inches", "kg": "kg", "pounds": "lb", "lbs": "lb", "g": "g",
        "liters": "L", "l": "L", "gallons": "gal", "celsius": "°C", "fahrenheit": "°F",
        "kelvin": "K", "seconds": "seconds", "sec": "seconds", "minutes": "minutes",
        "hours": "hours", "days": "days", "weeks": "weeks", "years": "years",
        "mph": "mph", "km/h": "km/h", "meters/second": "m/s",
        "bytes": "bytes", "mb": "MB", "gb": "GB", "tb": "TB",
    }.get(unit, unit)