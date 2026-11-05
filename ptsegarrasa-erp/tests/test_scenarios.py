"""Uji otomatis T-01..T-36 terhadap baseline seed (API level).

Jalankan:  python -m pytest -q tests/
Setiap test memakai database baru (fixture rule: tiap T-xx mulai dari baseline).
Hasil ini membantu mengisi Test Results (expected vs actual); screenshot UI tetap perlu diambil tim.
"""
import os
import tempfile
import pytest
from app import create_app


@pytest.fixture()
def app():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DATABASE_PATH"] = path
    a = create_app()
    a.config["TESTING"] = True
    yield a
    os.remove(path)


def client_as(app, username):
    c = app.test_client()
    r = c.post("/api/login", json={"username": username, "password": username + "123"})
    assert r.status_code == 200, r.get_json()
    return c


def so_id(c, number):
    return next(x["id"] for x in c.get("/api/documents/sales").get_json() if x["number"] == number)


def tb(c):
    return {x["account_code"]: x["balance"] for x in c.get("/api/accounting/trial-balance").get_json()["lines"]}


def new_so(c, customer="C01", sku="BVG-250-LEM", qty=8, disc=0, confirm=True):
    r = c.post("/api/sales-orders", json={"customer_code": customer, "discount_pct": disc, "warehouse_code": "WH-PKU",
                                           "lines": [{"sku": sku, "quantity": qty}]})
    assert r.status_code == 201, r.get_json()
    d = r.get_json()
    if confirm:
        rc = c.post(f"/api/sales-orders/{d['id']}/confirm")
        d["confirm"] = rc
    return d


def deliver(c, oid, sku, qty):
    r = c.post(f"/api/sales-orders/{oid}/delivery", json={"quantities": {sku: qty}})
    assert r.status_code == 201, r.get_json()
    did = r.get_json()["id"]
    v = c.post(f"/api/deliveries/{did}/validate")
    assert v.status_code == 200, v.get_json()
    return did, v.get_json()


def invoice_6_lemon(app):
    """Alur bersama T-15/T-27/T-28/T-31/T-32/T-36: SO + Delivery 6 Lemon + Post invoice."""
    owner = client_as(app, "owner")
    so = new_so(owner, qty=6)
    deliver(owner, so["id"], "BVG-250-LEM", 6)
    inv = owner.post(f"/api/invoices/from-order/{so['id']}").get_json()
    assert owner.post(f"/api/invoices/{inv['id']}/post").status_code == 200
    return owner, so, inv


def stock_row(c, sku, wh):
    return next(x for x in c.get("/api/stock").get_json() if x["sku"] == sku and x["warehouse_code"] == wh)


# ---------------- S-01 App switcher
def test_T01_app_switcher(app):
    owner = client_as(app, "owner")
    assert len(owner.get("/api/me").get_json()["apps"]) == 7
    sales = client_as(app, "sales")
    apps = {a["key"] for a in sales.get("/api/me").get_json()["apps"]}
    assert "accounting" not in apps and "sales" in apps
    assert sales.get("/api/accounting/trial-balance").status_code == 403


def test_T02_sales_denied_journal_detail(app):
    sales = client_as(app, "sales")
    r = sales.get("/api/journals/1")
    assert r.status_code == 403 and "lines" not in r.get_json()
    assert sales.get("/api/doc/journal/1").status_code == 403


# ---------------- S-02 Contact
def test_T03_credit_exposure_C01(app):
    c = client_as(app, "sales")
    d = c.get("/api/contacts/C01/credit").get_json()
    assert (d["credit_limit"], d["opening_ar"], d["confirmed_uninvoiced"]) == (2000000, 400000, 166500)
    assert d["exposure"] == 566500 and d["headroom"] == 1433500


def test_T04_credit_block_C02(app):
    c = client_as(app, "sales")
    so = new_so(c, "C02", "BVG-500-PCH", 30)
    r = so["confirm"]
    assert r.status_code == 409 and "Credit limit" in r.get_json()["error"]
    assert c.get(f"/api/doc/sale/{so['id']}").get_json()["status"] == "Quotation"
    assert c.get("/api/contacts/C02/credit").get_json()["headroom"] == 350000


# ---------------- S-03 Products
def test_T05_fifteen_skus(app):
    c = client_as(app, "sales")
    ps = {p["sku"]: p for p in c.get("/api/masters/products").get_json()}
    assert len(ps) == 15
    assert (ps["BVG-250-LEM"]["sales_price"], ps["BVG-500-LEM"]["sales_price"]) == (15000, 25000)
    assert (ps["BVG-250-LEM"]["standard_cost"], ps["BVG-500-LEM"]["standard_cost"]) == (9000, 15000)


