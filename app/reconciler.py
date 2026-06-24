from app.models import DiscountType, Receipt

CONF_THRESHOLD = 0.70
TOLERANCE = 0  # whole-Rupiah exact; relax later if real data needs it


def _amt(obj_type: DiscountType, value: int, base: int) -> int:
    if obj_type == DiscountType.percent:
        return round(base * value / 100)
    return value


def _expected_total(r: Receipt, included: bool) -> int:
    disc = 0
    if r.bill_discount:
        disc = _amt(r.bill_discount.type, r.bill_discount.value, r.subtotal)
    if included:
        return r.subtotal - disc
    charges = sum(_amt(c.type, c.value, r.subtotal - disc) for c in r.charges)
    return r.subtotal - disc + charges


def reconcile(r: Receipt) -> Receipt:
    review: list[str] = []
    # A missing/zero total can never reconcile, even though "0 == 0" would look
    # like a match when the parser extracted nothing.
    if r.total <= 0:
        r.reconciled = False
        review.append("total")
        for i, it in enumerate(r.line_items):
            if it.confidence < CONF_THRESHOLD:
                review.append(f"line_items[{i}]")
        r.needs_review = review
        return r

    # try "added on top" first, then "included"
    if abs(_expected_total(r, included=False) - r.total) <= TOLERANCE:
        r.reconciled = True
        r.charges_included = False
    elif abs(_expected_total(r, included=True) - r.total) <= TOLERANCE:
        r.reconciled = True
        r.charges_included = True
    else:
        r.reconciled = False
        review.append("total")

    for i, it in enumerate(r.line_items):
        if it.confidence < CONF_THRESHOLD:
            review.append(f"line_items[{i}]")
    for i, c in enumerate(r.charges):
        if c.confidence < CONF_THRESHOLD:
            review.append(f"charges[{i}]")
    r.needs_review = review
    return r
