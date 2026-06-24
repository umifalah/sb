import json

from app.ocr.base import TextBox
from app.parser import parse_receipt
from app.reconciler import reconcile


def load(name):
    with open(f"tests/fixtures/ocr_outputs/{name}.json") as f:
        return [TextBox(**b) for b in json.load(f)]


def test_parses_simple_receipt():
    r = parse_receipt(load("simple"))
    assert len(r.line_items) == 2
    assert r.line_items[0].name.lower().startswith("nasi")
    assert r.line_items[0].unit_price == 45000
    assert r.subtotal == 75000
    assert any(c.kind.value == "tax_pb1" for c in r.charges)
    assert r.total == 82500


def test_parses_real_freezh_receipt():
    # Real PaddleOCR output from tests/fixtures/images/freezh.jpg (single-line
    # items, English wording, price right-aligned slightly above its label).
    r = reconcile(parse_receipt(load("real_freezh")))
    assert [it.name for it in r.line_items] == ["Pink Please", "Purple Pop", "Sun n Sea"]
    assert [it.line_total for it in r.line_items] == [32000, 36000, 36000]
    charges = {c.kind.value: c.value for c in r.charges}
    assert charges == {"service": 2080, "tax_pb1": 10608}
    assert r.subtotal == 104000
    assert r.total == 116688
    assert r.reconciled is True


def test_parses_real_bone_receipt():
    # Real PaddleOCR output from tests/fixtures/images/BONE.jpg (two-line items:
    # name on one line, "qty x unit" + line total on the next; includes a
    # negative "Pembulatan" rounding line).
    r = reconcile(parse_receipt(load("real_bone")))
    assert [it.name for it in r.line_items] == [
        "Beef Pho",
        "Bone steak hamburg (rice)",
        "Sweet Ice Tea",
        "Lumpia Kukus",
    ]
    assert [it.line_total for it in r.line_items] == [76000, 48000, 45000, 17000]
    charges = {c.kind.value: c.value for c in r.charges}
    assert charges == {"tax_pb1": 18600, "other": -100}  # other == rounding
    assert r.subtotal == 186000
    assert r.total == 204500
    assert r.reconciled is True
