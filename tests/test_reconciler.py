from app.models import Charge, ChargeKind, DiscountType, LineItem, Receipt
from app.reconciler import reconcile


def test_clean_receipt_reconciles():
    r = Receipt(
        line_items=[
            LineItem(
                name="A", unit_price=100000, line_total=100000, confidence=0.99
            )
        ],
        charges=[
            Charge(
                kind=ChargeKind.tax_pb1,
                label="PB1",
                type=DiscountType.amount,
                value=10000,
                confidence=0.98,
            )
        ],
        subtotal=100000,
        total=110000,
    )
    out = reconcile(r)
    assert out.reconciled is True
    assert out.needs_review == []


def test_mismatch_flags_not_reconciled():
    r = Receipt(
        line_items=[LineItem(name="A", unit_price=100000, line_total=100000)],
        subtotal=100000,
        total=130000,  # 130000 != 100000
    )
    out = reconcile(r)
    assert out.reconciled is False
    assert any("total" in f for f in out.needs_review)


def test_low_confidence_field_flagged():
    r = Receipt(
        line_items=[
            LineItem(
                name="A", unit_price=100000, line_total=100000, confidence=0.40
            )
        ],
        subtotal=100000,
        total=100000,
    )
    out = reconcile(r)
    assert any("line_items[0]" in f for f in out.needs_review)


def test_empty_receipt_does_not_falsely_reconcile():
    # Parser extracted nothing -> subtotal/total both 0. "0 == 0" must NOT pass.
    r = Receipt()
    out = reconcile(r)
    assert out.reconciled is False
    assert "total" in out.needs_review


def test_zero_total_with_items_flagged():
    r = Receipt(
        line_items=[LineItem(name="A", unit_price=50000, line_total=50000)],
        subtotal=50000,
        total=0,  # no total parsed
    )
    out = reconcile(r)
    assert out.reconciled is False
    assert "total" in out.needs_review


def test_charges_included_interpretation():
    # subtotal already equals total; a service line is present but baked in
    r = Receipt(
        line_items=[LineItem(name="A", unit_price=100000, line_total=100000)],
        charges=[
            Charge(
                kind=ChargeKind.service,
                label="SC",
                type=DiscountType.amount,
                value=5000,
            )
        ],
        subtotal=100000,
        total=100000,
    )
    out = reconcile(r)
    assert out.reconciled is True
    assert out.charges_included is True
