from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api import bills, receipts
from app.store import BillStore

_DEBUG_PAGE = Path(__file__).parent / "debug" / "page.html"


class _LazyPaddle:
    """Defers importing/constructing PaddleOCR until the first real scan,
    so importing app.main (e.g. in tests) never loads paddlepaddle."""

    def __init__(self) -> None:
        self._engine = None

    def extract_text(self, image: np.ndarray):
        if self._engine is None:
            from app.ocr.paddle_engine import PaddleEngine

            self._engine = PaddleEngine()
        return self._engine.extract_text(image)


API_DESCRIPTION = """
Backend for the Split Bill app. Scan an Indonesian receipt, extract structured
line items + charges (no LLM), then split the bill fairly among friends.

**Typical flow**
1. `POST /receipts/scan` — upload the photo, get a structured `receipt`
   (check `needs_rescan` and `receipt.reconciled`; let the user fix flagged fields).
2. `POST /bills` — send the receipt + people, get a shareable `code`.
3. `PUT /bills/{code}/assignments` — record who ate what.
4. `GET /bills/{code}/split` — get each person's amount to pay.

All money is whole-Rupiah integers (no decimals).
""".strip()


def create_app(ocr=None, db_path: str | Path = "bills.db") -> FastAPI:
    app = FastAPI(
        title="Split Bill API",
        version="1.0.0",
        description=API_DESCRIPTION,
        openapi_tags=[
            {"name": "Receipts", "description": "Scan and extract receipt data."},
            {"name": "Bills", "description": "Create bills, assign items, compute the split."},
        ],
    )
    # Open CORS for the MVP so the Flutter app (any origin/device) can call the
    # API. Tighten allow_origins to known hosts before production.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.ocr = ocr if ocr is not None else _LazyPaddle()
    app.state.store = BillStore(db_path)
    app.include_router(receipts.router)
    app.include_router(bills.router)

    @app.get("/")
    async def debug_page():
        # no-store so edits to the debug tool always show on a normal refresh
        return FileResponse(_DEBUG_PAGE, headers={"Cache-Control": "no-store"})

    return app


app = create_app()
