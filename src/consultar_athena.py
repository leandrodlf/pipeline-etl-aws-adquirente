#!/usr/bin/env python3
"""Ejecuta una consulta SQL en Amazon Athena sobre el catálogo `adquirente_raw` y muestra el resultado.

Sin argumentos de consulta ejecuta un resumen mensual de transacciones (consulta de ejemplo).
Los resultados quedan además en s3://<bucket>/athena-results/.

Uso:
    python src/consultar_athena.py --bucket leandrodlf-adquirente-datalake
    python src/consultar_athena.py --bucket ... --sql "SELECT count(*) FROM comercios"
    python src/consultar_athena.py --bucket ... --archivo sql/athena/mi_consulta.sql
"""
import argparse
import sys
import time
from pathlib import Path

import boto3

BASE_DATOS = "adquirente_raw"

CONSULTA_EJEMPLO = """
SELECT anio,
       mes,
       count(*)                                              AS transacciones,
       round(100.0 * count_if(estado = 'APROBADA') / count(*), 1) AS pct_aprobacion,
       sum(CASE WHEN estado = 'APROBADA' THEN monto END)     AS monto_aprobado
FROM transacciones
GROUP BY anio, mes
ORDER BY anio, mes
"""


def ejecutar(athena, sql, bucket, workgroup="primary"):
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": BASE_DATOS},
        ResultConfiguration={"OutputLocation": f"s3://{bucket}/athena-results/"},
        WorkGroup=workgroup,
    )["QueryExecutionId"]
    while True:
        ej = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        estado = ej["Status"]["State"]
        if estado in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if estado != "SUCCEEDED":
        sys.exit(f"La consulta terminó en {estado}: {ej['Status'].get('StateChangeReason', 'sin detalle')}")
    filas = []
    for pagina in athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        for fila in pagina["ResultSet"]["Rows"]:
            filas.append([c.get("VarCharValue", "") for c in fila["Data"]])
    escaneado = ej.get("Statistics", {}).get("DataScannedInBytes", 0)
    return filas, escaneado


def imprimir(filas):
    if not filas:
        print("(sin resultados)")
        return
    anchos = [max(len(str(f[i])) for f in filas) for i in range(len(filas[0]))]
    for n, fila in enumerate(filas):
        print("  ".join(str(v).rjust(w) for v, w in zip(fila, anchos)))
        if n == 0:
            print("  ".join("-" * w for w in anchos))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", required=True)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--sql", help="consulta SQL a ejecutar")
    g.add_argument("--archivo", help="archivo .sql con la consulta")
    a = p.parse_args()
    if a.archivo:
        sql = Path(a.archivo).read_text(encoding="utf-8")
    else:
        sql = a.sql or CONSULTA_EJEMPLO
    filas, escaneado = ejecutar(boto3.client("athena"), sql, a.bucket)
    imprimir(filas)
    print(f"\n{max(len(filas) - 1, 0)} filas | datos escaneados: {escaneado / 1024:,.0f} KB")


if __name__ == "__main__":
    main()
