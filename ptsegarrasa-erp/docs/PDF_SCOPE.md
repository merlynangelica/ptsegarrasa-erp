# Source basis and interpretation

This project follows the supplied 36-page handout: *Student Scenario Handout — PT Segar Rasa Riau*.

Source-derived facts encoded in the seed/config:
- PT Segar Rasa Riau / SegarRasa, Pekanbaru, IDR.
- Warehouses WH-PKU and OUT-MRP.
- Six application roles: Sales, Warehouse, Procurement, Finance, Quality, Owner.
- Six customers, four vendors, fifteen product variant SKUs.
- Baseline stock 749 units (WH-PKU 596, OUT-MRP 153), baseline reserved 10 units on S00004, available 739.
- Opening balances: AR Rp1.050.000, AP Rp220.000, Bank Rp7.000.000, Inventory Rp6.314.000; opening journal balances.
- Effective simulated VAT 11% = nominal 12% × 11/12, rounded half-up per invoice line.
- SR-01–SR-20, F-01–F-22, screens S-01–S-18 and tests T-01–T-36 are the assignment's requirement set.

Implementation choices added by this starter (not mandated technology choices in the PDF):
- Flask + SQLite + Vanilla JS.
- API route naming, database normalization, session handling, initial permission map, local development server.
- PO approval setting is seeded at Rp1.000.000 subtotal to make the Rp1.050.000 fixture enter Pending Approval.

Important: Some required behavior is represented as API foundations and still needs completion/integration. The handout explicitly says teams may choose technology, database and hosting. This starter is not a production deployment and is not certified as passing T-01–T-36.
