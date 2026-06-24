import cv2
import numpy as np
from fastapi.testclient import TestClient

from app.main import create_app
from app.ocr.base import TextBox


class FakeOCR:
    def extract_text(self, image):
        return [
            TextBox("Nasi Goreng", 0.99, [[10, 10], [150, 10], [150, 30], [10, 30]]),
            TextBox("45.000", 0.98, [[300, 10], [370, 10], [370, 30], [300, 30]]),
            TextBox("Total", 0.99, [[10, 60], [80, 60], [80, 80], [10, 80]]),
            TextBox("45.000", 0.98, [[300, 60], [370, 60], [370, 80], [300, 80]]),
        ]


def client(tmp_path):
    return TestClient(create_app(ocr=FakeOCR(), db_path=tmp_path / "t.db"))


def _sharp_png_bytes():
    img = np.zeros((800, 600, 3), np.uint8)
    img[::2] = 255  # high-frequency stripes => passes the quality gate
    ok, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def test_scan_returns_receipt(tmp_path):
    c = client(tmp_path)
    r = c.post(
        "/receipts/scan",
        files={"file": ("r.png", _sharp_png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["receipt"]["line_items"][0]["name"].lower().startswith("nasi")
    assert body["needs_rescan"] is False


def test_scan_rejects_blurry_image(tmp_path):
    c = client(tmp_path)
    flat = np.full((800, 600, 3), 127, np.uint8)
    ok, buf = cv2.imencode(".png", flat)
    r = c.post(
        "/receipts/scan",
        files={"file": ("r.png", buf.tobytes(), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["needs_rescan"] is True


def test_create_and_split_bill(tmp_path):
    c = client(tmp_path)
    receipt = {
        "line_items": [
            {"name": "A", "unit_price": 30000, "line_total": 30000},
            {"name": "B", "unit_price": 70000, "line_total": 70000},
        ],
        "subtotal": 100000,
        "total": 100000,
    }
    create = c.post(
        "/bills",
        json={
            "receipt": receipt,
            "people": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        },
    )
    assert create.status_code == 200
    code = create.json()["code"]

    put = c.put(
        f"/bills/{code}/assignments",
        json={
            "assignments": [
                {"line_item_index": 0, "person_ids": ["a"]},
                {"line_item_index": 1, "person_ids": ["b"]},
            ]
        },
    )
    assert put.status_code == 200

    split = c.get(f"/bills/{code}/split").json()
    owed = {p["person_id"]: p["total_owed"] for p in split["per_person"]}
    assert owed == {"a": 30000, "b": 70000}
    assert split["grand_total"] == 100000


def test_get_missing_bill_404(tmp_path):
    c = client(tmp_path)
    assert c.get("/bills/NOPE12").status_code == 404
