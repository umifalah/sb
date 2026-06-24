from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.models import (
    Assignment,
    Bill,
    CreateBillResponse,
    OkResponse,
    Person,
    Receipt,
    SplitResult,
)
from app.split_engine import split

router = APIRouter(tags=["Bills"])


class CreateBillRequest(BaseModel):
    receipt: Receipt
    people: list[Person] = []

    model_config = {
        "json_schema_extra": {
            "example": {
                "receipt": {
                    "line_items": [
                        {"name": "Beef Pho", "unit_price": 76000, "line_total": 76000},
                        {"name": "Sweet Ice Tea", "unit_price": 45000, "line_total": 45000},
                    ],
                    "charges": [
                        {"kind": "tax_pb1", "label": "PB1", "type": "amount", "value": 12100}
                    ],
                    "subtotal": 121000,
                    "total": 133100,
                },
                "people": [
                    {"id": "A", "name": "Andi"},
                    {"id": "B", "name": "Budi"},
                ],
            }
        }
    }


class AssignmentsRequest(BaseModel):
    assignments: list[Assignment]

    model_config = {
        "json_schema_extra": {
            "example": {
                "assignments": [
                    {"line_item_index": 0, "person_ids": ["A"]},
                    {"line_item_index": 1, "person_ids": ["A", "B"]},
                ]
            }
        }
    }


def _store(request: Request):
    return request.app.state.store


@router.post(
    "/bills",
    response_model=CreateBillResponse,
    summary="Create a bill",
    description=(
        "Create a saved bill from a (possibly user-corrected) receipt plus the "
        "list of people. Returns a short shareable `code`. Each person needs a "
        "unique `id` (used to reference them in assignments) and a display `name`."
    ),
)
async def create_bill(request: Request, body: CreateBillRequest):
    bill = Bill(receipt=body.receipt, people=body.people)
    code = _store(request).save(bill)
    return {"code": code}


@router.get(
    "/bills/{code}",
    response_model=Bill,
    summary="Fetch a saved bill",
    description="Return the full saved bill (receipt, people, assignments) by code.",
    responses={404: {"description": "Bill not found"}},
)
async def get_bill(request: Request, code: str):
    bill = _store(request).load(code)
    if bill is None:
        raise HTTPException(status_code=404, detail="bill not found")
    return bill


@router.put(
    "/bills/{code}/assignments",
    response_model=OkResponse,
    summary="Set who ate what",
    description=(
        "Replace the bill's assignments. Each assignment maps one line item "
        "(`line_item_index`, 0-based into `receipt.line_items`) to the people who "
        "shared it (`person_ids`). More than one id => the item is split equally "
        "among them. Call this whenever the user changes the selection, then GET "
        "the split."
    ),
    responses={404: {"description": "Bill not found"}},
)
async def set_assignments(request: Request, code: str, body: AssignmentsRequest):
    store = _store(request)
    bill = store.load(code)
    if bill is None:
        raise HTTPException(status_code=404, detail="bill not found")
    bill.assignments = body.assignments
    store.save(bill)
    return {"ok": True}


@router.get(
    "/bills/{code}/split",
    response_model=SplitResult,
    summary="Compute the split",
    description=(
        "Compute each person's fair share for the current assignments. Charges "
        "(tax/service/rounding) are allocated proportionally to what each person "
        "ate; shared items split equally; rounding is largest-remainder so the "
        "per-person totals sum to exactly `receipt.total` (`grand_total`)."
    ),
    responses={404: {"description": "Bill not found"}},
)
async def get_split(request: Request, code: str):
    bill = _store(request).load(code)
    if bill is None:
        raise HTTPException(status_code=404, detail="bill not found")
    return split(bill.receipt, bill.people, bill.assignments)
