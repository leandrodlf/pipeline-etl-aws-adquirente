#!/usr/bin/env python3
"""Validaciones de calidad de datos sobre el data lake (Athena, base `adquirente_raw`).

Ejecuta reglas de negocio y de integridad. Cada regla es una consulta SQL que devuelve
las filas que la incumplen; el script cuenta los incumplimientos, los compara con lo
esperado y guarda un reporte JSON en s3://<bucket>/meta/calidad/.

Las 3 primeras reglas detectan las anomalías inyectadas a propósito al generar los datos
(30 + 20 + 8 = 58). Las demás deben dar 0.

Uso:
    python src/calidad_datos.py --bucket leandrodlf-adquirente-datalake
    python src/calidad_datos.py --bucket ... --detalle     # muestra ejemplos de filas con error
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import boto3

from consultar_athena import ejecutar, imprimir

# nombre, descripción, esperado (None = solo informar), SQL que devuelve las filas con error
REGLAS = [
    ("liq_neto_coherente",
     "liquidaciones: monto_neto = monto_bruto - comision",
     30,
     """SELECT id, comercio_id, monto_bruto, comision, monto_neto,
               monto_bruto - comision - monto_neto AS diferencia
        FROM liquidaciones
        WHERE monto_neto <> monto_bruto - comision"""),

    ("tx_aprobada_con_codigo",
     "transacciones APROBADAS deben tener codigo_autorizacion",
     20,
     """SELECT id, terminal_id, fecha_hora, monto
        FROM transacciones
        WHERE estado = 'APROBADA'
          AND (codigo_autorizacion IS NULL OR trim(codigo_autorizacion) = '')"""),

    ("lote_monto_cuadra",
     "lotes_cierre: monto_total = suma de transacciones aprobadas del lote",
     8,
     """WITH suma_tx AS (
            SELECT lote_id, sum(monto) AS monto_tx
            FROM transacciones
            WHERE estado = 'APROBADA' AND lote_id IS NOT NULL
            GROUP BY lote_id
        )
        SELECT l.id, l.terminal_id, l.monto_total,
               coalesce(s.monto_tx, 0) AS monto_tx,
               l.monto_total - coalesce(s.monto_tx, 0) AS diferencia
        FROM lotes_cierre l
        LEFT JOIN suma_tx s ON s.lote_id = l.id
        WHERE l.monto_total <> coalesce(s.monto_tx, 0)"""),

    ("tx_sin_terminal",
     "transacciones sin terminal existente (integridad referencial)",
     0,
     """SELECT t.id, t.terminal_id
        FROM transacciones t
        LEFT JOIN terminales m ON m.id = t.terminal_id
        WHERE m.id IS NULL"""),

    ("tx_monto_invalido",
     "transacciones con monto nulo o <= 0",
     0,
     """SELECT id, monto FROM transacciones WHERE monto IS NULL OR monto <= 0"""),

    ("tx_id_duplicado",
     "transacciones con id duplicado",
     0,
     """SELECT id, count(*) AS veces FROM transacciones GROUP BY id HAVING count(*) > 1"""),

    ("tx_liquidada_inexistente",
     "transacciones que apuntan a una liquidación que no existe",
     0,
     """SELECT t.id, t.liquidacion_id
        FROM transacciones t
        LEFT JOIN liquidaciones l ON l.id = t.liquidacion_id
        WHERE t.liquidacion_id IS NOT NULL AND l.id IS NULL"""),
]


def evaluar(ejecutar_sql, detalle=False):
    """ejecutar_sql(sql) -> filas (primera fila = encabezado). Devuelve lista de resultados."""
    resultados = []
    for nombre, descripcion, esperado, sql in REGLAS:
        filas = ejecutar_sql(sql)
        encontrados = max(len(filas) - 1, 0)
        if esperado is None:
            estado = "INFO"
        elif encontrados == esperado:
            estado = "OK"
        else:
            estado = "REVISAR"
        resultados.append({"regla": nombre, "descripcion": descripcion, "esperado": esperado,
                           "encontrados": encontrados, "estado": estado,
                           "ejemplos": filas[:4] if detalle and encontrados else []})
    return resultados


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", required=True)
    p.add_argument("--detalle", action="store_true", help="muestra hasta 3 filas de ejemplo por regla fallida")
    a = p.parse_args()

    athena = boto3.client("athena")
    resultados = evaluar(lambda sql: ejecutar(athena, sql, a.bucket)[0], a.detalle)

    tabla = [["regla", "esperado", "encontrados", "estado"]]
    for r in resultados:
        tabla.append([r["regla"], r["esperado"], r["encontrados"], r["estado"]])
    print("\nReporte de calidad de datos\n")
    imprimir(tabla)
    total = sum(r["encontrados"] for r in resultados)
    print(f"\nTotal de registros con problemas: {total}")

    if a.detalle:
        for r in resultados:
            if r["ejemplos"]:
                print(f"\n-- {r['regla']} (ejemplos)")
                imprimir(r["ejemplos"])

    ahora = datetime.now(timezone.utc)
    clave = f"meta/calidad/calidad_{ahora:%Y%m%dT%H%M%SZ}.json"
    cuerpo = {"generado_utc": ahora.isoformat(), "total_con_problemas": total,
              "reglas": [{k: v for k, v in r.items() if k != "ejemplos"} for r in resultados]}
    boto3.client("s3").put_object(Bucket=a.bucket, Key=clave,
                                  Body=json.dumps(cuerpo, indent=2, ensure_ascii=False).encode("utf-8"))
    print(f"Reporte guardado en s3://{a.bucket}/{clave}")

    if any(r["estado"] == "REVISAR" for r in resultados):
        sys.exit(1)


if __name__ == "__main__":
    main()
