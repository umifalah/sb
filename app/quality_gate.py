import cv2
import numpy as np

MIN_WIDTH, MIN_HEIGHT = 400, 400
# Tuned against real phone photos: a legible-but-soft receipt (BONE.jpg) scores
# ~58 on Laplacian variance, so 100 was too strict. 40 keeps genuinely blurry
# shots out while letting real-world photos through. Revisit with more samples.
BLUR_THRESHOLD = 40.0


def check_quality(img: np.ndarray) -> tuple[bool, str | None]:
    h, w = img.shape[:2]
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return False, f"resolution too low ({w}x{h})"
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    if variance < BLUR_THRESHOLD:
        return False, f"image too blurry (sharpness={variance:.0f})"
    return True, None
