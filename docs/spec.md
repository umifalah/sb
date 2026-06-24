# Split Bill — Backend & Receipt-Extraction Spec

- **Status:** Draft (design approved)
- **Owner:** Umi F
- **Created:** 2026-06-22
- **Scope:** Backend API + receipt-extraction pipeline + thin debug web tool. Flutter frontend is owned by a separate developer and is out of scope.

---

## 1. Summary

A backend for a split-bill app. A user photographs an Indonesian restaurant/supermarket
receipt; the backend extracts the structured data (line items, quantities, prices,
per-item and bill-level discounts, tax/PB1, service charge, other fees, subtotal, total)
**without using an LLM**, using a self-hosted OCR pipeline. The system is designed to
**fail visibly** — when an image is too poor or the numbers do not reconcile, it asks the
user to re-scan or to hand-correct flagged fields, rather than guessing. Users then assign
items to people and the backend computes a fair **proportional** split. Bills are saved and
reachable by a short shareable code; no user accounts.

## 2. Goals / Non-Goals

**Goals**
- Reliable extraction of Indonesian printed receipts despite varying layouts.
- Visible failure on low-quality input (re-scan) and never silently accept low-confidence numbers.
- Fair proportional split of tax/service/fees, equal split of shared items, exact whole-Rupiah totals.
- Self-hosted extraction: no third-party AI, no per-image fees, receipts never leave our infra.
- A debug web page to measure extraction accuracy.

**Non-Goals (Out of Scope)**
- The Flutter UI (owned by another developer).
- User accounts, friends lists, running balances / debts across bills.
- International receipts, currencies other than IDR, handwritten receipts.
- LLM/vision-model extraction (explicitly rejected for trust, cost, and privacy reasons).
- Payment / money movement.

## 3. Users & Context

- **Primary goal:** a real MVP friends can use soon; reliability and speed-to-usable over building everything from scratch.
- **Region/language:** Indonesia, Bahasa Indonesia, Rupiah (IDR, whole-number currency).
- **Receipt reality:** printed thermal receipts from restaurants and supermarkets. Layout varies
  between merchants. Charges are inconsistent: sometimes PB1 tax, sometimes a service fee,
  sometimes nothing added (already included in item prices). Discounts appear per-item
  (percent or amount, varying per item) and sometimes as a whole-bill discount.

## 4. Architecture

A single **Python / FastAPI** service with three responsibilities and a debug page:

1. **Extraction service** — image → structured receipt (the hard core).
2. **Split service** — assignments → per-person breakdown (pure functions, no I/O).
3. **Bill store** — SQLite persistence, reachable by a short code.
4. **Debug web page** — served by the same app; drop an image, see OCR text + confidence +
   parsed fields + reconciliation result. The internal accuracy harness.

### Components

```
[Quality Gate] → [OCR Engine] → [Receipt Parser] → [Reconciler] → structured receipt
      │           (PaddleOCR,
      ▼            swappable)
  reject / re-scan

[Split Engine]   [Bill Store]   [Debug Web UI]
```

- **Quality Gate** — blur (Laplacian variance) + resolution check. Fails fast before OCR.
- **OCR Engine** — behind a small interface `extract_text(image) -> list[TextBox]`, where a
  `TextBox` is `{ text, confidence, box }`. PaddleOCR is the default; EasyOCR/Tesseract are
  drop-in alternatives. Start with PaddleOCR (`lang="latin"`), CPU is sufficient.
- **Receipt Parser** — layout-agnostic. Pairs item names (left) with prices (right) by row
  geometry; classifies summary lines via a fuzzy **Indonesian keyword dictionary**
  (Subtotal, Total, PB1, Pajak, Service/Servis, Biaya, Diskon, etc.). No per-merchant templates.
- **Reconciler** — recomputes `items − discounts + tax + service + fees = total`; produces an
  overall confidence and a `needs_review` list of low-confidence / non-reconciling fields.
- **Split Engine** — proportional allocation of charges; equal split of shared items;
  largest-remainder rounding so per-person totals sum exactly to the receipt total.
- **Bill Store** — SQLite, keyed by short shareable code.

## 5. Data Model

