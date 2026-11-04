#!/usr/bin/env python3
"""Setup sekali jalan: buat meters.db (SQLite) dan, opsional, database + tabel MySQL.

Memakai fungsi di server.py supaya skema tidak terduplikasi. Aman dijalankan ulang
(idempotent): koneksi dengan nama sama diperbarui, bukan digandakan.

Pemisahan peran MySQL (disarankan untuk pabrik):
  - user ADMIN (--mysql-user): hanya dipakai di script ini untuk CREATE DATABASE/TABLE & membuat user penulis.
  - user PENULIS (--writer-user): hanya hak INSERT pada power_readings; inilah yang disimpan di meters.db
    dan dipakai server.py saat berjalan. Kalau meters.db bocor, data tidak bisa dibaca/dihapus lewat user ini.

Contoh:
    python setup_db.py --source minipc-gudang1
    python setup_db.py --source minipc-gudang1 --mysql-host 127.0.0.1 --mysql-user root \
        --writer-user ems_writer --writer-from 192.168.1.%
Password: env MYSQL_PASSWORD / MYSQL_WRITER_PASSWORD, atau ditanya (tidak tampil di layar).
"""
import argparse
import getpass
import os
import sys

import mysql.connector

import server


def save_connection(cfg):
    """Insert koneksi, atau update kalau nama sama sudah ada. Return id."""
    fields = server.CONN_FIELDS
    with server.db() as con:
        row = con.execute("SELECT id FROM db_connections WHERE name=?", (cfg["name"],)).fetchone()
        if row:
            con.execute(f"UPDATE db_connections SET {','.join(f + '=?' for f in fields)} WHERE id=?",
                        [cfg[f] for f in fields] + [row["id"]])
            return row["id"]
        cur = con.execute(f"INSERT INTO db_connections ({','.join(fields)}) VALUES ({','.join('?' * len(fields))})",
                          [cfg[f] for f in fields])
        return cur.lastrowid


def ask_password(env_name, prompt):
    value = os.environ.get(env_name)
    return value if value is not None else getpass.getpass(prompt)


def provision_writer(admin_cfg, user, host_from, password):
    """Buat/perbarui user penulis dengan hak INSERT saja pada power_readings."""
    con = server.mysql_connect(admin_cfg)
    try:
        cur = con.cursor()
        cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, host_from, password))
        cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, host_from, password))
        cur.execute(f"GRANT INSERT ON `{admin_cfg['db_name']}`.power_readings TO %s@%s", (user, host_from))
        con.commit()
    finally:
        con.close()


def main():
    p = argparse.ArgumentParser(description="Setup database dashboard")
    p.add_argument("--source", help="nama mini PC pengirim data (default: nama komputer)")
    p.add_argument("--mysql-host")
    p.add_argument("--mysql-port", type=int, default=3306)
    p.add_argument("--mysql-user", help="user ADMIN MySQL (hak CREATE), hanya untuk setup")
    p.add_argument("--mysql-db", default=server.DEFAULT_DB_NAME)
    p.add_argument("--writer-user", help="user penulis (hanya INSERT) yang dipakai server.py; dibuat otomatis")
    p.add_argument("--writer-from", default="%", help="asal koneksi user penulis, mis. 192.168.1.%% (default: %%)")
    p.add_argument("--name", default="Utama", help="nama koneksi di dashboard")
    p.add_argument("--interval", type=float, default=60, help="interval simpan ke MySQL (detik)")
    args = p.parse_args()

    server.init_db()
    if args.source:
        if not server.SOURCE_RE.match(args.source):
            print("[ERROR] --source hanya boleh huruf, angka, titik, minus, underscore (maks. 50).")
            return 1
        server.set_setting("source", args.source)
    print(f"[OK] SQLite siap: {server.DB_PATH}")
    print(f"[OK] Source (nama mini PC): {server.get_setting('source')}")
    print(f"[OK] Token admin dashboard: {server.get_setting('api_token')}   <- simpan; diminta saat edit di UI")

    if not args.mysql_host:
        print("MySQL dilewati (tidak ada --mysql-host). Atur lewat UI dashboard bila perlu.")
        return 0
    if not args.mysql_user:
        print("[ERROR] --mysql-user (admin) wajib diisi bersama --mysql-host.")
        return 1

    admin_pw = ask_password("MYSQL_PASSWORD", f"Password ADMIN MySQL {args.mysql_user}@{args.mysql_host}: ")
    admin, err = server.validate_conn({
        "name": args.name, "host": args.mysql_host, "port": args.mysql_port,
        "db_user": args.mysql_user, "db_password": admin_pw,
        "db_name": args.mysql_db, "store_interval_s": args.interval,
    })
    if err:
        print(f"[ERROR] {err}")
        return 1

    try:
        server.ensure_schema(admin)
        print(f"[OK] Database `{admin['db_name']}` dan tabel power_readings siap di {admin['host']}:{admin['port']}")
        runtime = dict(admin)
        if args.writer_user:
            writer_pw = ask_password("MYSQL_WRITER_PASSWORD", f"Password baru untuk user penulis {args.writer_user}: ")
            if not writer_pw:
                print("[ERROR] Password user penulis tidak boleh kosong.")
                return 1
            provision_writer(admin, args.writer_user, args.writer_from, writer_pw)
            runtime.update(db_user=args.writer_user, db_password=writer_pw)
            server.mysql_connect(runtime).close()  # pastikan login user penulis berhasil
            print(f"[OK] User penulis '{args.writer_user}'@'{args.writer_from}' (hanya INSERT) siap.")
        else:
            print("[PERINGATAN] Tanpa --writer-user, server.py memakai user ADMIN saat berjalan (kurang aman).")
    except mysql.connector.Error as exc:
        print(f"[ERROR] Gagal menyiapkan MySQL: {exc}")
        return 1

    conn_id = save_connection(runtime)
    with server.db() as con:
        con.execute("UPDATE db_connections SET active = (id=?)", (conn_id,))
    print(f"[OK] Koneksi '{runtime['name']}' tersimpan sebagai {runtime['db_user']} dan diaktifkan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
