from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class DiscountType(str, Enum):
    percent = "percent"
    amount = "amount"


class Discount(BaseModel):
    type: DiscountType
    value: int  # percent (0-100) or whole-Rupiah amount


class LineItem(BaseModel):
    name: str
    quantity: int = 1
    unit_price: int  # whole Rupiah
    line_total: int
    discount: Discount | None = None
    confidence: float = 1.0


class ChargeKind(str, Enum):
    tax_pb1 = "tax_pb1"
    service = "service"
    other = "other"


class Charge(BaseModel):
    kind: ChargeKind
    label: str
    type: DiscountType  # percent | amount
    value: int
    confidence: float = 1.0


class Receipt(BaseModel):
    merchant_name: str | None = None
    currency: str = "IDR"
    line_items: list[LineItem] = []
    bill_discount: Discount | None = None
    charges: list[Charge] = []
    charges_included: bool = False
    subtotal: int = 0
    total: int = 0
    reconciled: bool = False
    needs_review: list[str] = []


class Person(BaseModel):
    id: str
    name: str


class Assignment(BaseModel):
    line_item_index: int
    person_ids: list[str]  # >1 => shared item


class PersonShare(BaseModel):
    person_id: str
    name: str
    items_subtotal: int = 0
    discount_share: int = 0
    tax_share: int = 0
    service_share: int = 0
    other_share: int = 0
    total_owed: int = 0


class SplitResult(BaseModel):
    per_person: list[PersonShare] = []
    grand_total: int = 0


class Bill(BaseModel):
    code: str = ""
    receipt: Receipt
    people: list[Person] = []
    assignments: list[Assignment] = []
    created_at: str | None = None


# ---- API response envelopes ----

class ScanResponse(BaseModel):
    receipt: Receipt | None = None
    needs_rescan: bool
    needs_review: list[str] = []
    reason: str | None = None


class CreateBillResponse(BaseModel):
    code: str


class OkResponse(BaseModel):
    ok: bool = True
