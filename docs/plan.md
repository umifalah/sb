# Split Bill Backend — Implementation Plan

> **For the implementer:** Build task-by-task, in order. Each task is TDD: write the failing test, watch it fail, write the minimal code, watch it pass. Do not skip the "watch it fail" step.

**Goal:** Build the Split Bill backend — a self-hosted FastAPI service that extracts structured data from Indonesian receipt photos (no LLM), computes a fair proportional split, and stores bills by shareable code, plus a debug web tool to measure extraction accuracy.

**Architecture:** One FastAPI app with isolated, independently testable components: Quality Gate → OCR (swappable, PaddleOCR default) → geometric/keyword Parser → Reconciler produce a structured `Receipt`; a pure-function Split Engine computes per-person shares; a SQLite Bill Store persists bills by code. The hardest correctness work lives in pure functions (Split Engine, Reconciler, Parser) that are tested without any I/O.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite (`sqlite3` stdlib), PaddleOCR, OpenCV + Pillow (quality gate / preprocessing), pytest.

**Spec:** [spec.md](spec.md)

---

## Approach

- **Build inward-out by risk, but test pure logic first.** The pure functions (Split Engine, Reconciler) have zero dependencies and encode the correctness Umi F cares about — build them first to lock the money math. Then the extraction pipeline. Then persistence and API. Then wire end-to-end.
- **OCR behind an interface.** Everything downstream of OCR consumes a `list[TextBox]`, never PaddleOCR directly. The parser is tested against *saved* `TextBox` fixtures, so parser tests never run OCR and become a permanent regression corpus.
- **Fail visibly.** The Quality Gate and Reconciler are the safety net; their job is to populate `needs_rescan` / `needs_review` rather than let a wrong number through.
- **Money is integer Rupiah everywhere.** No floats for currency. Allocation uses largest-remainder rounding so per-person totals sum exactly to the receipt total.

## File Structure

```
sb/
  requirements.txt
  app/
    __init__.py
    main.py            # FastAPI app; wires routers; mounts debug page
    models.py          # Pydantic: Receipt, LineItem, Charge, Discount, Bill, Person, Assignment, SplitResult
    quality_gate.py    # blur (Laplacian variance) + resolution check
    ocr/
      __init__.py
      base.py          # TextBox dataclass + OCREngine protocol
      paddle_engine.py # PaddleOCR adapter implementing OCREngine
    keywords.py        # Indonesian keyword dictionary + fuzzy matcher
    parser.py          # list[TextBox] -> Receipt (geometry rows + keyword classify)
    reconciler.py      # recompute totals -> reconciled flag + needs_review[]
    split_engine.py    # Receipt + assignments -> SplitResult (proportional, rounding-exact)
    store.py           # SQLite persistence; bill code generation
    api/
      __init__.py
      receipts.py      # POST /receipts/scan
      bills.py         # POST/GET/PUT bills; GET split
    debug/
      page.html        # internal debug/accuracy UI
  tests/
    test_split_engine.py
    test_reconciler.py
    test_quality_gate.py
    test_keywords.py
    test_parser.py
    test_store.py
    test_api.py
    fixtures/
      ocr_outputs/     # saved list[TextBox] JSON per real receipt (regression corpus)
      images/          # real + synthetic receipt images
```

---

## Task Breakdown

## Chunk 1: Skeleton & Models

### Task 0: Project setup

**Files:**
- Create: `requirements.txt`, `app/__init__.py`, `app/ocr/__init__.py`, `app/api/__init__.py`, `tests/__init__.py`

- [ ] **Step 1:** Write `requirements.txt`:
```
fastapi
uvicorn[standard]
pydantic>=2
python-multipart
paddleocr
paddlepaddle
opencv-python-headless
pillow
rapidfuzz
pytest
httpx
```
- [ ] **Step 2:** Create empty `__init__.py` files listed above.
- [ ] **Step 3:** `python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`. (PaddleOCR/paddlepaddle is the heavy one; if install is painful in your env, defer it — only Task 5 needs it. The rest of the plan runs without it.)
- [ ] **Step 4:** Run `pytest -q`. Expected: "no tests ran" (exit 5) — confirms pytest works.

