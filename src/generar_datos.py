#!/usr/bin/env python3
"""Genera datos SIMULADOS de un adquirente de pagos y los carga en PostgreSQL.

Crea (si no existe) la base `adquirente_dw`, aplica sql/01_schema.sql y carga
12 tablas con integridad referencial. No toca otras bases del servidor.

Conexión: usa las variables de entorno estándar de PostgreSQL
(PGHOST, PGPORT, PGUSER, PGPASSWORD). Ejemplo en PowerShell:
    $env:PGHOST="localhost"; $env:PGPORT="5432"
    $env:PGUSER="postgres";  $env:PGPASSWORD="tu_clave"

Uso:
    python src/generar_datos.py                 # carga completa (120.000 transacciones)
    python src/generar_datos.py --solo-validar  # genera y valida en memoria, sin base de datos
"""
import argparse
import io
import os
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

RAIZ = Path(__file__).resolve().parent.parent
SQL_ESQUEMA = RAIZ / "sql" / "01_schema.sql"

RUBROS = [("Supermercados", "5411"), ("Restaurantes", "5812"), ("Farmacias", "5912"),
          ("Estaciones de servicio", "5541"), ("Vestuario", "5651"), ("Ferretería", "5251"),
          ("Electrónica", "5732"), ("Hoteles", "7011"), ("Transporte", "4111"),
          ("Salud", "8011"), ("Educación", "8299"), ("Cafeterías", "5814"),
          ("Librerías", "5942"), ("Mascotas", "5995"), ("Belleza", "7230")]
BANCOS = [("Banco Andino", "CL"), ("Banco del Pacífico Sur", "CL"), ("Banco Cordillera", "CL"),
          ("Banco Austral", "CL"), ("Banco Mapocho", "CL"), ("Banco Atlántico", "AR"),
          ("Banco Andes Perú", "PE"), ("Global Card Bank", "US")]
MODELOS = [("PAX", "A920", "Android"), ("PAX", "D190", "Móvil"), ("Ingenico", "Move5000", "Móvil"),
           ("Verifone", "VX520", "Fijo"), ("Newland", "N910", "Android"), ("Sunmi", "P2", "Android")]
CODIGOS = [("00", "Aprobada", "APROBADA"), ("05", "No honrar", "RECHAZO"),
           ("14", "Número de tarjeta inválido", "RECHAZO"), ("51", "Fondos insuficientes", "RECHAZO"),
           ("54", "Tarjeta vencida", "RECHAZO"), ("55", "PIN incorrecto", "RECHAZO"),
           ("57", "Transacción no permitida", "RECHAZO"), ("91", "Emisor no disponible", "ERROR"),
           ("96", "Error de sistema", "ERROR")]
P_RECHAZOS = [0.10, 0.03, 0.45, 0.10, 0.10, 0.07, 0.10, 0.05]  # códigos 05..96
# región: (peso, comunas)
REGIONES = {
    "Metropolitana": (0.55, ["Santiago", "Providencia", "Las Condes", "Ñuñoa", "Maipú", "La Florida", "San Joaquín"]),
    "Valparaíso": (0.12, ["Valparaíso", "Viña del Mar", "Quilpué"]),
    "Biobío": (0.10, ["Concepción", "Talcahuano", "Los Ángeles"]),
    "Araucanía": (0.06, ["Temuco", "Villarrica"]),
    "Los Lagos": (0.05, ["Puerto Montt", "Osorno"]),
    "Coquimbo": (0.05, ["La Serena", "Coquimbo"]),
    "Maule": (0.04, ["Talca", "Curicó"]),
    "Antofagasta": (0.03, ["Antofagasta", "Calama"]),
}
TASA_COMISION = {"CREDITO": 0.0195, "DEBITO": 0.0095, "PREPAGO": 0.0120}
SUFIJOS = ("E.I.R.L.", "EIRL", "LIMITADA", "LTDA.", "S.A.", "SPA", "S.P.A.", "S.A", "SA", "SOCIEDAD ANÓNIMA", "Y CIA.", "HERMANOS")
MOTIVOS_CONTRACARGO = ["Fraude", "Producto no recibido", "Cobro duplicado", "Servicio no prestado", "Monto incorrecto"]


def dv_rut(numero: int) -> str:
    suma, mult = 0, 2
    for d in reversed(str(numero)):
        suma += int(d) * mult
        mult = mult + 1 if mult < 7 else 2
    r = 11 - suma % 11
    return "0" if r == 11 else "K" if r == 10 else str(r)


