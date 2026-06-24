from app.models import (
    Assignment,
    Discount,
    DiscountType,
    Person,
    PersonShare,
    Receipt,
    SplitResult,
)


def _discount_amount(d: Discount | None, base: int) -> int:
    if d is None:
        return 0
    if d.type == DiscountType.percent:
        return round(base * d.value / 100)
    return min(d.value, base)


def _charge_amount(charge_type: DiscountType, value: int, subtotal: int) -> int:
    if charge_type == DiscountType.percent:
        return round(subtotal * value / 100)
    return value


def _allocate(total: int, weights: list[int]) -> list[int]:
    """Largest-remainder: distribute integer `total` across weights, summing exactly.

    Works on the magnitude and reapplies the sign, so negative amounts (e.g. a
    -100 rounding/Pembulatan charge) also sum back to exactly `total`.
    """
    s = sum(weights)
    if s == 0 or total == 0:
        return [0] * len(weights)
    sign = -1 if total < 0 else 1
    mag = abs(total)
    raw = [mag * w / s for w in weights]
    floors = [int(x) for x in raw]
    remainder = mag - sum(floors)
    # hand out the leftover Rupiah to the largest fractional parts
    order = sorted(range(len(weights)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return [sign * f for f in floors]


def split(
    receipt: Receipt, people: list[Person], assignments: list[Assignment]
) -> SplitResult:
    idx = {p.id: n for n, p in enumerate(people)}
    items_subtotal = [0] * len(people)
    for a in assignments:
        item = receipt.line_items[a.line_item_index]
        net = item.line_total - _discount_amount(item.discount, item.line_total)
        shares = _allocate(net, [1] * len(a.person_ids))
        for pid, sh in zip(a.person_ids, shares):
            items_subtotal[idx[pid]] += sh

    subtotal_sum = sum(items_subtotal)
    bill_disc_total = _discount_amount(receipt.bill_discount, subtotal_sum)
    discount_share = _allocate(bill_disc_total, items_subtotal)
    weights = [items_subtotal[i] - discount_share[i] for i in range(len(people))]

    tax = [0] * len(people)
    service = [0] * len(people)
    other = [0] * len(people)
    if not receipt.charges_included:
        post_disc_subtotal = sum(weights)
        for c in receipt.charges:
            amt = _charge_amount(c.type, c.value, post_disc_subtotal)
            alloc = _allocate(amt, weights)
            bucket = {"tax_pb1": tax, "service": service, "other": other}[c.kind.value]
            for i in range(len(people)):
                bucket[i] += alloc[i]

    shares: list[PersonShare] = []
    for i, p in enumerate(people):
        owed = (
            items_subtotal[i]
            - discount_share[i]
            + tax[i]
            + service[i]
            + other[i]
        )
        shares.append(
            PersonShare(
                person_id=p.id,
                name=p.name,
                items_subtotal=items_subtotal[i],
                discount_share=discount_share[i],
                tax_share=tax[i],
                service_share=service[i],
                other_share=other[i],
                total_owed=owed,
            )
        )
    return SplitResult(
        per_person=shares, grand_total=sum(s.total_owed for s in shares)
    )
