import numpy as np

from app.quality_gate import check_quality


def test_rejects_too_small():
    img = np.zeros((50, 50, 3), dtype=np.uint8)
    ok, reason = check_quality(img)
    assert ok is False and "resolution" in reason


def test_rejects_blurry():
    img = np.full((800, 600, 3), 127, dtype=np.uint8)  # flat => ~zero variance
    ok, reason = check_quality(img)
    assert ok is False and "blur" in reason


def test_accepts_sharp():
    img = np.zeros((800, 600, 3), dtype=np.uint8)
    img[::2] = 255  # high-frequency stripes => high Laplacian variance
    ok, reason = check_quality(img)
    assert ok is True and reason is None
