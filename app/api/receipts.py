import cv2
import numpy as np
from fastapi import APIRouter, File, Request, UploadFile

from app.models import ScanResponse
from app.parser import parse_receipt
from app.quality_gate import check_quality
from app.reconciler import reconcile

router = APIRouter(tags=["Receipts"])


@router.post(
    "/receipts/scan",
    response_model=ScanResponse,
    summary="Scan a receipt image",
    description=(
        "Upload a receipt photo (multipart form field `file`). Runs the full "
        "pipeline: quality gate -> OCR -> parse -> reconcile.\n\n"
        "- If the image is too blurry/low-res, returns `needs_rescan: true` with a "
        "`reason` and a null `receipt` -> ask the user to retake the photo.\n"
        "- Otherwise returns the structured `receipt`. Check `receipt.reconciled`: "
        "if false, the parsed numbers don't add up to the printed total, and "
        "`needs_review` lists the suspect fields for the user to confirm/fix.\n\n"
        "The returned `receipt` is what you send to `POST /bills` (after any "
        "user corrections)."
    ),
)
async def scan(request: Request, file: UploadFile = File(...)):
    data = await file.read()
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"receipt": None, "needs_rescan": True, "needs_review": [],
                "reason": "could not decode image"}

    ok, reason = check_quality(img)
    if not ok:
        return {"receipt": None, "needs_rescan": True, "needs_review": [],
                "reason": reason}

    ocr = request.app.state.ocr
    receipt = reconcile(parse_receipt(ocr.extract_text(img)))
    return {
        "receipt": receipt.model_dump(),
        "needs_rescan": False,
        "needs_review": receipt.needs_review,
        "reason": None,
    }