### Task 1: Domain models

**Files:**
- Create: `app/models.py`
- Test: covered indirectly; no dedicated test (Pydantic validation is exercised by later tasks).

- [ ] **Step 1:** Define Pydantic v2 models exactly mirroring spec §5:
```python
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field

class DiscountType(str, Enum):
    percent = "percent"
    amount = "amount"

class Discount(BaseModel):
    type: DiscountType
    value: int            # percent (0-100) or whole-Rupiah amount

class LineItem(BaseModel):
    name: str
    quantity: int = 1
    unit_price: int       # whole Rupiah
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
    type: DiscountType    # percent | amount
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
    person_ids: list[str]   # >1 => shared item

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
```
- [ ] **Step 2:** Run `python -c "import app.models"`. Expected: no error.

---

## Chunk 2: Money Logic (pure functions, no I/O — build & lock first)

### Task 2: Split Engine

**Files:**
- Create: `app/split_engine.py`
- Test: `tests/test_split_engine.py`

The algorithm:
1. Compute each item's **net** (`line_total` minus its per-item discount).
2. Each assigned person gets an equal share of each item they're on (shared item ⇒ net split equally among its `person_ids`; use largest-remainder so item splits are exact).
3. Sum per person ⇒ `items_subtotal`.
4. Apply **bill_discount** proportionally to `items_subtotal` (largest-remainder) ⇒ `discount_share`; post-discount weight = `items_subtotal − discount_share`.
5. Allocate each charge by `kind` proportionally to post-discount weights (largest-remainder) ⇒ `tax_share`/`service_share`/`other_share`. If `charges_included` is true, charges are already in item prices ⇒ allocate **zero** extra.
6. `total_owed = items_subtotal − discount_share + tax_share + service_share + other_share`.
7. `grand_total = sum(total_owed)` and MUST equal `receipt.total`.

