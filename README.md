# SOSRO Dashboard

Dashboard listrik realtime (Modbus TCP, 3 fasa). Backend `server.py` (Python stdlib + pymodbus),
frontend statis di `static/`. Konfigurasi meter & koneksi MySQL disimpan di `meters.db` (SQLite, dibuat otomatis, tidak di-commit).

## Setup awal (sekali)
```
setup.bat --mysql-host 127.0.0.1 --mysql-user root
```
Membuat venv, memasang dependensi, membuat `meters.db`, serta database + tabel MySQL
(`power_readings`) dan mengaktifkannya sebagai koneksi penyimpanan. Tanpa argumen `--mysql-*`,
hanya SQLite yang dibuat. Password ditanya saat setup (atau env `MYSQL_PASSWORD`).
Pindah ke server pabrik: jalankan ulang dengan `--mysql-host <ip-server>`
(nama koneksi sama = diperbarui, tidak digandakan).

## Jalankan
```
.venv\Scripts\python server.py --host 0.0.0.0 --port 8080
```
Buka `http://localhost:8080`. Lalu tambahkan perangkat & koneksi database lewat UI.

## Update di mini PC
```
git pull
```
Restart `server.py`.
