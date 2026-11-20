#!/usr/bin/env python3
"""Registra en el AWS Glue Data Catalog las tablas Parquet de la capa raw, sin usar crawler.

Lee el esquema real de los archivos Parquet que hay en S3 (s3://<bucket>/raw/<tabla>/),
crea una tabla externa por carpeta en la base `adquirente_raw` y, para `transacciones`,
registra las particiones anio/mes que existen en S3. Es idempotente: se puede repetir.

Las tablas quedan disponibles para Amazon Athena, Redshift Spectrum y Glue.

Uso:
    python src/crear_tablas_catalogo.py --bucket leandrodlf-adquirente-datalake
"""
import argparse
import io
import re
import sys

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.exceptions import ClientError

from extraer_a_s3 import PARTICIONADAS, TABLAS

BASE_DATOS = "adquirente_raw"
FORMATO_ENTRADA = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
FORMATO_SALIDA = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
SERDE = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"


def tipo_glue(t: pa.DataType) -> str:
    if pa.types.is_int16(t):
        return "smallint"
    if pa.types.is_int32(t):
        return "int"
    if pa.types.is_int64(t):
        return "bigint"
    if pa.types.is_string(t):
        return "string"
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_date32(t):
        return "date"
    if pa.types.is_timestamp(t):
        return "timestamp"
    if pa.types.is_decimal(t):
        return f"decimal({t.precision},{t.scale})"
    sys.exit(f"Tipo de Parquet no soportado: {t}")


def listar_parquets(s3, bucket, prefijo):
    claves = []
    for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefijo):
        claves += [o["Key"] for o in pagina.get("Contents", []) if o["Key"].endswith(".parquet")]
    return sorted(claves)


def esquema_de(s3, bucket, clave) -> pa.Schema:
    cuerpo = s3.get_object(Bucket=bucket, Key=clave)["Body"].read()
    return pq.read_schema(io.BytesIO(cuerpo))


def asegurar_base(glue):
    try:
        glue.create_database(DatabaseInput={"Name": BASE_DATOS, "Description": "Capa raw del adquirente (Parquet en S3)"})
        print(f"Base de datos '{BASE_DATOS}' creada.")
    except ClientError as e:
        if e.response["Error"]["Code"] != "AlreadyExistsException":
            raise
        print(f"Base de datos '{BASE_DATOS}' ya existía.")


def registrar_tabla(glue, s3, bucket, nombre):
    prefijo = f"raw/{nombre}/"
    claves = listar_parquets(s3, bucket, prefijo)
    if not claves:
        sys.exit(f"No hay archivos Parquet en s3://{bucket}/{prefijo}. Ejecuta antes extraer_a_s3.py")
    esquema = esquema_de(s3, bucket, claves[0])
    descriptor = {
        "Columns": [{"Name": f.name, "Type": tipo_glue(f.type)} for f in esquema],
        "Location": f"s3://{bucket}/{prefijo}",
        "InputFormat": FORMATO_ENTRADA, "OutputFormat": FORMATO_SALIDA,
        "SerdeInfo": {"SerializationLibrary": SERDE, "Parameters": {"serialization.format": "1"}},
    }
    tabla = {
        "Name": nombre, "TableType": "EXTERNAL_TABLE",
        "Parameters": {"classification": "parquet", "EXTERNAL": "TRUE", "parquet.compression": "SNAPPY"},
        "StorageDescriptor": descriptor,
        "PartitionKeys": [{"Name": "anio", "Type": "int"}, {"Name": "mes", "Type": "int"}] if nombre in PARTICIONADAS else [],
    }
    try:
        glue.create_table(DatabaseName=BASE_DATOS, TableInput=tabla)
        estado = "creada"
    except ClientError as e:
        if e.response["Error"]["Code"] != "AlreadyExistsException":
            raise
        glue.update_table(DatabaseName=BASE_DATOS, TableInput=tabla)
        estado = "actualizada"

    n_part = 0
    if nombre in PARTICIONADAS:
        parts = {}
        for c in claves:
            m = re.search(r"anio=(\d+)/mes=(\d+)/", c)
            if m:
                parts[(int(m[1]), int(m[2]))] = c.rsplit("/", 1)[0] + "/"
        entradas = [{"Values": [str(a), str(me)],
                     "StorageDescriptor": {**descriptor, "Location": f"s3://{bucket}/{ubic}"}}
                    for (a, me), ubic in sorted(parts.items())]
        resp = glue.batch_create_partition(DatabaseName=BASE_DATOS, TableName=nombre, PartitionInputList=entradas)
        errores = [e for e in resp.get("Errors", []) if e["ErrorDetail"]["ErrorCode"] != "AlreadyExistsException"]
        if errores:
            sys.exit(f"Error registrando particiones de {nombre}: {errores[0]['ErrorDetail']}")
        n_part = len(entradas)
    extra = f", {n_part} particiones (anio/mes)" if n_part else ""
    print(f"  {nombre:<20} {estado}: {len(esquema)} columnas{extra}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", required=True)
    a = p.parse_args()
    sesion = boto3.Session()
    glue, s3 = sesion.client("glue"), sesion.client("s3")
    print(f"Región: {sesion.region_name}")
    asegurar_base(glue)
    print("Registrando tablas:")
    for t in TABLAS:
        registrar_tabla(glue, s3, a.bucket, t)
    print(f"Listo: {len(TABLAS)} tablas en el catálogo '{BASE_DATOS}'.")


if __name__ == "__main__":
    main()
