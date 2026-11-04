# SOSRO Dashboard

Dashboard listrik realtime (Modbus TCP, 3 fasa). Backend `server.py` (Python stdlib + pymodbus),
frontend statis di `static/`. Konfigurasi meter & koneksi MySQL disimpan di `meters.db` (SQLite, dibuat otomatis, tidak di-commit).

## Jalankan
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python server.py --host 0.0.0.0 --port 8080
```
Buka `http://localhost:8080`. Lalu tambahkan perangkat & koneksi database lewat UI.

## Update di mini PC
```
git pull
```
Restart `server.py`.
