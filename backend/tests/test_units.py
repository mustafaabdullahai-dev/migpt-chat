"""Unit-conversion tests: one real conversion per required category + registry smoke."""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.tools.base import registry
from app.tools.units import convert

CASES: list[tuple[str, str, str, float, float]] = [
    ("length", 1.0, "meter", "centimeter", 100.0),
    ("mass", 1.0, "kilogram", "gram", 1000.0),
    ("temperature", 0.0, "celsius", "kelvin", 273.15),
    ("temperature", 100.0, "celsius", "fahrenheit", 212.0),
    ("area", 1.0, "meter ** 2", "foot ** 2", 10.7639),
    ("volume", 1.0, "liter", "milliliter", 1000.0),
    ("speed", 20.0, "mph", "km/hour", 32.1869),
    ("time", 1.0, "hour", "minute", 60.0),
    ("data", 8.0, "bit", "byte", 1.0),
    ("data", 1.0, "kilobyte", "byte", 1000.0),
    ("energy", 1.0, "joule", "kilojoule", 0.001),
    ("energy", 1.0, "kWh", "joule", 3600000.0),
    ("pressure", 1.0, "bar", "pascal", 100000.0),
    ("pressure", 1.0, "psi", "pascal", 6894.7573),
]


@pytest.mark.parametrize("category,value,from_,to,expected", CASES)
def test_convert_category(category: str, value: float, from_: str, to: str, expected: float) -> None:
    res = convert(value, from_, to)
    assert abs(res.result - expected) < expected * 1e-3, f"{category}: {from_} -> {to}"


def test_convert_returns_calculation_shape():
    res = convert(5, "kilometers", "miles")
    assert res.expression == "5 kilometers -> miles"
    assert isinstance(res.steps, list) and res.steps


def test_convert_invalid_raises():
    with pytest.raises(ValueError):
        convert(1, "meter", "kilogram")  # dimensionality mismatch
    with pytest.raises(ValueError):
        convert(1, "nonexistentunit", "meter")


def test_converter_registered_and_enabled():
    spec = registry.get("unit_converter")
    assert spec is not None and spec.enabled
    assert spec.args_schema is not None


@pytest_asyncio.fixture
async def _async_run():
    yield


@pytest.mark.asyncio
async def test_converter_runs_via_registry():
    from app.tools.base import run_spec

    spec = registry.get("unit_converter")
    value, _ = await run_spec(spec, {"value": 3.0, "from_unit": "foot", "to_unit": "meter"})
    assert abs(value.result - 0.9144) < 1e-6