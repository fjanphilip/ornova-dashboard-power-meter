# SOSRO Dashboard

Dashboard listrik realtime (Modbus TCP, 3 fasa). Backend `server.py` (Python stdlib + pymodbus),
frontend statis di `static/`. Konfigurasi meter & koneksi MySQL disimpan di `meters.db` (SQLite, dibuat otomatis, tidak di-commit).

```
Meter --Modbus--> server.py --> outbox (SQLite, buffer lokal) --> MySQL server pabrik
                      \--> browser (/api/devices)
```

## Setup awal (sekali per mini PC)

Di MySQL, siapkan satu user ADMIN (hak CREATE) — hanya dipakai saat setup. Lalu:
```
setup.bat --source minipc-gudang1 --mysql-host 127.0.0.1 --mysql-user root --writer-user ems_writer --writer-from 192.168.1.%
```
- `--source` : nama unik mini PC ini. Disimpan di tiap baris data, jadi beberapa mini PC bisa menulis ke satu MySQL tanpa bentrok.
- `--mysql-user` : admin MySQL. Membuat database `ois_sosro_pw` + tabel `power_readings`.
- `--writer-user` : user khusus **INSERT saja** yang dibuat otomatis dan dipakai `server.py` saat berjalan. `--writer-from` membatasi asal koneksinya (pakai subnet, jangan `%`).
- Password ditanya saat setup (atau env `MYSQL_PASSWORD`, `MYSQL_WRITER_PASSWORD`).
- Setup menampilkan **token admin**. Simpan; diminta di browser saat menambah/mengubah meter atau membuka halaman koneksi database.

Tanpa argumen `--mysql-*`, hanya SQLite yang dibuat. Menjalankan ulang setup aman (nama koneksi sama = diperbarui).
Pindah ke server pabrik: jalankan ulang dengan `--mysql-host <ip-server>`; tabel di server baru dibuat otomatis.

## Jalankan
```
.venv\Scripts\python server.py --host 0.0.0.0 --port 8080
```
Buka `http://<ip-mini-pc>:8080`. Tampilan monitoring terbuka; operasi tulis butuh token.

## Hal operasional di pabrik
- **Firewall**: batasi port 8080 hanya untuk IP yang perlu.
  `netsh advfirewall firewall add rule name="SOSRO Dashboard" dir=in action=allow protocol=TCP localport=8080 remoteip=192.168.1.50,192.168.1.51`
- **File `meters.db`** berisi password MySQL & token. Batasi akses file ke akun layanan; jangan taruh di folder share/backup umum.
- **Data tertahan**: kalau MySQL/jaringan putus, data menumpuk di outbox (maks. 500.000 baris) dan terkirim otomatis saat pulih. Jumlahnya tampil di halaman Database.
- **Log**: `dashboard.log` (berputar) mencatat error dan perubahan status pengiriman.
- **Jam**: aktifkan sinkronisasi waktu (NTP) dan samakan zona waktu dengan server pabrik.
- **Auto-start**: Task Scheduler, trigger "At startup", "Run whether user is logged on or not", restart saat gagal.

## Update di mini PC
```
git pull
```
Restart `server.py`. Tabel MySQL lama dimigrasi otomatis (kolom `source`) bila server.py diaktifkan dengan user yang punya hak ALTER; jika tidak, jalankan ulang `setup.bat` dengan user admin.
