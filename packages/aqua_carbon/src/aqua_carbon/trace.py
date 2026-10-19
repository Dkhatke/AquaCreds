"""Calculation trace: every intermediate value with its formula, unit and source."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    value: float
    unit: str
    formula: str
    source: str


@dataclass
class Trace:
    steps: list[Step] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(
        self, key: str, label: str, value: float, unit: str, formula: str, source: str
    ) -> float:
        self.steps.append(Step(key, label, value, unit, formula, source))
        return value

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def value(self, key: str) -> float:
        for step in self.steps:
            if step.key == key:
                return step.value
        raise KeyError(key)

    def to_dict(self, ndigits: int = 6) -> dict[str, Any]:
        return {
            "steps": [asdict(s) | {"value": round(s.value, ndigits)} for s in self.steps],
            "warnings": list(self.warnings),
        }