def formatear_rut(numero: int) -> str:
    return f"{numero:,}".replace(",", ".") + "-" + dv_rut(numero)


def generar(a):
    rng = np.random.default_rng(a.semilla)
    random.seed(a.semilla)
    fk = Faker("es_CL")
    Faker.seed(a.semilla)
    hasta = date.fromisoformat(a.hasta) if a.hasta else date.today()
    desde = hasta - timedelta(days=a.dias)

    # ---------- Catálogos ----------
    rubros = pd.DataFrame({"id": range(1, len(RUBROS) + 1),
                           "nombre": [r[0] for r in RUBROS], "codigo_mcc": [r[1] for r in RUBROS]})
    bancos = pd.DataFrame({"id": range(1, len(BANCOS) + 1),
                           "nombre": [b[0] for b in BANCOS], "pais": [b[1] for b in BANCOS]})
    modelos = pd.DataFrame({"id": range(1, len(MODELOS) + 1), "marca": [m[0] for m in MODELOS],
                            "modelo": [m[1] for m in MODELOS], "tipo": [m[2] for m in MODELOS]})
    codigos = pd.DataFrame({"id": range(1, len(CODIGOS) + 1), "codigo": [c[0] for c in CODIGOS],
                            "descripcion": [c[1] for c in CODIGOS], "tipo": [c[2] for c in CODIGOS]})

    # ---------- Comercios ----------
    n = a.comercios
    ruts = rng.choice(np.arange(76_000_000, 79_000_000), size=n, replace=False)
    nombres = [fk.unique.company() for _ in range(n)]
    regiones = list(REGIONES)
    region = rng.choice(regiones, size=n, p=[REGIONES[r][0] for r in regiones])
    base = desde - timedelta(days=1500)
    span = (hasta - timedelta(days=60) - base).days
    afiliacion = [base + timedelta(days=int(x)) for x in rng.integers(0, span, n)]
    comercios = pd.DataFrame({
        "id": range(1, n + 1),
        "rut": [formatear_rut(int(r)) for r in ruts],
        "razon_social": [x if x.upper().endswith(SUFIJOS) else f"{x} {random.choice(['SpA', 'Ltda.', 'S.A.'])}" for x in nombres],
        "nombre_fantasia": nombres,
        "rubro_id": rng.integers(1, len(RUBROS) + 1, n),
        "region": region,
        "fecha_afiliacion": afiliacion,
        "estado": rng.choice(["ACTIVO", "SUSPENDIDO", "CERRADO"], size=n, p=[0.90, 0.06, 0.04]),
    })

    # ---------- Sucursales ----------
    ns = rng.choice([1, 2, 3], size=n, p=[0.6, 0.3, 0.1])
    suc_com = np.repeat(comercios["id"].values, ns)
    reg_de = comercios.set_index("id")["region"]
    fant_de = comercios.set_index("id")["nombre_fantasia"]
    comunas = [random.choice(REGIONES[reg_de[c]][1]) for c in suc_com]
    sucursales = pd.DataFrame({
        "id": range(1, len(suc_com) + 1),
        "comercio_id": suc_com,
        "nombre": [f"{fant_de[c]} {co}" for c, co in zip(suc_com, comunas)],
        "comuna": comunas,
        "direccion": [fk.street_address() for _ in suc_com],
    })

    # ---------- Terminales ----------
    nt = rng.choice([1, 2, 3], size=len(sucursales), p=[0.65, 0.25, 0.10])
    t_suc = np.repeat(sucursales["id"].values, nt)
    suc_com_de = sucursales.set_index("id")["comercio_id"]
    com_estado = comercios.set_index("id")["estado"]
    com_afil = comercios.set_index("id")["fecha_afiliacion"]
    t_com = suc_com_de[t_suc].values
    terminales = pd.DataFrame({
        "sucursal_id": t_suc,
        "estado": np.where(com_estado[t_com].values == "ACTIVO", "ACTIVA", "INACTIVA"),
        "fecha_instalacion": [com_afil[c] + timedelta(days=int(d)) for c, d in zip(t_com, rng.integers(0, 31, len(t_com)))],
    })
    n_bod = int(0.03 * len(terminales))  # terminales en bodega, sin sucursal
    bodega = pd.DataFrame({"sucursal_id": [None] * n_bod, "estado": "EN_BODEGA", "fecha_instalacion": [None] * n_bod})
    terminales = pd.concat([terminales, bodega], ignore_index=True)
    terminales.insert(0, "id", range(1, len(terminales) + 1))
    terminales.insert(1, "numero_serie", [f"SN{20250000 + i:08d}" for i in terminales["id"]])
    terminales.insert(2, "modelo_id", rng.integers(1, len(MODELOS) + 1, len(terminales)))
    terminales["sucursal_id"] = terminales["sucursal_id"].astype("Int64")

    # ---------- Tarjetas ----------
    ntar = a.tarjetas
    marca = rng.choice(["VISA", "MASTERCARD", "AMEX"], size=ntar, p=[0.55, 0.40, 0.05])
    tipo = rng.choice(["CREDITO", "DEBITO", "PREPAGO"], size=ntar, p=[0.45, 0.45, 0.10])
    tipo = np.where(marca == "AMEX", "CREDITO", tipo)
    pref = {"VISA": "4", "MASTERCARD": "5", "AMEX": "37"}
    bins = [pref[m] + "".join(str(rng.integers(0, 10)) for _ in range(6 - len(pref[m]))) for m in marca]
    tarjetas = pd.DataFrame({"id": range(1, ntar + 1), "bin": bins, "marca": marca, "tipo": tipo,
                             "banco_emisor_id": rng.integers(1, len(BANCOS) + 1, ntar)})

    # ---------- Transacciones ----------
    act = terminales[terminales["estado"] == "ACTIVA"].reset_index(drop=True)
    peso = rng.lognormal(0, 1.0, len(act))
    peso /= peso.sum()
    idx = rng.choice(len(act), size=a.tx, p=peso)
    t_id = act["id"].values[idx]
    inst = pd.to_datetime(act["fecha_instalacion"]).to_numpy().astype("datetime64[D]")[idx]
    inicio = np.maximum(inst, np.datetime64(desde))
    dias_span = (np.datetime64(hasta) - inicio).astype(int) + 1
    dia = inicio + (rng.random(a.tx) * dias_span).astype(int).astype("timedelta64[D]")
    seg = rng.integers(8 * 3600, 22 * 3600, a.tx)
    fecha_hora = dia.astype("datetime64[s]") + seg.astype("timedelta64[s]")

    tarj_id = rng.integers(1, ntar + 1, a.tx)
    tipo_tx = tarjetas["tipo"].values[tarj_id - 1]
    cuotas = np.where(tipo_tx == "CREDITO", rng.choice([1, 3, 6, 12], size=a.tx, p=[0.70, 0.12, 0.10, 0.08]), 1)
    monto = (np.clip(rng.lognormal(np.log(18000), 0.9, a.tx), 500, 2_000_000) / 10).round() * 10
    aprobada = rng.random(a.tx) < 0.92
    cod_id = np.where(aprobada, 1, rng.choice(np.arange(2, 10), size=a.tx, p=P_RECHAZOS))
    autoriz = np.array([f"{x:06d}" for x in rng.integers(0, 1_000_000, a.tx)], dtype=object)
    autoriz[~aprobada] = None

    tx = pd.DataFrame({"terminal_id": t_id, "tarjeta_id": tarj_id, "fecha_hora": fecha_hora,
                       "monto": monto.astype("int64"), "cuotas": cuotas,
                       "estado": np.where(aprobada, "APROBADA", "RECHAZADA"),
                       "codigo_autorizacion": autoriz, "codigo_respuesta_id": cod_id})
    tx = tx.sort_values("fecha_hora", kind="stable").reset_index(drop=True)
    tx.insert(0, "id", range(1, len(tx) + 1))
    tx["fecha"] = tx["fecha_hora"].dt.normalize()
    ok = tx["estado"] == "APROBADA"

    # ---------- Lotes de cierre (terminal + día) ----------
    lotes = tx.groupby(["terminal_id", "fecha"]).agg(cantidad_tx=("id", "size")).reset_index()
    aprob_dia = tx[ok].groupby(["terminal_id", "fecha"])["monto"].sum().rename("monto_total").reset_index()
    lotes = lotes.merge(aprob_dia, on=["terminal_id", "fecha"], how="left").fillna({"monto_total": 0})
    lotes.insert(0, "id", range(1, len(lotes) + 1))
    lotes["fecha_cierre"] = lotes["fecha"] + pd.to_timedelta(23 * 3600 + rng.integers(0, 3600, len(lotes)), unit="s")
    lotes["estado"] = np.where(lotes["fecha"] >= pd.Timestamp(hasta), "ABIERTO", "CERRADO")
    tx = tx.merge(lotes[["terminal_id", "fecha", "id"]].rename(columns={"id": "lote_id"}),
                  on=["terminal_id", "fecha"], how="left")

    # ---------- Liquidaciones (comercio + viernes de pago) ----------
    com_de_term = np.zeros(len(terminales) + 1, dtype=int)
    t_s = terminales["sucursal_id"]
    mask = t_s.notna().values
    com_de_term[terminales["id"].values[mask]] = suc_com_de[t_s[mask].astype(int).values].values
    tx["comercio_id"] = com_de_term[tx["terminal_id"].values]
    wd = tx["fecha"].dt.weekday.values
    suma = (4 - wd) % 7
    suma = np.where(suma == 0, 7, suma)
    tx["fecha_pago"] = tx["fecha"] + pd.to_timedelta(suma, unit="D")
    tx["tipo_tarjeta"] = tarjetas["tipo"].values[tx["tarjeta_id"].values - 1]
    tx["comision_tx"] = tx["monto"] * tx["tipo_tarjeta"].map(TASA_COMISION)
    elegible = ok & (tx["fecha_pago"] <= pd.Timestamp(hasta))
    g = tx[elegible].groupby(["comercio_id", "fecha_pago"]).agg(
        monto_bruto=("monto", "sum"), comision=("comision_tx", "sum")).reset_index()
    g["comision"] = g["comision"].round(0)
    g["monto_neto"] = g["monto_bruto"] - g["comision"]
    g.insert(0, "id", range(1, len(g) + 1))
    g["estado"] = "PAGADA"
    liq = g[["id", "comercio_id", "fecha_pago", "monto_bruto", "comision", "monto_neto", "estado"]].copy()
    liq["fecha_pago"] = liq["fecha_pago"].dt.date
    mapa = g.set_index(["comercio_id", "fecha_pago"])["id"]
    tx["liquidacion_id"] = pd.NA
    clave = pd.MultiIndex.from_frame(tx.loc[elegible, ["comercio_id", "fecha_pago"]])
    tx.loc[elegible, "liquidacion_id"] = mapa.reindex(clave).values
    tx["liquidacion_id"] = tx["liquidacion_id"].astype("Int64")

    # ---------- Contracargos ----------
    aprob_idx = tx.index[ok]
    k = int(0.004 * len(aprob_idx))
    pick = rng.choice(aprob_idx, size=k, replace=False)
    cc = pd.DataFrame({
        "transaccion_id": tx.loc[pick, "id"].values,
        "motivo": rng.choice(MOTIVOS_CONTRACARGO, size=k),
        "monto": tx.loc[pick, "monto"].values,
        "fecha": tx.loc[pick, "fecha"].values + pd.to_timedelta(rng.integers(10, 61, k), unit="D"),
        "estado": rng.choice(["ABIERTO", "GANADO", "PERDIDO"], size=k, p=[0.3, 0.3, 0.4]),
    })
    cc = cc[cc["fecha"] <= pd.Timestamp(hasta)].reset_index(drop=True)
    cc["fecha"] = cc["fecha"].dt.date
    cc.insert(0, "id", range(1, len(cc) + 1))

    # ---------- Anomalías deliberadas (para que las validaciones de calidad tengan qué detectar) ----------
    resumen_anom = {}
    if not a.sin_anomalias:
        k1 = max(3, int(0.002 * len(liq)))
        i1 = rng.choice(liq.index, size=k1, replace=False)
        liq.loc[i1, "monto_neto"] = liq.loc[i1, "monto_neto"] + rng.integers(1000, 50_000, k1)
        k2 = 20
        i2 = rng.choice(tx.index[ok], size=k2, replace=False)
        tx.loc[i2, "codigo_autorizacion"] = None
        k3 = 8
        i3 = rng.choice(lotes.index, size=k3, replace=False)
        lotes.loc[i3, "monto_total"] = lotes.loc[i3, "monto_total"] + rng.integers(500, 20_000, k3)
        resumen_anom = {"liquidaciones con neto != bruto - comision": k1,
                        "tx aprobadas sin codigo_autorizacion": k2,
                        "lotes con monto_total != suma de tx aprobadas": k3}

    # ---------- Tablas finales (orden de columnas = DDL) ----------
    lotes_out = lotes[["id", "terminal_id", "fecha_cierre", "cantidad_tx", "monto_total", "estado"]].copy()
    tx_out = tx[["id", "terminal_id", "tarjeta_id", "liquidacion_id", "fecha_hora", "monto", "cuotas",
                 "estado", "codigo_autorizacion", "codigo_respuesta_id", "lote_id"]].copy()
    return {
        "rubros": rubros, "bancos_emisores": bancos, "modelos_terminal": modelos,
        "codigos_respuesta": codigos, "comercios": comercios, "sucursales": sucursales,
        "terminales": terminales, "tarjetas": tarjetas, "liquidaciones": liq,
        "lotes_cierre": lotes_out, "transacciones": tx_out, "contracargos": cc,
    }, resumen_anom


