from app.models import (
    Assignment,
    Charge,
    ChargeKind,
    Discount,
    DiscountType,
    LineItem,
    Person,
    Receipt,
)
from app.split_engine import split


def P(i):
    return Person(id=i, name=i.upper())


def test_two_people_no_charges_exact():
    r = Receipt(
        line_items=[
            LineItem(name="A", unit_price=30000, line_total=30000),
            LineItem(name="B", unit_price=70000, line_total=70000),
        ],
        subtotal=100000,
        total=100000,
    )
    res = split(
        r,
        [P("a"), P("b")],
        [
            Assignment(line_item_index=0, person_ids=["a"]),
            Assignment(line_item_index=1, person_ids=["b"]),
        ],
    )
    owed = {p.person_id: p.total_owed for p in res.per_person}
    assert owed == {"a": 30000, "b": 70000}
    assert res.grand_total == 100000


def test_proportional_tax_allocation():
    # 10% PB1 on 100000 => 10000, split proportional to 30/70
    r = Receipt(
        line_items=[
            LineItem(name="A", unit_price=30000, line_total=30000),
            LineItem(name="B", unit_price=70000, line_total=70000),
        ],
        charges=[
            Charge(
                kind=ChargeKind.tax_pb1,
                label="PB1",
                type=DiscountType.amount,
                value=10000,
            )
        ],
        subtotal=100000,
        total=110000,
    )
    res = split(
        r,
        [P("a"), P("b")],
        [
            Assignment(line_item_index=0, person_ids=["a"]),
            Assignment(line_item_index=1, person_ids=["b"]),
        ],
    )
    owed = {p.person_id: p.total_owed for p in res.per_person}
    assert owed == {"a": 33000, "b": 77000}
    assert res.grand_total == 110000


def test_shared_item_split_equally():
    r = Receipt(
        line_items=[LineItem(name="Fries", unit_price=30000, line_total=30000)],
        subtotal=30000,
        total=30000,
    )
    res = split(
        r,
        [P("a"), P("b"), P("c")],
        [Assignment(line_item_index=0, person_ids=["a", "b", "c"])],
    )
    owed = sorted(p.total_owed for p in res.per_person)
    assert owed == [10000, 10000, 10000]
    assert res.grand_total == 30000


def test_rounding_sums_exactly_on_indivisible():
    # 10000 split 3 ways => 3334/3333/3333, sum == 10000
    r = Receipt(
        line_items=[LineItem(name="X", unit_price=10000, line_total=10000)],
        subtotal=10000,
        total=10000,
    )
    res = split(
        r,
        [P("a"), P("b"), P("c")],
        [Assignment(line_item_index=0, person_ids=["a", "b", "c"])],
    )
    shares = sorted(p.total_owed for p in res.per_person)
    assert sum(shares) == 10000
    assert shares == [3333, 3333, 3334]


def test_per_item_discount():
    r = Receipt(
        line_items=[
            LineItem(
                name="A",
                unit_price=50000,
                line_total=50000,
                discount=Discount(type=DiscountType.amount, value=10000),
            )
        ],
        subtotal=40000,
        total=40000,
    )
    res = split(r, [P("a")], [Assignment(line_item_index=0, person_ids=["a"])])
    assert res.per_person[0].total_owed == 40000


def test_bill_discount_proportional():
    r = Receipt(
        line_items=[
            LineItem(name="A", unit_price=40000, line_total=40000),
            LineItem(name="B", unit_price=60000, line_total=60000),
        ],
        bill_discount=Discount(type=DiscountType.amount, value=10000),
        subtotal=100000,
        total=90000,
    )
    res = split(
        r,
        [P("a"), P("b")],
        [
            Assignment(line_item_index=0, person_ids=["a"]),
            Assignment(line_item_index=1, person_ids=["b"]),
        ],
    )
    owed = {p.person_id: p.total_owed for p in res.per_person}
    assert owed == {"a": 36000, "b": 54000}
    assert res.grand_total == 90000


def test_negative_rounding_charge_sums_exactly_three_way():
    # A -100 "Pembulatan" (rounding) charge split across 3 uneven weights must
    # still sum to exactly -100 and keep grand_total == receipt.total.
    r = Receipt(
        line_items=[
            LineItem(name="X", unit_price=61500, line_total=61500),
            LineItem(name="Y", unit_price=61500, line_total=61500),
            LineItem(name="Z", unit_price=63000, line_total=63000),
        ],
        charges=[
            Charge(kind=ChargeKind.other, label="Pembulatan",
                   type=DiscountType.amount, value=-100)
        ],
        subtotal=186000,
        total=185900,
    )
    res = split(
        r,
        [P("a"), P("b"), P("c")],
        [
            Assignment(line_item_index=0, person_ids=["a"]),
            Assignment(line_item_index=1, person_ids=["b"]),
            Assignment(line_item_index=2, person_ids=["c"]),
        ],
    )
    assert sum(p.other_share for p in res.per_person) == -100
    assert res.grand_total == 185900


def test_charges_included_adds_nothing_extra():
    r = Receipt(
        line_items=[LineItem(name="A", unit_price=100000, line_total=100000)],
        charges=[
            Charge(
                kind=ChargeKind.service,
                label="incl",
                type=DiscountType.amount,
                value=5000,
            )
        ],
        charges_included=True,
        subtotal=100000,
        total=100000,
    )
    res = split(r, [P("a")], [Assignment(line_item_index=0, person_ids=["a"])])
    assert res.per_person[0].total_owed == 100000
    assert res.grand_total == 100000
