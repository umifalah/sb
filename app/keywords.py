from rapidfuzz import fuzz

KEYWORDS = {
    "subtotal": ["subtotal", "sub total"],
    "total": ["total", "grand total", "jumlah", "total bayar"],
    "tax_pb1": ["pb1", "pb 1", "pajak", "ppn", "tax"],
    "service": ["service", "service charge", "servis", "biaya layanan"],
    "discount": ["diskon", "discount", "potongan", "disc"],
    "rounding": ["pembulatan", "rounding", "pembuatan"],
}
THRESHOLD = 82


def classify_line(text: str) -> str | None:
    """Classify a receipt summary line into a known label, or None.

    `partial_ratio` lets a keyword match when it's embedded in a longer line
    (e.g. "pajak" in "Pajak 10%"), but it also makes "total" match "subtotal"
    (a true substring). We disambiguate by ranking primarily on partial match
    and breaking ties with full-string similarity, so an exact "Total" beats a
    merely-contained "subtotal".
    """
    t = text.strip().lower()
    best: tuple[float, float, str] | None = None
    for label, variants in KEYWORDS.items():
        partial = max(fuzz.partial_ratio(v, t) for v in variants)
        full = max(fuzz.ratio(v, t) for v in variants)
        if partial >= THRESHOLD:
            cand = (partial, full, label)
            if best is None or cand[:2] > best[:2]:
                best = cand
    return best[2] if best else None
