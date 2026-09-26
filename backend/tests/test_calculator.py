"""Unit tests for the safe calculator."""

from __future__ import annotations

import pytest

from app.tools.calculator import evaluate


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("2+3", 5),
        ("10/4", 2.5),
        ("2**10", 1024),
        ("17.5/100*85000", 14875),
        ("(2+3)*4", 20),
        ("sqrt(144)", 12.0),
        ("log(1)", 0.0),
        ("sin(0)", 0.0),
        ("abs(-7)", 7),
        ("2**0.5", pytest.approx(1.41421356)),
    ],
)
def test_valid_expressions(expr, expected):
    result = evaluate(expr)
    assert result.result == expected
    assert result.steps


def test_percent_of():
    result = evaluate("(17.5/100)*85000")
    assert result.result == 14875
    assert len(result.steps) == 2


@pytest.mark.parametrize("bad", ["__import__('os')", "open('/etc/passwd')", "a=1", "1; 2", "lambda x: x", "'s'*3", "[x for x in []]", "1 if True else 2"])
def test_bad_expressions_never_eval(bad):
    with pytest.raises((ValueError, SyntaxError)):
        evaluate(bad)


def test_guards_blocked_node_types_at_runtime():
    for bad in ("__import__('os')", "open('/etc/passwd')", "[x for x in []]", "1 if True else 2"):
        with pytest.raises((ValueError, SyntaxError)):
            evaluate(bad)


def test_division_by_zero_cleanly_errors():
    with pytest.raises((ValueError, ZeroDivisionError)):
        evaluate("1/0")

# ---------------- numpy support ----------------
def test_numpy_mean():
    result = evaluate("mean([1,2,3,4,5])")
    assert abs(result.result - 3.0) < 1e-9
    assert any("mean(" in s for s in result.steps)


def test_numpy_std_median():
    assert abs(evaluate("median([10,20,30,100])").result - 25.0) < 1e-9
    assert abs(evaluate("std([1,2,3,4])").result - 1.1180339887498949) < 1e-6


def test_numpy_matrix_dot():
    result = evaluate("dot([[1,2],[3,4]],[[5,6],[7,8]])")
    assert isinstance(result.result, list)
    assert abs(result.result[0][0] - 19) < 1e-9


def test_numpy_det_inv():
    result = evaluate("det(array([[1,2],[3,4]]))")
    assert abs(result.result - (-2.0)) < 1e-9
    inv = evaluate("inv(array([[2,0],[0,4]]))")
    assert abs(inv.result[0][0] - 0.5) < 1e-9


def test_numpy_dot_vectors():
    result = evaluate("np.dot([1,2,3],[4,5,6])")
    assert abs(result.result - 32.0) < 1e-9


def test_numpy_linalg_path():
    result = evaluate("np.linalg.det([[1,2],[3,4]])")
    assert abs(result.result - (-2.0)) < 1e-9


def test_numpy_unsafe_attribute_blocked():
    import pytest as _pytest

    with _pytest.raises((ValueError, SyntaxError)):
        evaluate("np.__class__")
    with _pytest.raises((ValueError, SyntaxError)):
        evaluate("np.whatever")


# ---------------- loan amortization ----------------
from app.agents.nodes.calculate import plan_calculation, _loan_schedule


def test_loan_plan_french_method():
    p = plan_calculation("monthly loan amortization, principal 200000, annual interest rate 6.5 percent, French method, 30 years")
    assert p["kind"] == "loan"
    assert abs(p["principal"] - 200000) < 1 < 2
    assert abs(p["rate_pct"] - 6.5) < 1e-9
    assert p["months"] == 360


def test_loan_payment_math():
    pay, ti, rows = _loan_schedule(200000, 6.5, 12, 360)
    assert abs(pay - 1264.14) < 0.01
    assert len(rows) == 360
    assert rows[-1][4] < 0.01  # balance ~ fully paid


def test_loan_negative_misroute():
    assert plan_calculation("how many people live in egypt?").get("kind") == "none"


def test_loan_plan_no_rate_returns_none():
    # Missing rate -> deterministic fast path + (offline) LLM can't compute a verified
    # plan, so it falls to the "needs_input" clarifier instead of crashing.
    p = plan_calculation("a loan of 10000 for 5 years")
    assert p.get("kind") in ("none", "needs_input")


# ---------------- placeholder template (needs_input) ----------------
_TEMPLATE = (
    "Act as an expert financial controller. I need you to calculate the monthly loan "
    "amortization. Here is the given data and context: Variable A = [Value/Unit]; "
    "Variable B = [Value/Unit]; Formula/Method to apply = French amortization method. "
    "Write out the step-by-step working. Present the final answer rounded to [number] "
    "decimal places, and output any schedule data in a markdown table."
)


def test_template_placeholder_returns_needs_input():
    p = plan_calculation(_TEMPLATE)
    assert p["kind"] == "needs_input"
    assert "placeholder" in p["answer"] or "values" in p["answer"]
    assert "concrete values" in p["answer"] or "real numbers" in p["answer"]


def test_loan_placeholder_mentions_principal():
    p = plan_calculation("calculate the monthly loan amortization. Variable A = [Value/Unit], Variable B = [Value/Unit], French method")
    assert p["kind"] == "needs_input"
    assert "principal" in p["answer"]


def test_generic_calc_placeholder():
    p = plan_calculation("I need you to calculate the maximum bending moment. Variable A = [Value/Unit]; Variable B = [Value/Unit]; rounded to [number] decimal places")
    assert p["kind"] == "needs_input"


def test_plain_chat_not_needs_input():
    p = plan_calculation("hi, how are you today?")
    assert p["kind"] in ("none", None)


# ---------------- expert-formula examples ----------------
def test_bending_moment_formula():
    p = plan_calculation("maximum bending moment of a simply supported beam, span 6 m, uniform load 2 kN/m, M=wL^2/8")
    assert p["kind"] in ("math", "formula")
    expr = p.get("expression", "")
    assert expr


def test_churn_rate_formula():
    p = plan_calculation("customer churn rate: 120 customers lost out of 9000 total")
    assert p["kind"] in ("math", "formula")
