#!/usr/bin/env python3
"""Setup sekali jalan: buat meters.db (SQLite) dan, opsional, database + tabel MySQL.

Memakai fungsi di server.py supaya skema tidak terduplikasi. Aman dijalankan ulang
(idempotent): koneksi dengan nama sama diperbarui, bukan digandakan.

Contoh:
    python setup_db.py                                   # hanya SQLite
    python setup_db.py --mysql-host 127.0.0.1 --mysql-user root
    python setup_db.py --mysql-host 10.0.0.5 --mysql-user ems  # nanti, server pabrik
Password MySQL: env MYSQL_PASSWORD, atau ditanya (tidak tampil di layar).
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


def main():
    p = argparse.ArgumentParser(description="Setup database dashboard")
    p.add_argument("--mysql-host")
    p.add_argument("--mysql-port", type=int, default=3306)
    p.add_argument("--mysql-user")
    p.add_argument("--mysql-db", default=server.DEFAULT_DB_NAME)
    p.add_argument("--name", default="Utama", help="nama koneksi di dashboard")
    p.add_argument("--interval", type=float, default=60, help="interval simpan ke MySQL (detik)")
    args = p.parse_args()

    server.init_db()
    print(f"[OK] SQLite siap: {server.DB_PATH}")

    if not args.mysql_host:
        print("MySQL dilewati (tidak ada --mysql-host). Atur lewat UI dashboard bila perlu.")
        return 0
    if not args.mysql_user:
        print("[ERROR] --mysql-user wajib diisi bersama --mysql-host.")
        return 1

    password = os.environ.get("MYSQL_PASSWORD")
    if password is None:
        password = getpass.getpass(f"Password MySQL untuk {args.mysql_user}@{args.mysql_host}: ")

    cfg, err = server.validate_conn({
        "name": args.name, "host": args.mysql_host, "port": args.mysql_port,
        "db_user": args.mysql_user, "db_password": password,
        "db_name": args.mysql_db, "store_interval_s": args.interval,
    })
    if err:
        print(f"[ERROR] {err}")
        return 1

    try:
        server.ensure_schema(cfg)
    except mysql.connector.Error as exc:
        print(f"[ERROR] Gagal menyiapkan MySQL: {exc}")
        return 1
    print(f"[OK] Database `{cfg['db_name']}` dan tabel power_readings siap di {cfg['host']}:{cfg['port']}")

    conn_id = save_connection(cfg)
    with server.db() as con:
        con.execute("UPDATE db_connections SET active = (id=?)", (conn_id,))
    print(f"[OK] Koneksi '{cfg['name']}' tersimpan dan diaktifkan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
