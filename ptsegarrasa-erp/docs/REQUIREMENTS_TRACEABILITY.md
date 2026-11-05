# Traceability — Handout PT Segar Rasa Riau → Implementasi

Kolom "Uji otomatis" mengacu ke `tests/test_scenarios.py` (API). Bukti UI/screenshot tetap wajib dikumpulkan tim.

| SR | Fitur / Layar | Implementasi (file → fungsi) | Uji otomatis |
|---|---|---|---|
| SR-01, SR-02 | F-08, F-09 / S-07, S-08 | routes.py → `invoice_from_order` (cap delivered − sudah ditagih), `post_invoice` | T-14, T-15, T-16 |
| SR-03, SR-04 | F-07 / S-06 | routes.py → `confirm_so` (Available = On Hand − Reserved), `validate_delivery` (tidak memakai reservasi lain, tidak negatif) | T-11, T-12, T-13 |
| SR-05, SR-06 | F-14 / S-12 | routes.py → `bill_from_receipt` (billed ≤ accepted, Mismatch), `post_bill` (ditahan), `resolve_bill` | T-23, T-24 |
| SR-06, SR-11 | F-12, F-13 / S-10, S-11 | routes.py → `create_receipt` (≤ PO), `quality_check`, `validate_receipt` (accepted only + GRNI), `edit_receipt_lines` (override ditolak) | T-19–T-22 |
| SR-07, SR-08 | F-06 / S-05 | routes.py → `effective_discount`, `confirm_so` (Pending Approval), `approve_discount`, `update_so` (locked), `revise_so` | T-10 |
| SR-09 | F-02, F-10 / S-02, S-06 | routes.py → `credit_info`, `contact_credit`, `confirm_so` | T-03, T-04, T-09 |
| SR-10 | F-16 / S-14 | routes.py → `payment`, `pay_status` | T-27, T-28 |
| SR-12 | F-17 / S-15 | routes.py → `transfer`, `validate_transfer` | T-29, T-30 |
| SR-13 | F-18 / S-16 | routes.py → `return_cap`, `create_return`, `validate_return` (stok, COGS reversal, Credit Note, AR) | T-31, T-32 |
| SR-14 | F-21 / S-08 | db.py → `tax_of`, `half_up` (12% × 11/12, half-up per line); routes.py → `tax_settings` | T-09, T-15 |
| SR-15 | F-15, F-20 / S-13, S-18 | db.py → `next_number`, `post_journal` (debit = kredit), `audit`; routes.py → `tx` (BEGIN IMMEDIATE), update bersyarat status | T-25, T-26, T-35, T-36 |
| SR-16 | F-01 / S-01 | auth.py → `PERMISSIONS`, `require`, `deny` (audit akses ditolak); routes.py → `/me` | T-01, T-02 |
| SR-17 | F-03 / S-03 | routes.py → `create_product` (SKU unik); db.py seed 15 SKU | T-05, T-06 |
| SR-18 | F-04, F-05 / S-04, S-05 | routes.py → `create_lead`, `lead_stage` (+ history), `lead_to_quotation` | T-07, T-08 |
| SR-19 | F-19 / S-17 | routes.py → `dashboard` (dari ledger & dokumen, drill-through di UI) | T-33, T-34 |
| SR-20 | F-11, F-22 / S-09 | routes.py → `create_po`, `confirm_po` (Pending Approval), `approve_po` (actor + waktu) | T-17, T-18 |

## Pilihan keputusan (bahan Decision Log — tim wajib melengkapi trade-off & mitigasi)
- SR-01 vs SR-02: invoice dibatasi delivered quantity (SR-02 dipilih). Sales dirugikan karena tidak bisa menagih di muka; mitigasi: partial delivery lalu partial invoice.
- SR-03 vs SR-04: oversell ditolak; Available tidak pernah negatif. Mitigasi: backorder/PO baru.
- SR-05 vs SR-06: bill hanya atas accepted qty; selisih Mismatch dan posting ditahan sampai Finance menandai selisih.
- SR-07 vs SR-08: diskon sampai 20% boleh, tetapi > 10% wajib approval Owner; setelah Confirmed harga terkunci dan perubahan lewat Revisi (cancel + copy).
- Return Damaged: stok tidak kembali, biaya dipindah dari COGS ke 6100 Quality Loss.