# (hija, columna, padre)
RELACIONES = [
    ("comercios", "rubro_id", "rubros"), ("sucursales", "comercio_id", "comercios"),
    ("terminales", "modelo_id", "modelos_terminal"), ("terminales", "sucursal_id", "sucursales"),
    ("tarjetas", "banco_emisor_id", "bancos_emisores"), ("liquidaciones", "comercio_id", "comercios"),
    ("lotes_cierre", "terminal_id", "terminales"), ("transacciones", "terminal_id", "terminales"),
    ("transacciones", "tarjeta_id", "tarjetas"), ("transacciones", "liquidacion_id", "liquidaciones"),
    ("transacciones", "codigo_respuesta_id", "codigos_respuesta"), ("transacciones", "lote_id", "lotes_cierre"),
    ("contracargos", "transaccion_id", "transacciones"),
]


def validar_integridad(t):
    errores = 0
    for hija, col, padre in RELACIONES:
        valores = t[hija][col].dropna()
        huerfanos = (~valores.isin(t[padre]["id"])).sum()
        if huerfanos:
            errores += 1
            print(f"  [ERROR] {hija}.{col}: {huerfanos} huérfanos")
    for nombre, df in t.items():
        if df["id"].duplicated().any():
            errores += 1
            print(f"  [ERROR] {nombre}: ids duplicados")
    return errores


