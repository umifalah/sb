import numpy as np

from app.ocr.base import TextBox


class PaddleEngine:
    """Adapter for PaddleOCR 3.x.

    Notes on 3.x vs 2.x:
    - Entry point is `.predict()`, not `.ocr(img, cls=True)`.
    - Results are dict-like objects exposing `rec_texts`, `rec_scores`, and
      `rec_polys` (one parallel list each), not `[[box, (text, conf)], ...]`.
    - `enable_mkldnn=False` is required: the default oneDNN CPU path crashes on
      paddlepaddle 3.3.x with "ConvertPirAttribute2RuntimeAttribute not support".
    - `lang="id"` covers Indonesian (Latin script); 2.x's `lang="latin"` is gone.
    """

    def __init__(self, lang: str = "id"):
        from paddleocr import PaddleOCR

        self._ocr = PaddleOCR(
            lang=lang,
            enable_mkldnn=False,
            use_textline_orientation=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
        )

    def extract_text(self, image: np.ndarray) -> list[TextBox]:
        boxes: list[TextBox] = []
        for res in self._ocr.predict(image):
            texts = res.get("rec_texts", [])
            scores = res.get("rec_scores", [])
            polys = res.get("rec_polys", res.get("dt_polys", []))
            for text, score, poly in zip(texts, scores, polys):
                box = poly.tolist() if hasattr(poly, "tolist") else [list(p) for p in poly]
                boxes.append(TextBox(text=str(text), confidence=float(score), box=box))
        return boxes