- [ ] **Step 1: Write failing tests** covering the cases the spec calls out:
```python
import pytest
from app.models import (Receipt, LineItem, Charge, ChargeKind, DiscountType,
                        Discount, Person, Assignment)
from app.split_engine import split

def P(i): return Person(id=i, name=i.upper())

def test_two_people_no_charges_exact():
    r = Receipt(line_items=[LineItem(name="A", unit_price=30000, line_total=30000),
                            LineItem(name="B", unit_price=70000, line_total=70000)],
                subtotal=100000, total=100000)
    res = split(r, [P("a"), P("b")],
                [Assignment(line_item_index=0, person_ids=["a"]),
                 Assignment(line_item_index=1, person_ids=["b"])])
    owed = {p.person_id: p.total_owed for p in res.per_person}
    assert owed == {"a": 30000, "b": 70000}
    assert res.grand_total == 100000

def test_proportional_tax_allocation():
    # 10% PB1 on 100000 => 10000, split proportional to 30/70
    r = Receipt(line_items=[LineItem(name="A", unit_price=30000, line_total=30000),
                            LineItem(name="B", unit_price=70000, line_total=70000)],
                charges=[Charge(kind=ChargeKind.tax_pb1, label="PB1",
                                type=DiscountType.amount, value=10000)],
                subtotal=100000, total=110000)
    res = split(r, [P("a"), P("b")],
                [Assignment(line_item_index=0, person_ids=["a"]),
                 Assignment(line_item_index=1, person_ids=["b"])])
    owed = {p.person_id: p.total_owed for p in res.per_person}
    assert owed == {"a": 33000, "b": 77000}
    assert res.grand_total == 110000

def test_shared_item_split_equally():
    r = Receipt(line_items=[LineItem(name="Fries", unit_price=30000, line_total=30000)],
                subtotal=30000, total=30000)
    res = split(r, [P("a"), P("b"), P("c")],
                [Assignment(line_item_index=0, person_ids=["a", "b", "c"])])
    owed = sorted(p.total_owed for p in res.per_person)
    assert owed == [10000, 10000, 10000]
    assert res.grand_total == 30000

def test_rounding_sums_exactly_on_indivisible():
    # 10000 split 3 ways => 3334/3333/3333, sum == 10000
    r = Receipt(line_items=[LineItem(name="X", unit_price=10000, line_total=10000)],
                subtotal=10000, total=10000)
    res = split(r, [P("a"), P("b"), P("c")],
                [Assignment(line_item_index=0, person_ids=["a", "b", "c"])])
    shares = sorted(p.total_owed for p in res.per_person)
    assert sum(shares) == 10000
    assert shares == [3333, 3333, 3334]

def test_per_item_discount():
    r = Receipt(line_items=[LineItem(name="A", unit_price=50000, line_total=50000,
                                     discount=Discount(type=DiscountType.amount, value=10000))],
                subtotal=40000, total=40000)
    res = split(r, [P("a")], [Assignment(line_item_index=0, person_ids=["a"])])
    assert res.per_person[0].total_owed == 40000

def test_bill_discount_proportional():
    r = Receipt(line_items=[LineItem(name="A", unit_price=40000, line_total=40000),
                            LineItem(name="B", unit_price=60000, line_total=60000)],
                bill_discount=Discount(type=DiscountType.amount, value=10000),
                subtotal=100000, total=90000)
    res = split(r, [P("a"), P("b")],
                [Assignment(line_item_index=0, person_ids=["a"]),
                 Assignment(line_item_index=1, person_ids=["b"])])
    owed = {p.person_id: p.total_owed for p in res.per_person}
    assert owed == {"a": 36000, "b": 54000}
    assert res.grand_total == 90000

def test_charges_included_adds_nothing_extra():
    r = Receipt(line_items=[LineItem(name="A", unit_price=100000, line_total=100000)],
                charges=[Charge(kind=ChargeKind.service, label="incl",
                                type=DiscountType.amount, value=5000)],
                charges_included=True, subtotal=100000, total=100000)
    res = split(r, [P("a")], [Assignment(line_item_index=0, person_ids=["a"])])
    assert res.per_person[0].total_owed == 100000
    assert res.grand_total == 100000
```
- [ ] **Step 2: Run** `pytest tests/test_split_engine.py -v`. Expected: all FAIL (`split` undefined / ImportError).
- [ ] **Step 3: Implement** `app/split_engine.py`:
```python
from app.models import Receipt, Person, Assignment, SplitResult, PersonShare, DiscountType

def _discount_amount(d, base):
    if d is None:
        return 0
    return round(base * d.value / 100) if d.type == DiscountType.percent else min(d.value, base)

def _charge_amount(c, subtotal):
    return round(subtotal * c.value / 100) if c.type == DiscountType.percent else c.value

def _allocate(total, weights):
    """Largest-remainder: distribute integer `total` across weights, summing exactly."""
    s = sum(weights)
    if s == 0 or total == 0:
        return [0] * len(weights)
    raw = [total * w / s for w in weights]
    floors = [int(x) for x in raw]
    remainder = total - sum(floors)
    # hand out the leftover Rupiah to the largest fractional parts
    order = sorted(range(len(weights)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors

def split(receipt: Receipt, people: list[Person], assignments: list[Assignment]) -> SplitResult:
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

    tax = [0] * len(people); service = [0] * len(people); other = [0] * len(people)
    if not receipt.charges_included:
        post_disc_subtotal = sum(weights)
        for c in receipt.charges:
            amt = _charge_amount(c, post_disc_subtotal)
            alloc = _allocate(amt, weights)
            bucket = {"tax_pb1": tax, "service": service, "other": other}[c.kind.value]
            for i in range(len(people)):
                bucket[i] += alloc[i]

    shares = []
    for i, p in enumerate(people):
        owed = items_subtotal[i] - discount_share[i] + tax[i] + service[i] + other[i]
        shares.append(PersonShare(person_id=p.id, name=p.name,
                                  items_subtotal=items_subtotal[i], discount_share=discount_share[i],
                                  tax_share=tax[i], service_share=service[i], other_share=other[i],
                                  total_owed=owed))
    return SplitResult(per_person=shares, grand_total=sum(s.total_owed for s in shares))
```
- [ ] **Step 4: Run** `pytest tests/test_split_engine.py -v`. Expected: all PASS.

