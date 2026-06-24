import re

from app.keywords import classify_line
from app.models import Charge, ChargeKind, Discount, DiscountType, LineItem, Receipt
from app.ocr.base import TextBox

# A pure amount: optional parens (negative), digits with Indonesian thousands dots.
_AMOUNT = re.compile(r"^\(?\s*\d[\d.,]*\s*\)?$")
# A "quantity x unit-price" line, e.g. "2 x 38.000", "1 × 17.000".
_QTY_LINE = re.compile(r"^\s*\d+\s*[x×*]\s*[\d.,]+\s*$")
# Trailing quantity suffix on a name, e.g. "Pink Please x 1".
_QTY_SUFFIX = re.compile(r"\s*[x×*]\s*\d+\s*$")

_CHARGE_KEYWORDS = {"tax_pb1", "service", "rounding"}
_SUMMARY_KEYWORDS = {"subtotal", "total"} | _CHARGE_KEYWORDS | {"discount"}


def _is_amount(text: str) -> bool:
    return bool(_AMOUNT.match(text.strip()))


def _amount_value(text: str) -> int | None:
    t = text.strip()
    negative = "(" in t and ")" in t  # accounting style: (100) == -100
    digits = re.sub(r"[^\d]", "", t.split(",")[0])
    if not digits:
        return None
    val = int(digits)
    return -val if negative else val


def _strip_qty_suffix(name: str) -> str:
    return _QTY_SUFFIX.sub("", name).strip()


def parse_receipt(boxes: list[TextBox]) -> Receipt:
    receipt = Receipt()
    if not boxes:
        return receipt

    content_width = max(b.x1 for b in boxes)
    right_threshold = 0.55 * content_width

    # Price anchors live in the right column and read as pure amounts.
    prices = sorted(
        (b for b in boxes if b.x0 > right_threshold and _is_amount(b.text)),
        key=lambda b: b.cy,
    )
    labels = [b for b in boxes if b not in prices]
    price_set = set(id(b) for b in prices)

    content_height = max(b.cy for b in boxes)

    summary_mode = False
    for p in prices:
        amount = _amount_value(p.text)
        if amount is None:
            continue
        partner = _nearest_label(p, labels, content_height)
        text = partner.text if partner else ""
        kind = classify_line(text)

        if kind in _SUMMARY_KEYWORDS:
            summary_mode = summary_mode or kind in ({"subtotal", "total"} | _CHARGE_KEYWORDS)
            _record_summary(receipt, kind, text, amount, p.confidence)
            continue

        if summary_mode:
            continue  # payment / change lines after the summary block

        # An item. If the partner is just a "qty x price" line (BONE-style), the
        # real name sits on the nearest line above it.
        if not text or _QTY_LINE.match(text):
            name = _name_above(partner, labels, prices, price_set) if partner else ""
        else:
            name = _strip_qty_suffix(text)
        if not name:
            name = "(unknown item)"
        receipt.line_items.append(
            LineItem(name=name, unit_price=amount, line_total=amount,
                     confidence=p.confidence)
        )

    return receipt


def _nearest_label(price: TextBox, labels: list[TextBox], content_height: float) -> TextBox | None:
    """Pair a price with its label by vertical proximity, biased toward the label
    BELOW the price. Printed line items put the amount slightly above its label,
    so an unbiased nearest-match mis-pairs interleaved rows by one. The penalty is
    small enough that a bold total (amount below its label) still pairs correctly.
    """
    candidates = [b for b in labels if not _is_amount(b.text)]
    if not candidates:
        return None
    above_penalty = max(20.0, 0.012 * content_height)

    def cost(b: TextBox) -> float:
        dist = abs(b.cy - price.cy)
        return dist + (above_penalty if b.cy < price.cy else 0.0)

    return min(candidates, key=cost)


def _name_above(qty_box, labels, prices, price_set, max_gap_ratio: float = 0.06):
    """Find the item name: nearest left box above the qty line that isn't itself
    a qty line, a keyword, or a price partner."""
    content_height = max((b.cy for b in prices), default=qty_box.cy)
    max_gap = max_gap_ratio * content_height + 60
    best = None
    for b in labels:
        if id(b) in price_set or _is_amount(b.text) or _QTY_LINE.match(b.text):
            continue
        if classify_line(b.text):
            continue
        if b.cy >= qty_box.cy:  # must be above
            continue
        if qty_box.cy - b.cy > max_gap:
            continue
        if best is None or b.cy > best.cy:  # closest above
            best = b
    return best.text if best else ""


def _record_summary(receipt, kind, text, amount, confidence):
    if kind == "subtotal":
        receipt.subtotal = amount
    elif kind == "total":
        receipt.total = amount
    elif kind == "discount":
        receipt.bill_discount = Discount(type=DiscountType.amount, value=amount)
    elif kind == "tax_pb1":
        receipt.charges.append(Charge(kind=ChargeKind.tax_pb1, label=text,
                                      type=DiscountType.amount, value=amount,
                                      confidence=confidence))
    elif kind == "service":
        receipt.charges.append(Charge(kind=ChargeKind.service, label=text,
                                      type=DiscountType.amount, value=amount,
                                      confidence=confidence))
    elif kind == "rounding":
        receipt.charges.append(Charge(kind=ChargeKind.other, label=text,
                                      type=DiscountType.amount, value=amount,
                                      confidence=confidence))
