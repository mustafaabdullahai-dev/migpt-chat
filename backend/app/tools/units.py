"""Unit conversion tool built on the reliable `pint` library."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.schemas import CalculationResult

# lazy singleton
_registry = None


def _ureg():
    global _registry
    if _registry is None:
        import pint

        _registry = pint.UnitRegistry()
    return _registry


class UnitConverterArgs(BaseModel):
    value: float = Field(..., description="Numeric value to convert.")
    from_unit: str = Field(..., description="Source unit, e.g. 'meter', 'kg', 'celsius', 'liter', 'mph', 'kilobyte'.")
    to_unit: str = Field(..., description="Target unit, e.g. 'foot', 'pound', 'fahrenheit', 'gallon', 'km/h', 'megabyte'.")


def convert(value: float, from_unit: str, to_unit: str) -> CalculationResult:
    ureg = _ureg()
    try:
        quantity = ureg.Quantity(value, from_unit)
        converted = quantity.to(to_unit)
    except (Exception,) as exc:  # pint raises generic UndefinedUnitError / DimensionalityError
        raise ValueError(f"Could not convert {from_unit} -> {to_unit}: {exc}")
    return CalculationResult(
        expression=f"{value:g} {from_unit} -> {to_unit}",
        result=round(converted.magnitude, 10),
        steps=[f"1 {from_unit} = {converted.to(to_unit).magnitude / value:.10g} {to_unit}" if value else f"{value} {from_unit} = {round(converted.magnitude, 10)} {to_unit}"],
    )