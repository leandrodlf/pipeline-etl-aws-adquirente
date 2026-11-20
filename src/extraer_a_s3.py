#!/usr/bin/env python3
"""Extrae las tablas de `adquirente_dw` (PostgreSQL) y las sube a S3 en formato Parquet.

Capa "raw" del data lake:
    s3://<bucket>/raw/<tabla>/<tabla>.parquet
    s3://<bucket>/raw/transacciones/anio=AAAA/mes=MM/transacciones.parquet   (particionada)
    s3://<bucket>/meta/manifiestos/extraccion_<fecha>.json                   (conteos y trazabilidad)

Antes de terminar compara, tabla por tabla, las filas leídas en Postgres con las filas
escritas en Parquet; si no coinciden, falla.

Conexión a Postgres: variables de entorno PGHOST, PGPORT, PGUSER, PGPASSWORD.
Credenciales de AWS: las de tu sesión (`aws login`).

Uso:
    python src/extraer_a_s3.py --bucket leandrodlf-adquirente-datalake
    python src/extraer_a_s3.py --local data      # prueba sin AWS: escribe en la carpeta ./data
"""
import argparse
import io
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

TABLAS = ["rubros", "bancos_emisores", "modelos_terminal", "codigos_respuesta", "comercios",
          "sucursales", "terminales", "tarjetas", "liquidaciones", "lotes_cierre",
          "transacciones", "contracargos"]
PARTICIONADAS = {"transacciones": "fecha_hora"}  # tabla -> columna de fecha

# Tipo de Postgres (OID) -> tipo de Arrow. Se fija explícito para que todos los archivos
# tengan exactamente el mismo esquema (Athena/Glue fallan si cambia entre particiones).
OID_A_ARROW = {
    16: pa.bool_(), 20: pa.int64(), 21: pa.int16(), 23: pa.int32(), 25: pa.string(),
    1042: pa.string(), 1043: pa.string(), 1082: pa.date32(), 1114: pa.timestamp("us"),
    1700: pa.decimal128(14, 2),
}


def leer_tabla(cur, nombre):
    """Lee una tabla completa de Postgres como tabla de Arrow con esquema explícito."""
    cur.execute(f"SELECT * FROM {nombre} ORDER BY id")
    columnas = [d for d in cur.description]
    filas = cur.fetchall()
    campos = []
    for d in columnas:
        if d.type_code not in OID_A_ARROW:
            raise SystemExit(f"Tipo de Postgres no soportado en {nombre}.{d.name} (oid {d.type_code})")
        campos.append(pa.field(d.name, OID_A_ARROW[d.type_code]))
    esquema = pa.schema(campos)
    datos = list(zip(*filas)) if filas else [()] * len(campos)
    arrays = [pa.array(list(col), type=campo.type) for col, campo in zip(datos, esquema)]
    return pa.Table.from_arrays(arrays, schema=esquema)


def a_parquet(tabla: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(tabla, buf, compression="snappy")
    return buf.getvalue()


def particiones(nombre, tabla):
    """Devuelve [(clave_relativa, subtabla)]: una sola para tablas chicas, por año/mes para transacciones."""
    if nombre not in PARTICIONADAS:
        return [(f"{nombre}/{nombre}.parquet", tabla)]
    col = tabla[PARTICIONADAS[nombre]]
    anios, meses = pc.year(col), pc.month(col)
    pares = sorted(set(zip(anios.to_pylist(), meses.to_pylist())))
    salida = []
    for a, m in pares:
        mascara = pc.and_(pc.equal(anios, a), pc.equal(meses, m))
        salida.append((f"{nombre}/anio={a}/mes={m:02d}/{nombre}.parquet", tabla.filter(mascara)))
    return salida


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", default=os.environ.get("S3_BUCKET"), help="bucket de destino")
    p.add_argument("--prefijo", default="raw", help="prefijo de la capa raw (por defecto: raw)")
    p.add_argument("--bd", default="adquirente_dw")
    p.add_argument("--local", metavar="CARPETA", help="escribir en una carpeta local en vez de S3")
    a = p.parse_args()
    if not a.local and not a.bucket:
        sys.exit("Indica --bucket NOMBRE (o usa --local CARPETA para probar sin AWS).")

    import psycopg2
    s3 = None
    if not a.local:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
        s3 = boto3.client("s3")
        try:
            boto3.client("sts").get_caller_identity()
        except (BotoCoreError, ClientError) as e:
            sys.exit(f"No hay sesión de AWS válida ({type(e).__name__}). Ejecuta `aws login` y reintenta.")
        print(f"Sesión AWS OK (región {s3.meta.region_name}). Destino: s3://{a.bucket}/{a.prefijo}/")
    else:
        print(f"Modo local. Destino: {Path(a.local).resolve()}")

    def guardar(clave, contenido: bytes):
        if s3:
            s3.put_object(Bucket=a.bucket, Key=clave, Body=contenido)
        else:
            ruta = Path(a.local) / clave
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(contenido)

    manifiesto = {"generado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "origen": f"postgresql/{a.bd}", "tablas": {}}
    con = psycopg2.connect(dbname=a.bd)
    errores = 0
    print(f"{'tabla':<20}{'filas BD':>10}{'filas parquet':>15}{'archivos':>10}")
    with con, con.cursor() as cur:
        for nombre in TABLAS:
            cur.execute(f"SELECT count(*) FROM {nombre}")
            filas_bd = cur.fetchone()[0]
            tabla = leer_tabla(cur, nombre)
            escritas, archivos = 0, 0
            for rel, sub in particiones(nombre, tabla):
                guardar(f"{a.prefijo}/{rel}", a_parquet(sub))
                escritas += sub.num_rows
                archivos += 1
            ok = escritas == filas_bd
            errores += 0 if ok else 1
            print(f"{nombre:<20}{filas_bd:>10,}{escritas:>15,}{archivos:>10}{'' if ok else '   <-- NO COINCIDE'}")
            manifiesto["tablas"][nombre] = {"filas_origen": filas_bd, "filas_destino": escritas,
                                            "archivos": archivos, "coincide": ok}
    con.close()

    sello = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    guardar(f"meta/manifiestos/extraccion_{sello}.json",
            json.dumps(manifiesto, indent=2, ensure_ascii=False).encode("utf-8"))
    if errores:
        sys.exit(f"{errores} tabla(s) no coinciden entre origen y destino.")
    print("Extracción completa: todas las tablas coinciden con el origen.")


if __name__ == "__main__":
    main()