def test_T06_duplicate_sku_rejected(app):
    owner = client_as(app, "owner")
    r = owner.post("/api/masters/products", json={"sku": "BVG-250-LEM", "name": "Sparkling Lemon", "size": "500 ml",
                                                   "unit": "botol", "sales_price": 25000, "standard_cost": 15000})
    assert r.status_code == 409
    p = next(x for x in owner.get("/api/masters/products").get_json() if x["sku"] == "BVG-250-LEM")
    assert p["size"] == "250 ml" and p["sales_price"] == 15000


# ---------------- S-04 CRM
def test_T07_crm_stage_won(app):
    c = client_as(app, "sales")
    leads = c.get("/api/documents/leads").get_json()
    assert len(leads) == 1 and leads[0]["number"] == "CRM00009"
    assert c.post(f"/api/leads/{leads[0]['id']}/stage", json={"stage": "Won"}).status_code == 200
    assert c.get(f"/api/doc/lead/{leads[0]['id']}").get_json()["status"] == "Won"
    owner = client_as(app, "owner")
    assert owner.get("/api/dashboard").get_json()["revenue_posted"] == 0


def test_T08_lead_to_quotation(app):
    c = client_as(app, "sales")
    lead = c.get("/api/documents/leads").get_json()[0]
    q = c.post(f"/api/leads/{lead['id']}/quotation").get_json()
    assert q["number"] == "S00005"
    doc = c.get(f"/api/doc/lead/{lead['id']}").get_json()
    assert any(r["number"] == "S00005" for r in doc["related"])
    sale = c.get(f"/api/doc/sale/{q['id']}").get_json()
    assert any(r["number"] == "CRM00009" for r in sale["related"])
    owner = client_as(app, "owner")
    assert len(owner.get("/api/documents/journals").get_json()) == 1  # hanya opening


# ---------------- S-05 Quotation / SO
def test_T09_quotation_to_so(app):
    c = client_as(app, "sales")
    so = new_so(c, qty=8)
    assert (so["subtotal"], so["tax"], so["total"]) == (120000, 13200, 133200)
    assert so["number"] == "S00005" and so["confirm"].status_code == 200
    assert c.get("/api/contacts/C01/credit").get_json()["exposure"] == 699700


def test_T10_discount_gate_and_lock(app):
    c = client_as(app, "sales")
    ten = new_so(c, qty=8, disc=10, confirm=False)
    assert ten["total"] == 119880
    so = new_so(c, qty=8, disc=20)
    assert so["confirm"].status_code == 409
    assert c.get(f"/api/doc/sale/{so['id']}").get_json()["status"] == "Pending Approval"
    owner = client_as(app, "owner")
    assert owner.post(f"/api/sales-orders/{so['id']}/approve-discount").status_code == 200
    assert c.post(f"/api/sales-orders/{so['id']}/confirm").status_code == 200
    r = c.put(f"/api/sales-orders/{so['id']}", json={"lines": [{"sku": "BVG-250-LEM", "quantity": 8, "unit_price": 10000}]})
    assert r.status_code == 409 and r.get_json()["locked"]
    hist = [h["action"] for h in c.get(f"/api/doc/sale/{so['id']}").get_json()["history"]]
    assert "Approval required" in hist and "Approve discount" in hist and "Blocked edit (locked)" in hist


# ---------------- S-06 Reservation
def test_T11_reservation(app):
    c = client_as(app, "sales")
    s = stock_row(c, "BVG-250-LEM", "WH-PKU")
    assert (s["on_hand"], s["reserved"], s["available"]) == (50, 10, 40)
    new_so(c, qty=8)
    s = stock_row(c, "BVG-250-LEM", "WH-PKU")
    assert (s["on_hand"], s["reserved"], s["available"]) == (50, 18, 32)


def test_T12_oversell_blocked(app):
    c = client_as(app, "sales")
    so = new_so(c, "C05", qty=45)
    assert so["confirm"].status_code == 409
    s = stock_row(c, "BVG-250-LEM", "WH-PKU")
    assert (s["on_hand"], s["reserved"], s["available"]) == (50, 10, 40)