### Task 3: Reconciler

**Files:**
- Create: `app/reconciler.py`
- Test: `tests/test_reconciler.py`

Computes `expected_total = subtotal − bill_discount + charges` (or validates against an "included" interpretation), sets `reconciled`, and appends field paths to `needs_review` when totals don't match within tolerance or any field confidence is below threshold.

- [ ] **Step 1: Write failing tests:**
```python
from app.models import Receipt, LineItem, Charge, ChargeKind, DiscountType
from app.reconciler import reconcile

def test_clean_receipt_reconciles():
    r = Receipt(line_items=[LineItem(name="A", unit_price=100000, line_total=100000, confidence=0.99)],
                charges=[Charge(kind=ChargeKind.tax_pb1, label="PB1",
                                type=DiscountType.amount, value=10000, confidence=0.98)],
                subtotal=100000, total=110000)
    out = reconcile(r)
    assert out.reconciled is True
    assert out.needs_review == []

def test_mismatch_flags_not_reconciled():
    r = Receipt(line_items=[LineItem(name="A", unit_price=100000, line_total=100000)],
                subtotal=100000, total=130000)  # 130000 != 100000
    out = reconcile(r)
    assert out.reconciled is False
    assert any("total" in f for f in out.needs_review)

def test_low_confidence_field_flagged():
    r = Receipt(line_items=[LineItem(name="A", unit_price=100000, line_total=100000, confidence=0.40)],
                subtotal=100000, total=100000)
    out = reconcile(r)
    assert any("line_items[0]" in f for f in out.needs_review)

def test_charges_included_interpretation():
    # subtotal already equals total; a service line is present but baked in
    r = Receipt(line_items=[LineItem(name="A", unit_price=100000, line_total=100000)],
                charges=[Charge(kind=ChargeKind.service, label="SC",
                                type=DiscountType.amount, value=5000)],
                subtotal=100000, total=100000)
    out = reconcile(r)
    assert out.reconciled is True
    assert out.charges_included is True
```
- [ ] **Step 2: Run** `pytest tests/test_reconciler.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `app/reconciler.py`:
```python
from app.models import Receipt, DiscountType

CONF_THRESHOLD = 0.70
TOLERANCE = 0  # whole-Rupiah exact; relax later if real data needs it

def _amt(obj_type, value, base):
    return round(base * value / 100) if obj_type == DiscountType.percent else value

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
```
- [ ] **Step 4: Run** `pytest tests/test_reconciler.py -v`. Expected: PASS.

---

## Chunk 3: Extraction Pipeline

### Task 4: Quality Gate

**Files:**
- Create: `app/quality_gate.py`
- Test: `tests/test_quality_gate.py`

Returns `(ok: bool, reason: str | None)`. Rejects images below a resolution floor or below a blur (Laplacian variance) threshold.

- [ ] **Step 1: Write failing tests** using synthetic numpy images (a sharp checkerboard vs a uniform/blurred image):
```python
import numpy as np
from app.quality_gate import check_quality

def test_rejects_too_small():
    img = np.zeros((50, 50, 3), dtype=np.uint8)
    ok, reason = check_quality(img)
    assert ok is False and "resolution" in reason

def test_rejects_blurry():
    img = np.full((800, 600, 3), 127, dtype=np.uint8)  # flat => ~zero variance
    ok, reason = check_quality(img)
    assert ok is False and "blur" in reason

def test_accepts_sharp():
    img = np.zeros((800, 600, 3), dtype=np.uint8)
    img[::2] = 255  # high-frequency stripes => high Laplacian variance
    ok, reason = check_quality(img)
    assert ok is True and reason is None
