from app.models import Bill, Person, Receipt
from app.store import BillStore


def test_save_and_load_roundtrip(tmp_path):
    store = BillStore(tmp_path / "t.db")
    bill = Bill(receipt=Receipt(), people=[Person(id="a", name="A")], assignments=[])
    code = store.save(bill)
    assert len(code) >= 6
    loaded = store.load(code)
    assert loaded is not None
    assert loaded.people[0].name == "A"


def test_codes_are_unique(tmp_path):
    store = BillStore(tmp_path / "t.db")
    c1 = store.save(Bill(receipt=Receipt()))
    c2 = store.save(Bill(receipt=Receipt()))
    assert c1 != c2


def test_load_missing_returns_none(tmp_path):
    store = BillStore(tmp_path / "t.db")
    assert store.load("NOPE12") is None
