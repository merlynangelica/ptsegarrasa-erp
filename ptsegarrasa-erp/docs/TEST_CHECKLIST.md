# Test execution checklist

1. Jalankan `python -m pytest -q tests/` — 37 skenario API (T-01..T-36 + trial balance) harus PASS.
2. Untuk setiap T-xx lakukan juga di UI dari baseline baru (Owner → Home → Reset baseline seed), lalu catat: expected, actual, PASS/FAIL, path screenshot, defect ID, tester.
3. Tombol "↻ Kirim ulang (uji idempotensi)" pada Delivery/Invoice/Transfer/Return dan tombol "Ubah accepted qty" pada Receipt disediakan khusus untuk bukti T-22, T-26, T-30, T-32, T-36.
4. T-02: login sebagai sales lalu buka `http://127.0.0.1:5000/#doc/journal/1` langsung di address bar → halaman 403 dan event "Access denied" muncul di Audit Log.