```
- [ ] **Step 2: Run** `pytest tests/test_quality_gate.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `app/quality_gate.py`:
```python
import cv2
import numpy as np

MIN_WIDTH, MIN_HEIGHT = 400, 400
BLUR_THRESHOLD = 100.0  # tune against real receipts

def check_quality(img: np.ndarray) -> tuple[bool, str | None]:
    h, w = img.shape[:2]
    if w < MIN_WIDTH or h < MIN_HEIGHT:
        return False, f"resolution too low ({w}x{h})"
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    variance = cv2.Laplacian(gray, cv2.CV_64F).var()
    if variance < BLUR_THRESHOLD:
        return False, f"image too blurry (sharpness={variance:.0f})"
    return True, None
```
- [ ] **Step 4: Run** `pytest tests/test_quality_gate.py -v`. Expected: PASS.

### Task 5: OCR interface + PaddleOCR adapter

**Files:**
- Create: `app/ocr/base.py`, `app/ocr/paddle_engine.py`

- [ ] **Step 1:** Define the interface in `app/ocr/base.py`:
```python
from dataclasses import dataclass
from typing import Protocol
import numpy as np

@dataclass
class TextBox:
    text: str
    confidence: float
    box: list[list[float]]   # 4 [x,y] corner points

    @property
    def cx(self) -> float: return sum(p[0] for p in self.box) / 4
    @property
    def cy(self) -> float: return sum(p[1] for p in self.box) / 4

class OCREngine(Protocol):
    def extract_text(self, image: np.ndarray) -> list[TextBox]: ...
```
- [ ] **Step 2:** Implement `app/ocr/paddle_engine.py` (adapter only — no unit test; covered by the end-to-end task because it needs the heavy model):
```python
import numpy as np
from app.ocr.base import TextBox

class PaddleEngine:
    def __init__(self, lang: str = "latin"):
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

    def extract_text(self, image: np.ndarray) -> list[TextBox]:
        raw = self._ocr.ocr(image, cls=True)
        boxes: list[TextBox] = []
        for line in (raw[0] or []):
            box, (text, conf) = line
            boxes.append(TextBox(text=text, confidence=float(conf), box=box))
        return boxes
```
- [ ] **Step 3:** Run `python -c "from app.ocr.paddle_engine import PaddleEngine"`. Expected: imports without error (downloads model on first real call). If PaddleOCR isn't installed yet, skip — only the end-to-end task exercises this.

### Task 6: Indonesian keyword dictionary + fuzzy matcher

**Files:**
- Create: `app/keywords.py`
- Test: `tests/test_keywords.py`

- [ ] **Step 1: Write failing tests:**
```python
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
```
- [ ] **Step 2: Run** `pytest tests/test_keywords.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `app/keywords.py` using `rapidfuzz`:
```python
from rapidfuzz import fuzz

KEYWORDS = {
    "subtotal": ["subtotal", "sub total"],
    "total":    ["total", "grand total", "jumlah", "total bayar"],
    "tax_pb1":  ["pb1", "pb 1", "pajak", "ppn", "tax"],
    "service":  ["service", "service charge", "servis", "biaya layanan"],
    "discount": ["diskon", "discount", "potongan", "disc"],
}
THRESHOLD = 82

def classify_line(text: str) -> str | None:
    t = text.strip().lower()
    best_label, best_score = None, 0
    for label, variants in KEYWORDS.items():
        for v in variants:
            score = fuzz.partial_ratio(v, t)
            if score > best_score:
                best_label, best_score = label, score
    return best_label if best_score >= THRESHOLD else None
