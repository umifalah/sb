from app.keywords import classify_line


def test_matches_subtotal_variants():
    assert classify_line("Subtotal") == "subtotal"
    assert classify_line("Sub Total") == "subtotal"


def test_matches_tax_pb1():
    assert classify_line("PB1") == "tax_pb1"
    assert classify_line("Pajak 10%") == "tax_pb1"


def test_matches_service_and_discount_and_total():
    assert classify_line("Service Charge") == "service"
    assert classify_line("Servis") == "service"
    assert classify_line("Diskon") == "discount"
    assert classify_line("Total") == "total"


def test_unknown_returns_none():
    assert classify_line("Nasi Goreng Spesial") is None
