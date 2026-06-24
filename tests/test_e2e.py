"""End-to-end pipeline test using the REAL PaddleOCR model.

Marked `slow`: run with `pytest -m slow`. Skipped automatically if PaddleOCR
isn't installed. Renders a clean synthetic receipt so it runs without needing
real photos; drop real images in tests/fixtures/images/ to extend coverage.
"""

import glob
import os

import cv2
import numpy as np
import pytest

# Avoid the slow model-hoster connectivity check; models are cached after install.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

pytest.importorskip("paddleocr")

from app.ocr.paddle_engine import PaddleEngine  # noqa: E402
from app.parser import parse_receipt  # noqa: E402
from app.quality_gate import check_quality  # noqa: E402
from app.reconciler import reconcile  # noqa: E402


def _render_receipt() -> np.ndarray:
    """A clean, sharp synthetic receipt: name on the left, amount on the right."""
    img = np.full((520, 640, 3), 255, np.uint8)
    rows = [
        ("Nasi Goreng", "45.000"),
        ("Es Teh", "30.000"),
        ("Subtotal", "75.000"),
        ("PB1", "7.500"),
        ("Total", "82.500"),
    ]
    y = 90
    for left, right in rows:
        cv2.putText(img, left, (30, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
        cv2.putText(img, right, (420, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 2)
        y += 85
    return img


@pytest.fixture(scope="module")
def engine():
    return PaddleEngine(lang="id")


@pytest.mark.slow
def test_full_pipeline_on_rendered_receipt(engine):
    img = _render_receipt()

    ok, reason = check_quality(img)
    assert ok, f"quality gate rejected rendered receipt: {reason}"

    receipt = reconcile(parse_receipt(engine.extract_text(img)))

    assert receipt.subtotal == 75000
    assert receipt.total == 82500
    assert any(c.kind.value == "tax_pb1" for c in receipt.charges)
    assert len(receipt.line_items) >= 1
    # subtotal(75000) + PB1(7500) == total(82500) -> reconciles cleanly
    assert receipt.reconciled is True


@pytest.mark.slow
@pytest.mark.parametrize(
    "path", sorted(glob.glob("tests/fixtures/images/*.jpg") + glob.glob("tests/fixtures/images/*.png"))
)
def test_real_receipt_images_extract(engine, path):
    """For each real receipt dropped in fixtures/images, the pipeline must run
    and produce a total. Loose assertions — real receipts vary; this is a smoke
    test that the chain works, not an exactness check."""
    img = cv2.imread(path)
    assert img is not None, f"could not read {path}"
    ok, reason = check_quality(img)
    if not ok:
        pytest.skip(f"{path} failed quality gate: {reason}")
    receipt = reconcile(parse_receipt(engine.extract_text(img)))
    assert receipt.total > 0 or receipt.subtotal > 0, f"no totals extracted from {path}"
