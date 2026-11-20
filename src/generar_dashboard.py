#!/usr/bin/env python3
"""Genera un dashboard HTML estático con indicadores del adquirente, consultando Athena.

Crea `dashboard/index.html`: un único archivo, sin dependencias externas (gráficos SVG
propios, con tooltips, modo claro/oscuro y tabla de datos). Se puede publicar con
GitHub Pages.

Uso:
    python src/generar_dashboard.py --bucket leandrodlf-adquirente-datalake
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import boto3

from consultar_athena import ejecutar

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "dashboard" / "index.html"

CONSULTAS = {
    "mensual": """
        SELECT anio, mes,
               count(*) AS transacciones,
               round(100.0 * count_if(estado = 'APROBADA') / count(*), 1) AS pct_aprobacion,
               sum(CASE WHEN estado = 'APROBADA' THEN monto END) AS monto_aprobado
        FROM transacciones
        GROUP BY anio, mes
        ORDER BY anio, mes""",
    "region": """
        SELECT c.region, sum(t.monto) AS monto_aprobado
        FROM transacciones t
        JOIN terminales m ON m.id = t.terminal_id
        JOIN sucursales s ON s.id = m.sucursal_id
        JOIN comercios c ON c.id = s.comercio_id
        WHERE t.estado = 'APROBADA'
        GROUP BY c.region
        ORDER BY monto_aprobado DESC""",
    "top_comercios": """
        SELECT c.nombre_fantasia, sum(t.monto) AS monto_aprobado
        FROM transacciones t
        JOIN terminales m ON m.id = t.terminal_id
        JOIN sucursales s ON s.id = m.sucursal_id
        JOIN comercios c ON c.id = s.comercio_id
        WHERE t.estado = 'APROBADA'
        GROUP BY c.nombre_fantasia
        ORDER BY monto_aprobado DESC
        LIMIT 10""",
    "contracargos": """
        SELECT motivo, count(*) AS cantidad
        FROM contracargos
        GROUP BY motivo
        ORDER BY cantidad DESC""",
    "kpis": """
        SELECT count(*) AS transacciones,
               round(100.0 * count_if(estado = 'APROBADA') / count(*), 1) AS pct_aprobacion,
               sum(CASE WHEN estado = 'APROBADA' THEN monto END) AS monto_aprobado,
               (SELECT count(*) FROM contracargos) AS contracargos
        FROM transacciones""",
}


def recolectar(consultar):
    """consultar(sql) -> filas con encabezado en la primera. Devuelve dict listo para el HTML."""
    datos = {}
    for nombre, sql in CONSULTAS.items():
        filas = consultar(sql)
        cols, cuerpo = filas[0], filas[1:]
        datos[nombre] = [dict(zip(cols, fila)) for fila in cuerpo]
    for fila in datos["mensual"]:
        fila["anio"], fila["mes"] = int(fila["anio"]), int(fila["mes"])
        fila["transacciones"] = int(fila["transacciones"])
        fila["pct_aprobacion"] = float(fila["pct_aprobacion"])
        fila["monto_aprobado"] = float(fila["monto_aprobado"])
    for clave, campo in (("region", "monto_aprobado"), ("top_comercios", "monto_aprobado")):
        for fila in datos[clave]:
            fila[campo] = float(fila[campo])
    for fila in datos["contracargos"]:
        fila["cantidad"] = int(fila["cantidad"])
    k = datos["kpis"][0]
    datos["kpis"] = {"transacciones": int(k["transacciones"]), "pct_aprobacion": float(k["pct_aprobacion"]),
                     "monto_aprobado": float(k["monto_aprobado"]), "contracargos": int(k["contracargos"])}
    return datos


def render(datos):
    html = PLANTILLA.replace("__DATOS__", json.dumps(datos, ensure_ascii=False))
    return html.replace("__FECHA__", datetime.now().strftime("%d-%m-%Y"))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bucket", required=True)
    a = p.parse_args()
    athena = boto3.client("athena")
    datos = recolectar(lambda sql: ejecutar(athena, sql, a.bucket)[0])
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(render(datos), encoding="utf-8")
    print(f"Dashboard generado: {SALIDA}")


PLANTILLA = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard del adquirente (datos simulados)</title>
<style>
  :root {
    color-scheme: light;
    --page: #f4f3ef; --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
    --text-muted: #6f6e68; --grid: #e1e0d9; --series-1: #2a78d6;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --page: #121211; --surface-1: #1a1a19; --text-primary: #f0efec; --text-secondary: #c3c2b7;
      --text-muted: #9b9a92; --grid: #2c2c2a; --series-1: #3987e5;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--text-primary);
         font: 15px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 1100px; margin: 0 auto; padding: 28px 20px 48px; }
  h1 { font-size: 24px; margin: 0 0 4px; }
  .sub { color: var(--text-secondary); margin: 0 0 24px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .card { background: var(--surface-1); border: 1px solid var(--grid); border-radius: 10px; padding: 16px; position: relative; }
  .kpi .v { font-size: 30px; font-weight: 650; letter-spacing: -0.5px; }
  .kpi .l { color: var(--text-secondary); font-size: 13px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(440px, 1fr)); gap: 12px; }
  @media (max-width: 520px) { .grid { grid-template-columns: 1fr; } }
  .card h2 { font-size: 15px; margin: 0 0 2px; }
  .card .d { color: var(--text-secondary); font-size: 13px; margin: 0 0 8px; }
  svg { width: 100%; height: auto; display: block; }
  svg text { fill: var(--text-secondary); font-size: 11px; font-family: inherit; }
  svg .val { fill: var(--text-primary); }
  svg .grid { stroke: var(--grid); stroke-width: 1; }
  .bar, .dot { fill: var(--series-1); }
  .line { fill: none; stroke: var(--series-1); stroke-width: 2; stroke-linejoin: round; }
  .dot { stroke: var(--surface-1); stroke-width: 2; }
  .cross { stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 3 3; }
  .hit { fill: transparent; cursor: default; }
  #tip { position: fixed; pointer-events: none; background: var(--surface-1); color: var(--text-primary);
         border: 1px solid var(--grid); border-radius: 8px; padding: 6px 10px; font-size: 13px;
         box-shadow: 0 4px 14px rgba(0,0,0,.18); display: none; z-index: 10; white-space: nowrap; }
  #tip b { font-weight: 650; }
  details { margin-top: 16px; }
  summary { cursor: pointer; color: var(--text-secondary); }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; font-size: 13px; }
  th, td { text-align: right; padding: 6px 10px; border-bottom: 1px solid var(--grid); }
  th:first-child, td:first-child { text-align: left; }
  footer { color: var(--text-muted); font-size: 12px; margin-top: 20px; }
</style>
</head>
<body>
<main>
  <h1>Dashboard del adquirente</h1>
  <p class="sub">Indicadores calculados con Amazon Athena sobre el data lake en S3. Todos los datos son simulados.</p>
  <section class="kpis" id="kpis"></section>
  <section class="grid">
    <div class="card"><h2>Monto aprobado por mes</h2><p class="d">En pesos chilenos, millones. Noviembre incluye solo parte del mes.</p><div id="c-monto"></div></div>
    <div class="card"><h2>Tasa de aprobación por mes</h2><p class="d">Porcentaje de transacciones aprobadas. El eje no parte en cero.</p><div id="c-aprob"></div></div>
    <div class="card"><h2>Monto aprobado por región</h2><p class="d">Millones de pesos, de enero a noviembre.</p><div id="c-region"></div></div>
    <div class="card"><h2>Top 10 comercios por monto aprobado</h2><p class="d">Millones de pesos.</p><div id="c-top"></div></div>
    <div class="card"><h2>Contracargos por motivo</h2><p class="d">Cantidad de contracargos.</p><div id="c-cc"></div></div>
  </section>
  <details>
    <summary>Ver tabla de datos mensuales</summary>
    <table id="tabla"></table>
  </details>
  <footer>Generado el __FECHA__ por src/generar_dashboard.py. Proyecto personal con datos simulados.</footer>
</main>
<div id="tip"></div>
<script>
const D = __DATOS__;
const MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
const NS = "http://www.w3.org/2000/svg";
const nf = (v, d=0) => v.toLocaleString("es-CL", {maximumFractionDigits: d, minimumFractionDigits: d});
const fM = v => "$" + nf(v / 1e6, 1) + " M";
const tip = document.getElementById("tip");
function showTip(e, html) {
  tip.innerHTML = html; tip.style.display = "block";
  const w = tip.offsetWidth, x = Math.min(e.clientX + 14, innerWidth - w - 8);
  tip.style.left = x + "px"; tip.style.top = (e.clientY + 14) + "px";
}
function hideTip() { tip.style.display = "none"; }
function el(name, attrs, parent) {
  const n = document.createElementNS(NS, name);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(n);
  return n;
}
function txt(parent, x, y, s, attrs={}) { const t = el("text", {x, y, ...attrs}, parent); t.textContent = s; return t; }
function nice(max) {
  const p = Math.pow(10, Math.floor(Math.log10(max))), f = max / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
}
function topRounded(x, y, w, h, r) {
  r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function endRounded(x, y, w, h, r) {
  r = Math.min(r, h / 2, w);
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
}
function svgIn(id, W, H, label) {
  const host = document.getElementById(id);
  return el("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": label}, host);
}

function yAxis(svg, m, W, H, lo, hi, n, fmt) {
  for (let i = 0; i <= n; i++) {
    const v = lo + (hi - lo) * i / n, y = H - m.b - (H - m.t - m.b) * i / n;
    el("line", {x1: m.l, x2: W - m.r, y1: y, y2: y, class: "grid"}, svg);
    txt(svg, m.l - 8, y + 4, fmt(v), {"text-anchor": "end"});
  }
}

function barrasVerticales(id, labels, values, fmtAxis, fmtTip, desc) {
  const W = 560, H = 260, m = {l: 52, r: 8, t: 10, b: 26};
  const svg = svgIn(id, W, H, desc), hi = nice(Math.max(...values));
  yAxis(svg, m, W, H, 0, hi, 4, fmtAxis);
  const band = (W - m.l - m.r) / values.length, bw = Math.min(30, band * 0.55);
  values.forEach((v, i) => {
    const h = (H - m.t - m.b) * v / hi, x = m.l + band * i + (band - bw) / 2, y = H - m.b - h;
    el("path", {d: topRounded(x, y, bw, h, 4), class: "bar"}, svg);
    txt(svg, m.l + band * i + band / 2, H - 8, labels[i], {"text-anchor": "middle"});
    const hit = el("rect", {x: m.l + band * i, y: m.t, width: band, height: H - m.t - m.b, class: "hit"}, svg);
    hit.addEventListener("mousemove", e => showTip(e, fmtTip(i)));
    hit.addEventListener("mouseleave", hideTip);
  });
}

function lineaSimple(id, labels, values, fmtAxis, fmtTip, desc) {
  const W = 560, H = 260, m = {l: 52, r: 14, t: 10, b: 26};
  const svg = svgIn(id, W, H, desc);
  const lo = Math.floor(Math.min(...values) - 1), hi = lo + Math.ceil((Math.max(...values) + 1 - lo) / 4) * 4;
  yAxis(svg, m, W, H, lo, hi, 4, fmtAxis);
  const band = (W - m.l - m.r) / values.length;
  const px = i => m.l + band * i + band / 2, py = v => H - m.b - (H - m.t - m.b) * (v - lo) / (hi - lo);
  el("path", {d: values.map((v, i) => (i ? "L" : "M") + px(i) + "," + py(v)).join(""), class: "line"}, svg);
  const cross = el("line", {y1: m.t, y2: H - m.b, class: "cross", visibility: "hidden"}, svg);
  values.forEach((v, i) => {
    el("circle", {cx: px(i), cy: py(v), r: 4, class: "dot"}, svg);
    txt(svg, px(i), H - 8, labels[i], {"text-anchor": "middle"});
    const hit = el("rect", {x: m.l + band * i, y: m.t, width: band, height: H - m.t - m.b, class: "hit"}, svg);
    hit.addEventListener("mousemove", e => { cross.setAttribute("x1", px(i)); cross.setAttribute("x2", px(i));
      cross.setAttribute("visibility", "visible"); showTip(e, fmtTip(i)); });
    hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  });
}

function barrasHorizontales(id, labels, values, fmtVal, fmtTip, desc) {
  const maxLen = Math.min(34, Math.max(...labels.map(l => l.length))), rowH = 26, W = 560, m = {l: 14 + maxLen * 6, r: 70, t: 4, b: 4}, H = m.t + m.b + rowH * values.length;
  const svg = svgIn(id, W, H, desc), max = Math.max(...values);
  values.forEach((v, i) => {
    const y = m.t + rowH * i + 4, h = rowH - 10, w = (W - m.l - m.r) * v / max;
    const lab = labels[i].length > 34 ? labels[i].slice(0, 33) + "…" : labels[i];
    txt(svg, m.l - 8, y + h / 2 + 4, lab, {"text-anchor": "end"});
    el("path", {d: endRounded(m.l, y, Math.max(w, 2), h, 4), class: "bar"}, svg);
    txt(svg, m.l + w + 6, y + h / 2 + 4, fmtVal(v), {class: "val"});
    const hit = el("rect", {x: 0, y: m.t + rowH * i, width: W, height: rowH, class: "hit"}, svg);
    hit.addEventListener("mousemove", e => showTip(e, fmtTip(i)));
    hit.addEventListener("mouseleave", hideTip);
  });
}

// KPIs
const k = D.kpis;
document.getElementById("kpis").innerHTML = [
  [nf(k.transacciones), "Transacciones"],
  [nf(k.pct_aprobacion, 1) + " %", "Tasa de aprobación"],
  [fM(k.monto_aprobado), "Monto aprobado"],
  [nf(k.contracargos), "Contracargos"],
].map(([v, l]) => `<div class="card kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

const etq = D.mensual.map(r => MESES[r.mes - 1]);
barrasVerticales("c-monto", etq, D.mensual.map(r => r.monto_aprobado), v => nf(v / 1e6), i =>
  `<b>${etq[i]} ${D.mensual[i].anio}</b><br>${fM(D.mensual[i].monto_aprobado)}<br>${nf(D.mensual[i].transacciones)} transacciones`,
  "Monto aprobado por mes");
lineaSimple("c-aprob", etq, D.mensual.map(r => r.pct_aprobacion), v => nf(v) + " %", i =>
  `<b>${etq[i]} ${D.mensual[i].anio}</b><br>${nf(D.mensual[i].pct_aprobacion, 1)} % aprobadas`,
  "Tasa de aprobación por mes");
barrasHorizontales("c-region", D.region.map(r => r.region), D.region.map(r => r.monto_aprobado), fM, i =>
  `<b>${D.region[i].region}</b><br>${fM(D.region[i].monto_aprobado)}`, "Monto aprobado por región");
barrasHorizontales("c-top", D.top_comercios.map(r => r.nombre_fantasia), D.top_comercios.map(r => r.monto_aprobado), fM, i =>
  `<b>${D.top_comercios[i].nombre_fantasia}</b><br>${fM(D.top_comercios[i].monto_aprobado)}`, "Top 10 comercios");
barrasHorizontales("c-cc", D.contracargos.map(r => r.motivo), D.contracargos.map(r => r.cantidad), v => nf(v), i =>
  `<b>${D.contracargos[i].motivo}</b><br>${nf(D.contracargos[i].cantidad)} contracargos`, "Contracargos por motivo");

document.getElementById("tabla").innerHTML =
  "<tr><th>Mes</th><th>Transacciones</th><th>% aprobación</th><th>Monto aprobado</th></tr>" +
  D.mensual.map(r => `<tr><td>${MESES[r.mes - 1]} ${r.anio}</td><td>${nf(r.transacciones)}</td><td>${nf(r.pct_aprobacion, 1)}</td><td>${fM(r.monto_aprobado)}</td></tr>`).join("");
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
