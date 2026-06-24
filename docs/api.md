# Split Bill API — Frontend Reference

Reference for the Flutter app. The backend extracts receipt data and computes the
split; the app handles the camera, UI, and calling these endpoints.

- **Base URL (local dev):** `http://localhost:8000`
- **Demo UI (no code needed):** `http://localhost:8000/` — a simple web page to try
  the whole flow by hand (see "Try it in the browser" below).
- **Interactive docs:** `http://localhost:8000/docs` (Swagger) · `http://localhost:8000/redoc`
- **OpenAPI spec:** `http://localhost:8000/openapi.json`
  → you can generate a typed Dart client from this with
  [`openapi-generator`](https://openapi-generator.tech/) (`-g dart-dio`).
- **Money:** every amount is a **whole-Rupiah integer** (no decimals, no cents).
  Percentages (in `Discount`/`Charge` of `type: "percent"`) are integers too.

## Try it in the browser (recommended first step)

Before writing any Dart, run the backend and open **`http://localhost:8000/`** — a
small built-in demo tool that exercises the real API end-to-end:

1. **Scan a receipt** — upload a photo; see the extracted items, charges, and the
   reconcile / needs-rescan flags (this calls `POST /receipts/scan`).
2. **People** — add/rename the people splitting the bill.
3. **Who ate what** — tick who shared each item; fix any wrong item name/price.
4. **Compute Split** — see each person's amount (this calls `POST /bills` →
   `PUT /bills/{code}/assignments` → `GET /bills/{code}/split`).

It's plain HTML + `fetch`, so it doubles as a **reference implementation** of the
exact call sequence and payloads the Flutter app should use.

To run the backend:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Typical flow

```
①  POST /receipts/scan        (multipart photo)      → receipt JSON
②  [user reviews/fixes items in the app]
③  POST /bills                (receipt + people)      → { code }
④  PUT  /bills/{code}/assignments  (who ate what)     → { ok: true }
⑤  GET  /bills/{code}/split                           → per-person amounts
```

The `code` from step ③ is shareable: any friend can open the same bill and call
`GET /bills/{code}` or `GET /bills/{code}/split`.

---

## Endpoints

### ① POST `/receipts/scan`
Scan a receipt photo.

- **Request:** `multipart/form-data` with one field **`file`** (the image: jpg/png).
- **Response:** `ScanResponse`

```jsonc
{
  "receipt": { /* Receipt, or null if needs_rescan */ },
  "needs_rescan": false,        // true => image too blurry/low-res; ask to retake
  "needs_review": ["total"],    // field paths the user should confirm/fix (may be empty)
  "reason": null                // why a re-scan is needed (when needs_rescan = true)
}
```

Handling guide:
- `needs_rescan == true` → show `reason`, prompt the user to retake. `receipt` is `null`.
- `receipt.reconciled == false` → the parsed numbers don't match the printed total.
  Show the items but highlight `needs_review` fields and let the user correct them.
- Each `line_item` / `charge` has a `confidence` (0–1); values below ~0.7 are worth
  highlighting for review.

Example (curl):
```bash
curl -X POST http://localhost:8000/receipts/scan -F "file=@receipt.jpg"
```

---

### ② POST `/bills`
Create a saved bill from a (user-corrected) receipt + the people splitting it.

- **Request body:** `CreateBillRequest`
```json
{
  "receipt": {
    "line_items": [
      {"name": "Beef Pho", "unit_price": 76000, "line_total": 76000},
      {"name": "Sweet Ice Tea", "unit_price": 45000, "line_total": 45000}
    ],
    "charges": [{"kind": "tax_pb1", "label": "PB1", "type": "amount", "value": 12100}],
    "subtotal": 121000,
    "total": 133100
  },
  "people": [
    {"id": "A", "name": "Andi"},
    {"id": "B", "name": "Budi"}
  ]
}
```
- **Response:** `{ "code": "K7P2QX" }`

Notes:
- You can pass the `receipt` straight from `/receipts/scan` (after edits).
- Each person needs a **unique `id`** (used in assignments) and a display `name`.

---

### ③ GET `/bills/{code}`
Fetch a saved bill (receipt + people + current assignments).

- **Response:** `Bill`
- **404** if the code doesn't exist.