# ---------------- S-07 Delivery
def test_T13_partial_delivery(app):
    owner = client_as(app, "owner")
    so = new_so(owner, qty=8)
    did, v = deliver(owner, so["id"], "BVG-250-LEM", 6)
    assert v["total_cogs"] == 54000
    s = stock_row(owner, "BVG-250-LEM", "WH-PKU")
    assert (s["on_hand"], s["reserved"], s["available"]) == (44, 12, 32)
    line = owner.get(f"/api/doc/sale/{so['id']}").get_json()["lines"]["rows"][0]
    assert (line["delivered_qty"], line["backorder_qty"]) == (6, 2)


def test_T14_invoice_capped(app):
    owner = client_as(app, "owner")
    so = new_so(owner, qty=8)
    deliver(owner, so["id"], "BVG-250-LEM", 6)
    fin = client_as(app, "finance")
    r = fin.post(f"/api/invoices/from-order/{so['id']}", json={"quantities": {"BVG-250-LEM": 8}})
    assert r.status_code == 409 and r.get_json()["invoiceable"] == 6
    inv = fin.post(f"/api/invoices/from-order/{so['id']}").get_json()
    assert inv["net"] == 90000 and inv["total"] == 99900


# ---------------- S-08 Invoice
def test_T15_invoice_posted(app):
    owner, so, inv = invoice_6_lemon(app)
    assert (inv["net"], inv["tax"]) == (90000, 9900)
    b = tb(owner)
    assert b["1100"] == 1149900 and -b["2200"] == 9900 and -b["4100"] == 90000


def test_T16_invoice_without_delivery(app):
    owner = client_as(app, "owner")
    so = new_so(owner, qty=6)
    r = owner.post(f"/api/invoices/from-order/{so['id']}")
    assert r.status_code == 409 and r.get_json()["invoiceable"] == 0
    assert tb(owner)["1100"] == 1050000


# ---------------- S-09 Purchase
def test_T17_po_seed(app):
    c = client_as(app, "procurement")
    po = next(x for x in c.get("/api/documents/purchase").get_json() if x["number"] == "PO00003")
    assert (po["subtotal"], po["tax"], po["total"]) == (180000, 19800, 199800)
    assert sum(x["on_hand"] for x in c.get("/api/stock").get_json()) == 749


def test_T18_po_approval(app):
    c = client_as(app, "procurement")
    po = c.post("/api/purchase-orders", json={"vendor_code": "V01", "lines": [{"sku": "BVG-500-LEM", "quantity": 70}]}).get_json()
    assert po["subtotal"] == 1050000
    r = c.post(f"/api/purchase-orders/{po['id']}/confirm")
    assert r.get_json()["status"] == "Pending Approval"
    assert c.post(f"/api/purchase-orders/{po['id']}/approve").status_code == 403
    owner = client_as(app, "owner")
    assert owner.post(f"/api/purchase-orders/{po['id']}/approve").get_json()["approved_by"] == "owner"
    doc = owner.get(f"/api/doc/purchase/{po['id']}").get_json()
    assert doc["status"] == "Confirmed" and doc["raw"]["approved_at"]


# ---------------- S-10 / S-11 Receipt & Quality
def receive_po3(app, passed=10, failed=2):
    wh = client_as(app, "warehouse")
    po = next(x for x in wh.get("/api/documents/purchase").get_json() if x["number"] == "PO00003")
    rec = wh.post(f"/api/purchase-orders/{po['id']}/receipt", json={"lines": [{"sku": "BVG-500-LEM", "received_qty": 12}]}).get_json()
    q = client_as(app, "quality")
    r = q.post(f"/api/receipts/{rec['id']}/quality-check",
               json={"lines": [{"sku": "BVG-500-LEM", "passed": passed, "failed": failed, "reason": "Segel rusak"}]})
    assert r.status_code == 200, r.get_json()
    return wh, po, rec


def test_T19_receipt_accepted_only(app):
    wh, po, rec = receive_po3(app)
    assert wh.post(f"/api/receipts/{rec['id']}/validate").get_json()["accepted_value"] == 150000
    s = stock_row(wh, "BVG-500-LEM", "WH-PKU")
    assert s["on_hand"] == 40
    assert sum(x["on_hand"] for x in wh.get("/api/stock").get_json()) == 759


def test_T20_over_receipt_blocked(app):
    wh = client_as(app, "warehouse")
    po = next(x for x in wh.get("/api/documents/purchase").get_json() if x["number"] == "PO00003")
    r = wh.post(f"/api/purchase-orders/{po['id']}/receipt", json={"lines": [{"sku": "BVG-500-LEM", "received_qty": 13}]})
    assert r.status_code == 409 and "revisi PO" in r.get_json()["error"]


