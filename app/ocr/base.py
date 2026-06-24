from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class TextBox:
    text: str
    confidence: float
    box: list[list[float]]  # 4 [x, y] corner points

    @property
    def cx(self) -> float:
        return sum(p[0] for p in self.box) / 4

    @property
    def cy(self) -> float:
        return sum(p[1] for p in self.box) / 4

    @property
    def x0(self) -> float:
        return min(p[0] for p in self.box)

    @property
    def x1(self) -> float:
        return max(p[0] for p in self.box)


class OCREngine(Protocol):
    def extract_text(self, image: np.ndarray) -> list[TextBox]:
        ...