def conectar(dbname):
    import psycopg2
    return psycopg2.connect(dbname=dbname)


def copiar(cur, tabla, df):
    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="")
    buf.seek(0)
    cur.copy_expert(f"COPY {tabla} ({', '.join(df.columns)}) FROM STDIN WITH (FORMAT csv)", buf)


def cargar(tablas, nombre_bd):
    con = conectar("postgres")
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nombre_bd,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{nombre_bd}"')
            print(f"Base '{nombre_bd}' creada.")
    con.close()

    con = conectar(nombre_bd)
    with con, con.cursor() as cur:
        cur.execute(SQL_ESQUEMA.read_text(encoding="utf-8"))
        orden = ["rubros", "bancos_emisores", "modelos_terminal", "codigos_respuesta", "comercios",
                 "sucursales", "terminales", "tarjetas", "liquidaciones", "lotes_cierre",
                 "transacciones", "contracargos"]
        for nombre in orden:
            copiar(cur, nombre, tablas[nombre])
            print(f"  {nombre:<20} {len(tablas[nombre]):>9,} filas")
    con.close()
    con = conectar(nombre_bd)
    con.autocommit = True
    with con.cursor() as cur:
        cur.execute("ANALYZE")
    con.close()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tx", type=int, default=120_000, help="cantidad de transacciones")
    p.add_argument("--comercios", type=int, default=500)
    p.add_argument("--tarjetas", type=int, default=5_000)
    p.add_argument("--dias", type=int, default=300, help="días de historia hacia atrás")
    p.add_argument("--hasta", default=None, help="fecha final AAAA-MM-DD (por defecto, hoy)")
    p.add_argument("--semilla", type=int, default=42)
    p.add_argument("--bd", default=os.environ.get("PGDATABASE_DESTINO", "adquirente_dw"))
    p.add_argument("--sin-anomalias", action="store_true", help="no inyectar inconsistencias deliberadas")
    p.add_argument("--solo-validar", action="store_true", help="no conecta a la base de datos")
    a = p.parse_args()

    print("Generando datos simulados...")
    tablas, anomalias = generar(a)
    print("Validando integridad referencial en memoria...")
    if validar_integridad(tablas):
        sys.exit("Hay errores de integridad; no se carga nada.")
    print("  OK")
    if anomalias:
        print("Inconsistencias inyectadas a propósito (las detectará la etapa de calidad):")
        for k, v in anomalias.items():
            print(f"  - {k}: {v}")
    if a.solo_validar:
        for nombre, df in tablas.items():
            print(f"  {nombre:<20} {len(df):>9,} filas")
        return
    print(f"Cargando en PostgreSQL (base '{a.bd}')...")
    cargar(tablas, a.bd)
    print("Listo.")


if __name__ == "__main__":
    main()