```
Receipt
  merchant_name?        string (best-effort)
  currency = "IDR"
  line_items[]:
      name              string
      quantity          int
      unit_price        int (whole Rupiah)
      line_total        int
      discount?         { type: percent|amount, value }   # per-item, optional
      confidence        0–1
  bill_discount?        { type: percent|amount, value, confidence }   # whole-bill, optional
  charges[]:                                              # all optional
      kind              tax_pb1 | service | other
      label             raw text (e.g. "PB1", "Service Charge")
      type              percent | amount
      value             the % or the Rupiah amount
      confidence        0–1
  charges_included      bool    # true if charges already baked into item prices
  subtotal              int
  total                 int
  reconciled            bool    # did recompute match printed total (within tolerance)?
  needs_review[]        list of field paths below threshold / non-reconciling

Bill
  code                  short shareable string (e.g. "K7P2QX")
  receipt               Receipt (possibly hand-corrected)
  people[]              { id, name }
  assignments[]         { line_item_index, person_ids[] }   # multiple ids = shared item
  created_at

SplitResult
  per_person[]:
      person_id, name
      items_subtotal, discount_share, tax_share, service_share, other_share, total_owed  (all int)
  grand_total           int    # guaranteed == receipt.total
```

## 6. API

- `POST /receipts/scan` — multipart image → `{ receipt, needs_rescan, needs_review[] }`.
  Runs Quality Gate → OCR → Parse → Reconcile.
- `POST /bills` — create from a (possibly corrected) receipt + people → `{ code }`.
- `GET /bills/{code}` — fetch a saved bill.
- `PUT /bills/{code}/assignments` — set/update who-ate-what.
- `GET /bills/{code}/split` — compute and return `SplitResult`.
- `GET /` — debug web page (internal; not consumed by the app).

## 7. Splitting Rules

- **Per-person items:** assigned items count fully to that person.
- **Shared items:** split equally among the assigned `person_ids`.
- **Discounts:** per-item discount reduces that item; bill-level discount reduces the
  subtotal proportionally before charges.
- **Charges (tax/service/fees):** allocated **proportionally** to each person's post-discount
  item subtotal — matching how the receipt computed the percentages.
- **Rounding:** all shares are whole Rupiah; a largest-remainder pass guarantees the sum of
  `total_owed` equals `receipt.total` exactly (no lost or phantom Rupiah).
- **Charges included:** if reconciliation only holds under the "included" assumption,
  `charges_included = true` and charges are not added on top again.

## 8. Error Handling & Correctness (core concern)

- **Blurry / low-res image** → `needs_rescan: true` with reason, before OCR.
- **OCR ran but totals don't reconcile** → `reconciled: false`, populate `needs_review[]`,
  still return the data so the user can hand-correct rather than re-scan from scratch.
- **Low-confidence fields** → listed in `needs_review[]` for targeted tap-to-fix. Never
  silently accepted.
- **Charges included vs added** → resolved during reconciliation to avoid double-counting.

## 9. Testing Strategy

- **Split Engine** — pure-function unit tests: proportional allocation, shared items,
  per-item + bill-level discounts, rounding-sums-exactly, no-charges, charges-included.
- **Receipt Parser** — fed saved OCR outputs (text + boxes) as fixtures; every weird real
  receipt becomes a permanent regression case. Tests parsing independent of OCR.
- **Reconciler** — table of synthetic receipts incl. deliberately broken ones that must flag.
- **End-to-end** — real images through the debug page, eyeballed then frozen as fixtures.
- The **debug web page** is the manual test harness and accuracy dashboard.

## 10. Tech Stack

- Python / FastAPI, SQLite, PaddleOCR (OCR behind a swappable interface), OpenCV/Pillow for
  the quality gate and preprocessing. CPU-only is sufficient for MVP.

## 11. Open Questions

- Confidence thresholds for `needs_rescan` vs `needs_review` — tune against real samples.
- Reconciliation tolerance (exact vs small epsilon) for IDR rounding.
- Image preprocessing depth (deskew/denoise/threshold) needed before PaddleOCR on thermal paper.
- Bill-code length/collision strategy.