```
Note: order matters for ambiguous strings ("total" inside "subtotal") — `subtotal` is checked as its own variant set; "Sub Total" scores higher against `subtotal` than `total`. If real receipts break this, add a length/prefix tie-break here (documented in Risks).
- [ ] **Step 4: Run** `pytest tests/test_keywords.py -v`. Expected: PASS.

### Task 7: Receipt Parser

**Files:**
- Create: `app/parser.py`
- Test: `tests/test_parser.py` + `tests/fixtures/ocr_outputs/`

Turns `list[TextBox]` into a `Receipt`: groups boxes into rows by `cy` proximity, splits each row into left (label/name) and right (number), parses Indonesian number format, classifies summary rows via `classify_line`, and treats remaining priced rows as line items.

- [ ] **Step 1: Write a failing test** from a hand-authored fixture (simulating a real receipt's OCR output) saved at `tests/fixtures/ocr_outputs/simple.json`. The fixture is a list of `{text, confidence, box}`. Test asserts the parser recovers 2 items, a PB1 charge, subtotal, and total:
```python
import json
from app.ocr.base import TextBox
from app.parser import parse_receipt

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
```
Create `tests/fixtures/ocr_outputs/simple.json` with boxes laid out in rows (x increases L→R, y increases top→bottom), e.g. rows for "Nasi Goreng / 45.000", "Es Teh / 30.000", "Subtotal / 75.000", "PB1 / 7.500", "Total / 82.500".
- [ ] **Step 2: Run** `pytest tests/test_parser.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `app/parser.py`:
```python
import re
from app.ocr.base import TextBox
from app.models import Receipt, LineItem, Charge, ChargeKind, DiscountType
from app.keywords import classify_line

_NUM = re.compile(r"\d[\d.,]*\d|\d")

def _parse_amount(text: str) -> int | None:
    m = _NUM.search(text)
    if not m:
        return None
    # Indonesian: '.' is thousands sep, ',' decimal. Drop decimals; keep whole Rupiah.
    s = m.group().replace(".", "").split(",")[0]
    return int(s) if s.isdigit() else None

def _rows(boxes: list[TextBox], y_tol: float = 12.0) -> list[list[TextBox]]:
    rows: list[list[TextBox]] = []
    for b in sorted(boxes, key=lambda b: b.cy):
        if rows and abs(b.cy - rows[-1][0].cy) <= y_tol:
            rows[-1].append(b)
        else:
            rows.append([b])
    for r in rows:
        r.sort(key=lambda b: b.cx)
    return rows

def parse_receipt(boxes: list[TextBox]) -> Receipt:
    receipt = Receipt()
    for row in _rows(boxes):
        right = row[-1]
        amount = _parse_amount(right.text)
        label_text = " ".join(b.text for b in row[:-1]) if len(row) > 1 else row[0].text
        kind = classify_line(label_text)
        conf = min(b.confidence for b in row)
        if amount is None:
            continue
        if kind == "subtotal":
            receipt.subtotal = amount
        elif kind == "total":
            receipt.total = amount
        elif kind == "tax_pb1":
            receipt.charges.append(Charge(kind=ChargeKind.tax_pb1, label=label_text,
                                          type=DiscountType.amount, value=amount, confidence=conf))
        elif kind == "service":
            receipt.charges.append(Charge(kind=ChargeKind.service, label=label_text,
                                          type=DiscountType.amount, value=amount, confidence=conf))
        elif kind == "discount":
            from app.models import Discount
            receipt.bill_discount = Discount(type=DiscountType.amount, value=amount)
        else:
            receipt.line_items.append(LineItem(name=label_text, unit_price=amount,
                                               line_total=amount, confidence=conf))
    return receipt
```
- [ ] **Step 4: Run** `pytest tests/test_parser.py -v`. Expected: PASS.
- [ ] **Step 5:** Every new real receipt you test later that parses wrong → save its OCR output as a new fixture + assertion here. This file is the regression corpus.

---

## Chunk 4: Persistence & API

### Task 8: Bill Store (SQLite)

**Files:**
- Create: `app/store.py`
- Test: `tests/test_store.py`

Stores a `Bill` as JSON in one SQLite table keyed by a short code; generates collision-checked codes.