---

### ④ PUT `/bills/{code}/assignments`
Record who ate what. Replaces the bill's assignments each call.

- **Request body:** `AssignmentsRequest`
```json
{
  "assignments": [
    {"line_item_index": 0, "person_ids": ["A"]},
    {"line_item_index": 1, "person_ids": ["A", "B"]}
  ]
}
```
- **Response:** `{ "ok": true }` · **404** if the code doesn't exist.

Rules:
- `line_item_index` is the 0-based index into `receipt.line_items`.
- `person_ids` lists everyone who shared that item. **More than one id ⇒ split
  equally** among them. An item left out of all assignments is paid by nobody.

---

### ⑤ GET `/bills/{code}/split`
Compute each person's fair share for the current assignments.

- **Response:** `SplitResult`
```json
{
  "per_person": [
    {"person_id": "A", "name": "Andi", "items_subtotal": 98500,
     "discount_share": 0, "tax_share": 9850, "service_share": 0,
     "other_share": 0, "total_owed": 108350},
    {"person_id": "B", "name": "Budi", "items_subtotal": 22500,
     "discount_share": 0, "tax_share": 2250, "service_share": 0,
     "other_share": 0, "total_owed": 24750}
  ],
  "grand_total": 133100
}
```
- **404** if the code doesn't exist.

How the split works (so the UI can explain it):
- Item assigned to N people → split **equally** among them.
- Tax / service / rounding (`charges`) → allocated **proportionally** to each
  person's item subtotal.
- Rounding uses largest-remainder so `sum(total_owed) == grand_total ==
  receipt.total` exactly — no lost or extra Rupiah.

---

## Data models

### Receipt
| field | type | notes |
|-------|------|-------|
| `merchant_name` | string \| null | best-effort, often null |
| `currency` | string | always `"IDR"` |
| `line_items` | `LineItem[]` | the ordered items |
| `bill_discount` | `Discount` \| null | whole-bill discount, if any |
| `charges` | `Charge[]` | tax / service / rounding (any may be absent) |
| `charges_included` | bool | true ⇒ charges already baked into item prices |
| `subtotal` | int | Rupiah |
| `total` | int | Rupiah (the printed total) |
| `reconciled` | bool | did the parsed numbers match `total`? |
| `needs_review` | string[] | field paths the user should confirm |

### LineItem
| field | type | notes |
|-------|------|-------|
| `name` | string | item name |
| `quantity` | int | defaults to 1 |
| `unit_price` | int | Rupiah |
| `line_total` | int | Rupiah — **this is what the split uses** |
| `discount` | `Discount` \| null | per-item discount, if any |
| `confidence` | float | 0–1 |

### Charge
| field | type | notes |
|-------|------|-------|
| `kind` | enum | `"tax_pb1"` \| `"service"` \| `"other"` (rounding uses `other`) |
| `label` | string | raw text, e.g. `"PB1 (10%)"` |
| `type` | enum | `"amount"` \| `"percent"` |
| `value` | int | Rupiah (amount) or whole-number percent; **can be negative** (rounding) |
| `confidence` | float | 0–1 |

### Discount
| field | type | notes |
|-------|------|-------|
| `type` | enum | `"amount"` \| `"percent"` |
| `value` | int | Rupiah or whole-number percent |

### Person
`{ "id": string, "name": string }` — `id` must be unique within a bill.

### Assignment
`{ "line_item_index": int, "person_ids": string[] }`

### PersonShare (inside SplitResult.per_person)
`person_id`, `name`, `items_subtotal`, `discount_share`, `tax_share`,
`service_share`, `other_share`, `total_owed` — all integer Rupiah.

### SplitResult
`{ "per_person": PersonShare[], "grand_total": int }`

---

## Errors
- **404** `{ "detail": "bill not found" }` — unknown bill code (GET/PUT bill routes).
- **422** — validation error (FastAPI standard shape) when a request body doesn't
  match the schema. The `detail` array lists the offending fields.

## Notes for integration
- **CORS is enabled** (all origins, all methods) so the app can call the API from a
  web build, emulator, or device without extra setup. Origins will be tightened to
  known hosts before production.
- There is no auth — bills are reachable by anyone with the `code` (by design for
  the MVP).