def test_T21_quality_record(app):
    wh, po, rec = receive_po3(app)
    wh.post(f"/api/receipts/{rec['id']}/validate")
    doc = wh.get(f"/api/doc/receipt/{rec['id']}").get_json()
    t = dict(doc["totals"])
    assert (t["Inspected"], t["Accepted"], t["Rejected"]) == (12, 10, 2)
    assert t["Accepted value (masuk stock)"] == 150000 and t["Rejected value potensial (tidak masuk stock)"] == 30000
    assert doc["raw"]["qc_number"] == "QC00001" and doc["raw"]["qc_by"] == "quality"


def test_T22_cannot_override_failed(app):
    wh, po, rec = receive_po3(app)
    proc = client_as(app, "procurement")
    r = proc.patch(f"/api/receipts/{rec['id']}/lines", json={"lines": [{"sku": "BVG-500-LEM", "accepted_qty": 12}]})
    assert r.status_code == 409
    doc = wh.get(f"/api/doc/receipt/{rec['id']}").get_json()
    assert doc["lines"]["rows"][0]["accepted"] == 10
    assert any(h["action"] == "Blocked accepted override" for h in doc["history"])


# ---------------- S-12 Vendor bill
def test_T23_vendor_bill(app):
    wh, po, rec = receive_po3(app)
    wh.post(f"/api/receipts/{rec['id']}/validate")
    fin = client_as(app, "finance")
    b = fin.post(f"/api/vendor-bills/from-receipt/{rec['id']}").get_json()
    assert (b["net"], b["tax"], b["total"]) == (150000, 16500, 166500)
    assert fin.post(f"/api/vendor-bills/{b['id']}/post").status_code == 200
    bal = tb(fin)
    assert -bal["2100"] == 386500 and bal["1210"] == 0


def test_T24_bill_mismatch(app):
    wh, po, rec = receive_po3(app)
    wh.post(f"/api/receipts/{rec['id']}/validate")
    fin = client_as(app, "finance")
    b = fin.post(f"/api/vendor-bills/from-receipt/{rec['id']}", json={"claimed": {"BVG-500-LEM": 12}}).get_json()
    assert b["match_status"] == "Mismatch" and b["net"] == 150000
    assert fin.post(f"/api/vendor-bills/{b['id']}/post").status_code == 409
    assert -tb(fin)["2100"] == 220000


# ---------------- S-13 Journal
def test_T25_cogs_journal(app):
    owner = client_as(app, "owner")
    so = new_so(owner, qty=6)
    did, v = deliver(owner, so["id"], "BVG-250-LEM", 6)
    j = next(x for x in owner.get("/api/documents/journals").get_json() if x["number"] == v["journal_number"])
    assert j["debit"] == j["credit"] == 54000 and j["reference"].startswith("WH/OUT/")


def test_T26_delivery_idempotent(app):
    owner = client_as(app, "owner")
    so = new_so(owner, qty=6)
    did, v = deliver(owner, so["id"], "BVG-250-LEM", 6)
    r = owner.post(f"/api/deliveries/{did}/validate").get_json()
    assert r["idempotent"]
    assert stock_row(owner, "BVG-250-LEM", "WH-PKU")["on_hand"] == 44
    assert len([j for j in owner.get("/api/documents/journals").get_json() if j["source_type"] == "Delivery"]) == 1
    assert len([m for m in owner.get("/api/documents/moves").get_json() if m["move_type"] == "Delivery"]) == 1


# ---------------- S-14 Payment
def test_T27_partial_payment(app):
    owner, so, inv = invoice_6_lemon(app)
    fin = client_as(app, "finance")
    p = fin.post("/api/payments", json={"document_type": "customer", "document_id": inv["id"], "amount": 40000}).get_json()
    assert p["residual"] == 59900 and p["status"] == "Partial"
    b = tb(fin)
    assert b["1100"] == 1109900 and b["1010"] == 7040000


def test_T28_invalid_payment(app):
    owner, so, inv = invoice_6_lemon(app)
    fin = client_as(app, "finance")
    for amount in (-10000, 110000, 0):
        r = fin.post("/api/payments", json={"document_type": "customer", "document_id": inv["id"], "amount": amount})
        assert r.status_code == 409
    assert tb(fin)["1010"] == 7000000