- [ ] **Step 1: Write failing tests** (use a temp DB path via `tmp_path`):
```python
from app.store import BillStore
from app.models import Bill, Person

def test_save_and_load_roundtrip(tmp_path):
    store = BillStore(tmp_path / "t.db")
    bill = Bill(code="", receipt=__import__("app.models", fromlist=["Receipt"]).Receipt(),
                people=[Person(id="a", name="A")], assignments=[])
    code = store.save(bill)
    assert len(code) >= 6
    loaded = store.load(code)
    assert loaded.people[0].name == "A"

def test_codes_are_unique(tmp_path):
    store = BillStore(tmp_path / "t.db")
    from app.models import Receipt
    c1 = store.save(Bill(code="", receipt=Receipt(), people=[], assignments=[]))
    c2 = store.save(Bill(code="", receipt=Receipt(), people=[], assignments=[]))
    assert c1 != c2
```
Add `Bill` to `app/models.py` if not already present:
```python
class Bill(BaseModel):
    code: str = ""
    receipt: Receipt
    people: list[Person] = []
    assignments: list[Assignment] = []
    created_at: str | None = None
```
- [ ] **Step 2: Run** `pytest tests/test_store.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `app/store.py`:
```python
import sqlite3, secrets, string
from pathlib import Path
from app.models import Bill

_ALPHABET = string.ascii_uppercase + string.digits

class BillStore:
    def __init__(self, db_path: str | Path = "bills.db"):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS bills (code TEXT PRIMARY KEY, data TEXT)")
        self.db.commit()

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            if not self.db.execute("SELECT 1 FROM bills WHERE code=?", (code,)).fetchone():
                return code

    def save(self, bill: Bill) -> str:
        if not bill.code:
            bill.code = self._new_code()
        self.db.execute("INSERT OR REPLACE INTO bills VALUES (?, ?)",
                        (bill.code, bill.model_dump_json()))
        self.db.commit()
        return bill.code

    def load(self, code: str) -> Bill | None:
        row = self.db.execute("SELECT data FROM bills WHERE code=?", (code,)).fetchone()
        return Bill.model_validate_json(row[0]) if row else None
```
- [ ] **Step 4: Run** `pytest tests/test_store.py -v`. Expected: PASS.

### Task 9: API endpoints

**Files:**
- Create: `app/api/receipts.py`, `app/api/bills.py`, `app/main.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing tests** with FastAPI `TestClient`, mocking OCR so the API test never loads PaddleOCR:
```python
from fastapi.testclient import TestClient
from app.main import create_app
from app.ocr.base import TextBox

class FakeOCR:
    def extract_text(self, image):
        return [TextBox("Nasi Goreng", 0.99, [[10,10],[120,10],[120,30],[10,30]]),
                TextBox("45.000", 0.98, [[300,10],[360,10],[360,30],[300,30]]),
                TextBox("Total", 0.99, [[10,60],[80,60],[80,80],[10,80]]),
                TextBox("45.000", 0.98, [[300,60],[360,60],[360,80],[300,80]])]

def client(tmp_path):
    return TestClient(create_app(ocr=FakeOCR(), db_path=tmp_path / "t.db"))

def test_scan_returns_receipt(tmp_path):
    c = client(tmp_path)
    # 1x1 png is below quality gate; use a generated sharp image instead
    import numpy as np, cv2
    img = np.zeros((800,600,3), np.uint8); img[::2]=255
    ok, buf = cv2.imencode(".png", img)
    r = c.post("/receipts/scan", files={"file": ("r.png", buf.tobytes(), "image/png")})
    assert r.status_code == 200
    assert r.json()["receipt"]["line_items"][0]["name"].lower().startswith("nasi")

def test_create_and_split_bill(tmp_path):
    c = client(tmp_path)
    receipt = {"line_items":[{"name":"A","unit_price":30000,"line_total":30000},
                             {"name":"B","unit_price":70000,"line_total":70000}],
               "subtotal":100000,"total":100000}
    create = c.post("/bills", json={"receipt": receipt,
                                    "people":[{"id":"a","name":"A"},{"id":"b","name":"B"}]})
    code = create.json()["code"]
    c.put(f"/bills/{code}/assignments", json={"assignments":[
        {"line_item_index":0,"person_ids":["a"]},
        {"line_item_index":1,"person_ids":["b"]}]})
    split = c.get(f"/bills/{code}/split").json()
    owed = {p["person_id"]: p["total_owed"] for p in split["per_person"]}
    assert owed == {"a":30000, "b":70000}
```
- [ ] **Step 2: Run** `pytest tests/test_api.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the routers and `create_app(ocr, db_path)` factory in `app/main.py` (dependency-inject the OCR engine + store so tests pass fakes). Wire: `/receipts/scan` → decode image → `check_quality` → `ocr.extract_text` → `parse_receipt` → `reconcile` → return `{receipt, needs_rescan, needs_review}`. `/bills` endpoints → `BillStore` + `split`.
- [ ] **Step 4: Run** `pytest tests/test_api.py -v`. Expected: PASS.

### Task 10: Debug web page

**Files:**
- Create: `app/debug/page.html`; add `GET /` route in `app/main.py`

- [ ] **Step 1:** Single static HTML page: file input → POST to `/receipts/scan` → render the returned OCR boxes, parsed line items, confidence per field, `reconciled`, and `needs_review`. Plain HTML + fetch, no framework.
- [ ] **Step 2:** Manual check — run `uvicorn app.main:app`, open `/`, upload a receipt photo, confirm fields render. (No automated test; this is a human tool.)

---

## Chunk 5: End-to-End & Accuracy

### Task 11: End-to-end wiring with a real receipt

**Files:**
- Test: `tests/test_e2e.py` (marked `@pytest.mark.slow`, skipped if PaddleOCR absent)

- [ ] **Step 1:** Place 2-3 real Indonesian receipt photos in `tests/fixtures/images/`.
- [ ] **Step 2:** Write a slow test: real image → full pipeline with the real `PaddleEngine` → assert it reconciles (or is correctly flagged). Mark `@pytest.mark.slow`.
- [ ] **Step 3:** Run `pytest -m slow -v`. Eyeball output via the debug page; for any receipt that parses wrong, save its OCR output as a `tests/fixtures/ocr_outputs/` fixture and add a parser regression test (Task 7, Step 5).
- [ ] **Step 4:** Run the full suite `pytest -q`. Expected: all PASS (slow tests skipped without the model).

---

## Risks & Unknowns

- **PaddleOCR install friction** — `paddlepaddle` can be awkward on some platforms. Mitigation: OCR is behind `OCREngine`; swap in EasyOCR with a `tests`-identical adapter if needed. Only Tasks 5 & 11 depend on it.
- **Indonesian number/format variance** — thermal receipts vary; `_parse_amount` and `classify_line` thresholds will need tuning against real samples. Mitigation: the fixture corpus (Task 7 Step 5) turns each failure into a permanent test.
- **Row grouping `y_tol`** — items wrapping across two lines, or two columns, can break the row heuristic. Mitigation: tune `y_tol`; add multi-line handling only if real receipts require it (YAGNI until then).
- **Reconciliation tolerance** — currently exact (`TOLERANCE = 0`). Real receipts may round per-line; relax to a small epsilon once measured.
- **Keyword ambiguity** — "Total" vs "Subtotal" vs "Total Bayar". Mitigation: covered by `classify_line` tests; add tie-breaks if real data breaks them.

## Testing Strategy

- **Pure logic (Split Engine, Reconciler, keywords)** — exhaustive unit tests; these encode the money correctness and run with zero dependencies.
- **Parser** — tested against saved `TextBox` fixtures (the regression corpus), never against live OCR.
- **API** — `TestClient` with a fake OCR engine and a temp SQLite DB; fast, deterministic, no model load.
- **End-to-end** — real images through the real model, marked slow, skipped in normal runs.
- **Quality Gate** — synthetic numpy images for sharp/blurry/small.
- Run `pytest -q` for the fast suite; `pytest -m slow` when validating real extraction.

---

## Suggested Build Order

Tasks are already in dependency order: 0 → 1 → (2, 3 money logic) → (4–7 extraction) → (8, 9 persistence/API) → 10 debug → 11 e2e. Each task leaves the suite green. Start with Task 2 (Split Engine) right after the skeleton — it's the highest-value, lowest-dependency code and locks the math Umi F cares most about.