# ---------------- S-15 Transfer
def test_T29_transfer(app):
    wh = client_as(app, "warehouse")
    t = wh.post("/api/transfers", json={"sku": "TEA-350-ORI", "quantity": 5, "from_warehouse": "WH-PKU",
                                         "to_warehouse": "OUT-MRP"}).get_json()
    before = wh.get("/api/dashboard").get_json()["stock_value"]
    assert wh.post(f"/api/transfers/{t['id']}/validate").status_code == 200
    assert stock_row(wh, "TEA-350-ORI", "WH-PKU")["on_hand"] == 55
    assert stock_row(wh, "TEA-350-ORI", "OUT-MRP")["on_hand"] == 23
    assert wh.get("/api/dashboard").get_json()["stock_value"] == before == 6314000


def test_T30_transfer_idempotent(app):
    wh = client_as(app, "warehouse")
    t = wh.post("/api/transfers", json={"sku": "TEA-350-ORI", "quantity": 5, "from_warehouse": "WH-PKU",
                                         "to_warehouse": "OUT-MRP"}).get_json()
    wh.post(f"/api/transfers/{t['id']}/validate")
    assert wh.post(f"/api/transfers/{t['id']}/validate").get_json()["idempotent"]
    assert stock_row(wh, "TEA-350-ORI", "WH-PKU")["on_hand"] == 55


# ---------------- S-16 Return
def test_T31_return_credit_note(app):
    owner, so, inv = invoice_6_lemon(app)
    r = owner.post("/api/returns", json={"invoice_id": inv["id"], "sku": "BVG-250-LEM", "quantity": 2,
                                          "inspection": "Saleable"}).get_json()
    assert (r["credit_net"], r["credit_tax"], r["credit_total"], r["cogs_reversal"]) == (30000, 3300, 33300, 18000)
    v = owner.post(f"/api/returns/{r['id']}/validate").get_json()
    assert v["credit_note_number"].startswith("RINV/")
    assert stock_row(owner, "BVG-250-LEM", "WH-PKU")["on_hand"] == 46
    b = tb(owner)
    assert b["5100"] == 54000 - 18000 and b["1100"] == 1050000 + 99900 - 33300


def test_T32_return_cap_and_idempotent(app):
    owner, so, inv = invoice_6_lemon(app)
    r = owner.post("/api/returns", json={"invoice_id": inv["id"], "sku": "BVG-250-LEM", "quantity": 7})
    assert r.status_code == 409 and r.get_json()["cap"] == 6
    ok = owner.post("/api/returns", json={"invoice_id": inv["id"], "sku": "BVG-250-LEM", "quantity": 2}).get_json()
    owner.post(f"/api/returns/{ok['id']}/validate")
    again = owner.post(f"/api/returns/{ok['id']}/validate").get_json()
    assert again["idempotent"]
    assert len([j for j in owner.get("/api/documents/journals").get_json() if j["source_type"] == "Credit Note"]) == 1


# ---------------- S-17 Dashboard
def test_T33_dashboard_baseline(app):
    owner = client_as(app, "owner")
    d = owner.get("/api/dashboard").get_json()
    assert (d["on_hand"], d["reserved"], d["available"], d["stock_value"]) == (749, 10, 739, 6314000)
    assert (d["open_sales_orders"], d["open_purchase_orders"], d["open_leads"]) == (1, 1, 1)
    assert d["inventory_ledger"] == 6314000


def test_T34_revenue_excludes_so(app):
    owner = client_as(app, "owner")
    d = owner.get("/api/dashboard").get_json()
    assert d["revenue_posted"] == 0 and d["uninvoiced_confirmed"] == 166500


# ---------------- S-18 Audit
def test_T35_seed_audit(app):
    owner = client_as(app, "owner")
    doc = owner.get(f"/api/doc/sale/{so_id(owner, 'S00004')}").get_json()
    h = doc["history"][0]
    assert h["actor"] == "seed" and h["action"] == "Confirmed + reserve" and h["created_at"].startswith("2026-10-01 02:00")
    assert doc["reservations"][0]["quantity"] == 10


def test_T36_invoice_post_idempotent(app):
    owner, so, inv = invoice_6_lemon(app)
    r = owner.post(f"/api/invoices/{inv['id']}/post").get_json()
    assert r["idempotent"]
    js = [j for j in owner.get("/api/documents/journals").get_json() if j["source_type"] == "Customer Invoice"]
    assert len(js) == 1 and tb(owner)["1100"] == 1149900
    hist = owner.get(f"/api/doc/invoice/{inv['id']}").get_json()["history"]
    assert len([h for h in hist if h["action"] == "Post Customer Invoice"]) == 1


def test_trial_balance_always_balanced(app):
    owner, so, inv = invoice_6_lemon(app)
    t = owner.get("/api/accounting/trial-balance").get_json()
    assert t["difference"] == 0 and t["total_debit"] > 14364000
