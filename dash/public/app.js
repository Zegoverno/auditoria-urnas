// Auditoria das urnas 2026: mapa com drill-down Brasil → UF → município → local → seção.
"use strict";

const COR = { "13": [224, 57, 62], "22": [47, 111, 222], outros: [227, 163, 59] };
const HEX = { "13": "#e0393e", "22": "#2f6fde", outros: "#e3a33b", bn: "#8a8f98", abst: "#4a4f58" };
const C1 = ["13", "22", "70", "14", "55", "30"];
const UF_NOME = { AC: "Acre", AL: "Alagoas", AM: "Amazonas", AP: "Amapá", BA: "Bahia", CE: "Ceará",
  DF: "Distrito Federal", ES: "Espírito Santo", GO: "Goiás", MA: "Maranhão", MG: "Minas Gerais",
  MS: "Mato Grosso do Sul", MT: "Mato Grosso", PA: "Pará", PB: "Paraíba", PE: "Pernambuco", PI: "Piauí",
  PR: "Paraná", RJ: "Rio de Janeiro", RN: "Rio Grande do Norte", RO: "Rondônia", RR: "Roraima",
  RS: "Rio Grande do Sul", SC: "Santa Catarina", SE: "Sergipe", SP: "São Paulo", TO: "Tocantins", ZZ: "Exterior" };

const E = { modo: "vencedor", turno: 2, escala: "votos", nivel: "BR", uf: null, mun: null, local: null,
  ordem: { col: "aptos", desc: true } };
const D = { meta: null, uf: null, fluxo: null, geoUF: null, mun: {}, geoMun: {}, secoes: {} };

// ---------- util ----------
const fmt = n => (n ?? 0).toLocaleString("pt-BR");
const pct = (a, b, d = 1) => b ? (100 * a / b).toFixed(d).replace(".", ",") + "%" : "–";
const pp = x => (x > 0 ? "+" : "") + x.toFixed(1).replace(".", ",") + " p.p.";
const sinal = n => (n > 0 ? "+" : "") + fmt(n);
const json = async u => { const r = await fetch(u); if (!r.ok) throw new Error(u); return r.json(); };
const validos = t => t ? ["v13", "v22", "v70", "v14", "v55", "v30", "vout"].reduce((s, k) => s + (t[k] || 0), 0) : 0;
const soma = (lista, t) => {
  const o = {};
  for (const x of lista) { const v = x[t]; if (!v) continue;
    for (const k in v) if (typeof v[k] === "number") o[k] = (o[k] || 0) + v[k];
    if (v.log_ok === false) o.log_div = (o.log_div || 0) + 1;
    if (v.contingencia === true) o.contingencia = (o.contingencia || 0) + 1;
    if (v.log_ok !== undefined) o.secoes = (o.secoes || 0) + 1; }
  return o;
};
function vencedor(t) {
  if (!t) return null;
  let best = null, bv = -1;
  for (const k of C1.concat(["out"])) { const v = t["v" + k] || 0; if (v > bv) { bv = v; best = k; } }
  return best === "13" || best === "22" ? best : "outros";
}
function mistura(c, a) { // escurece rumo ao fundo conforme a intensidade a ∈ [0,1]
  const f = [28, 31, 38], k = 0.25 + 0.75 * Math.max(0, Math.min(1, a));
  return c.map((v, i) => Math.round(f[i] + (v - f[i]) * k));
}

// ---------- métricas por modo ----------
function corDe(reg) {
  const t1 = reg.t1, t2 = reg.t2;
  if (E.modo === "crescimento") {
    if (!t1 || !t2) return [60, 60, 60];
    const dl = t2.v13 - t1.v13, df = t2.v22 - t1.v22, base = Math.max(t2.aptos, 1);
    const d = (dl - df) / base;            // vantagem de crescimento, em fração dos aptos
    return mistura(d >= 0 ? COR["13"] : COR["22"], Math.abs(d) / ESCALA_CRESC);
  }
  const t = E.turno === 1 ? t1 : t2;
  if (!t) return [60, 60, 60];
  const w = vencedor(t), v = validos(t);
  const sorted = [t.v13, t.v22, t.v70, t.v14, t.v55, t.v30, t.vout].sort((a, b) => b - a);
  const margem = v ? (sorted[0] - sorted[1]) / v : 0;
  return mistura(COR[w], margem / 0.5);
}
let ESCALA_CRESC = 0.15;
function ajustaEscala(regs) { // 90º percentil de |Δ| entre as regiões visíveis: usa a faixa toda de cor
  const v = regs.filter(r => r.t1 && r.t2).map(r => Math.abs((r.t2.v13 - r.t1.v13) - (r.t2.v22 - r.t1.v22)) / Math.max(r.t2.aptos, 1))
    .sort((a, b) => a - b);
  ESCALA_CRESC = Math.max(v.length ? v[Math.floor(0.9 * (v.length - 1))] : 0.15, 0.005);
}
function legenda() {
  const el = document.getElementById("legenda");
  const p = x => (100 * x).toFixed(1).replace(".", ",") + "%";
  if (E.modo === "crescimento") {
    el.innerHTML = `<b>Quem mais cresceu do 1º para o 2º turno</b>
      <div class="barra" style="background:linear-gradient(90deg,${HEX["22"]},#1c1f26,${HEX["13"]})"></div>
      <div class="ext"><span>Flávio +${p(ESCALA_CRESC)}</span><span>empate</span><span>Lula +${p(ESCALA_CRESC)}</span></div>
      <div class="nota">(Δ votos Lula − Δ votos Flávio) ÷ aptos. Escala ajustada às regiões visíveis.</div>`;
  } else if (E.modo === "colunas") {
    el.innerHTML = `<b>${E.turno}º turno · ${E.escala === "pct" ? "% dos votos válidos" : "votos"}</b><br>
      <span style="color:${HEX["13"]}">■</span> Lula &nbsp; <span style="color:${HEX["22"]}">■</span> Flávio
      <div class="nota">Arraste com o botão direito (ou dois dedos) para inclinar.</div>`;
  } else {
    el.innerHTML = `<b>Vencedor · ${E.turno}º turno</b>
      <div class="barra" style="background:linear-gradient(90deg,${HEX["22"]},#1c1f26,${HEX["13"]})"></div>
      <div class="ext"><span>Flávio +50 p.p.</span><span>empate</span><span>Lula +50 p.p.</span></div>
      <div class="nota">Cor mais forte = margem maior sobre o 2º colocado.</div>
      ${E.turno === 1 ? `<div class="nota"><span style="color:${HEX.outros}">■</span> outro candidato</div>` : ""}`;
  }
}

// ---------- mapa ----------
const fundo = new deck.TileLayer({
  id: "fundo", data: "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
  minZoom: 0, maxZoom: 16, tileSize: 256,
  renderSubLayers: p => { const b = p.tile.boundingBox;
    return new deck.BitmapLayer(p, { data: null, image: p.data, bounds: [b[0][0], b[0][1], b[1][0], b[1][1]] }); },
});
const rotulos = new deck.TileLayer({
  id: "rotulos", data: "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
  minZoom: 0, maxZoom: 16, tileSize: 256,
  renderSubLayers: p => { const b = p.tile.boundingBox;
    return new deck.BitmapLayer(p, { data: null, image: p.data, bounds: [b[0][0], b[0][1], b[1][0], b[1][1]] }); },
});
const mapa = new deck.DeckGL({
  container: "mapa", controller: true,
  initialViewState: { longitude: -53, latitude: -14.5, zoom: 3.4, pitch: 0, bearing: 0 },
  getTooltip: ({ object }) => object && dica(object),
  onClick: ({ object }) => object && clique(object),
});
let vista = { longitude: -53, latitude: -14.5, zoom: 3.4 };

function voar(bbox, pitch) {
  const el = document.getElementById("mapa");
  const vp = new deck.WebMercatorViewport({ width: el.clientWidth, height: el.clientHeight });
  const { longitude, latitude, zoom } = vp.fitBounds(bbox, { padding: 40 });
  vista = { longitude, latitude, zoom: Math.min(zoom, 16) };
  mapa.setProps({ initialViewState: { ...vista, pitch: pitch ?? (E.modo === "colunas" ? 45 : 0), bearing: 0,
    transitionDuration: 700, transitionInterpolator: new deck.FlyToInterpolator() } });
}
function bboxDe(geo) {
  let a = [180, 90, -180, -90];
  const visita = c => typeof c[0] === "number"
    ? (a = [Math.min(a[0], c[0]), Math.min(a[1], c[1]), Math.max(a[2], c[0]), Math.max(a[3], c[1])])
    : c.forEach(visita);
  (geo.features || [geo]).forEach(f => visita(f.geometry.coordinates));
  return [[a[0], a[1]], [a[2], a[3]]];
}
function spanMetros(bbox) {
  const lat = (bbox[0][1] + bbox[1][1]) / 2;
  return Math.max((bbox[1][0] - bbox[0][0]) * 111320 * Math.cos(lat * Math.PI / 180), (bbox[1][1] - bbox[0][1]) * 110540);
}

// regiões do nível atual: [{id, nome, t1, t2, pos:[lon,lat], feature?}]
function regioes() {
  if (E.nivel === "BR") {
    return D.geoUF.features.map(f => ({ tipo: "uf", id: f.properties.uf, nome: UF_NOME[f.properties.uf],
      ...D.uf[f.properties.uf], feature: f, pos: centro(f) }));
  }
  if (E.nivel === "UF") {
    const ms = D.mun[E.uf], g = D.geoMun[E.uf];
    if (!g) return Object.entries(ms).map(([cd, m]) => ({ tipo: "mun", id: cd, ...m, pos: m.c }));
    return g.features.filter(f => ms[f.properties.tse]).map(f => {
      const m = ms[f.properties.tse]; return { tipo: "mun", id: String(f.properties.tse), ...m, feature: f, pos: m.c || centro(f) }; });
  }
  return locaisDe(E.mun).filter(l => l.lat && l.lon);
}
function centro(f) { const b = bboxDe(f); return [(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2]; }
function locaisDe(cd) {
  return (D.secoes[cd] || []).map(l => ({ tipo: "local", id: l.nr, nome: l.nome, bairro: l.bairro, lat: l.lat, lon: l.lon,
    pos: [l.lon, l.lat], secoes: l.secoes, t1: soma(l.secoes, "t1"), t2: soma(l.secoes, "t2") }));
}

function camadas() {
  const regs = regioes(), out = [fundo];
  ajustaEscala(regs);
  const poligonos = regs.filter(r => r.feature);
  const temPoligono = poligonos.length > 0;
  if (temPoligono) {
    out.push(new deck.GeoJsonLayer({
      id: "poligonos-" + E.nivel + E.uf, data: poligonos.map(r => ({ ...r.feature, reg: r })),
      pickable: E.modo !== "colunas", stroked: true, filled: true,
      getFillColor: f => E.modo === "colunas" ? [40, 44, 52, 160] : [...corDe(f.reg), 225],
      getLineColor: [15, 17, 21, 255], lineWidthMinPixels: E.nivel === "BR" ? 1 : 0.4,
      updateTriggers: { getFillColor: [E.modo, E.turno] },
    }));
  }
  if (E.nivel === "MUN" && D.geoMun[E.uf]) {  // contorno do município
    const f = D.geoMun[E.uf].features.find(x => String(x.properties.tse) === String(E.mun));
    if (f) out.push(new deck.GeoJsonLayer({ id: "contorno", data: [f], filled: false, stroked: true,
      getLineColor: [200, 200, 200, 180], lineWidthMinPixels: 1.5 }));
  }
  const span = spanMetros(bboxAtual()), raio = Math.max(span / (E.nivel === "MUN" ? 160 : 110), 25);
  if (E.modo === "colunas") {
    const t = "t" + E.turno, maxV = Math.max(1, ...regs.map(r => Math.max(r[t]?.v13 || 0, r[t]?.v22 || 0)));
    const alt = span * 0.35;
    const h = (r, k) => { const v = r[t] || {}; return E.escala === "pct" ? alt * (v[k] || 0) / Math.max(validos(v), 1)
      : alt * (v[k] || 0) / maxV; };
    const desloca = (p, d) => [p[0] + d * raio * 1.05 / (111320 * Math.cos(p[1] * Math.PI / 180)), p[1]];
    const dados = regs.filter(r => r.pos).flatMap(r => [
      { reg: r, k: "v13", pos: desloca(r.pos, -1) }, { reg: r, k: "v22", pos: desloca(r.pos, 1) }]);
    out.push(new deck.ColumnLayer({
      id: "colunas", data: dados, pickable: true, extruded: true, diskResolution: 12, radius: raio,
      getPosition: d => d.pos, getElevation: d => h(d.reg, d.k),
      getFillColor: d => d.k === "v13" ? COR["13"] : COR["22"],
      updateTriggers: { getElevation: [E.turno, E.escala] }, transitions: { getElevation: 500 },
    }));
  } else if (E.nivel === "MUN") {
    const maxA = Math.max(1, ...regs.map(r => r.t2?.aptos || 0));
    out.push(new deck.ScatterplotLayer({
      id: "locais", data: regs, pickable: true, stroked: true,
      getPosition: r => r.pos, getRadius: r => raio * (0.5 + 1.6 * Math.sqrt((r.t2?.aptos || 0) / maxA)),
      getFillColor: r => [...corDe(r), 235], getLineColor: [10, 10, 10, 200], lineWidthMinPixels: 0.5,
      updateTriggers: { getFillColor: [E.modo, E.turno] },
    }));
  }
  out.push(rotulos);
  return out;
}
let bboxCache = [[-74, -34], [-34, 5.5]];
function bboxAtual() { return bboxCache; }

function dica(o) {
  const r = o.reg || o.properties?.reg || o;
  const t1 = r.t1, t2 = r.t2;
  if (!t1 && !t2) return null;
  const linha = (t, n) => t ? `<div>${n}: <b style="color:${HEX["13"]}">Lula ${pct(t.v13, validos(t))}</b> ·
    <b style="color:${HEX["22"]}">Flávio ${pct(t.v22, validos(t))}</b></div>` : "";
  const nome = r.tipo === "local" ? `${r.nome}<div class="nota">${r.bairro || ""} · ${r.secoes.length} seções</div>` : r.nome;
  let extra = "";
  if (t1 && t2) extra = `<div class="nota">Δ Lula ${sinal(t2.v13 - t1.v13)} · Δ Flávio ${sinal(t2.v22 - t1.v22)}</div>`;
  return { html: `<b>${nome}</b>${linha(t1, "1º")}${linha(t2, "2º")}${extra}`,
    style: { background: "#171a21", color: "#e6e8ec", border: "1px solid #262a33", borderRadius: "8px", fontSize: "12px", padding: "8px" } };
}

function clique(o) {
  const r = o.reg || o.properties?.reg || o;
  if (r.tipo === "uf") irUF(r.id);
  else if (r.tipo === "mun") irMun(r.id);
  else if (r.tipo === "local") { E.local = r.id; painel(); }
}

// ---------- navegação ----------
async function carregando(p) {
  const el = document.getElementById("carregando"); el.hidden = false;
  try { return await p; } finally { el.hidden = true; }
}
async function irBR() {
  Object.assign(E, { nivel: "BR", uf: null, mun: null, local: null });
  bboxCache = [[-74, -34], [-34, 5.5]];
  voar(bboxCache); desenha();
}
async function irUF(uf) {
  if (uf === "ZZ") { Object.assign(E, { nivel: "UF", uf, mun: null, local: null });
    if (!D.mun.ZZ) D.mun.ZZ = await carregando(json("data/mun/ZZ.json")); desenha(); return; }
  Object.assign(E, { nivel: "UF", uf, mun: null, local: null });
  if (!D.mun[uf]) [D.mun[uf], D.geoMun[uf]] = await carregando(Promise.all([json(`data/mun/${uf}.json`), json(`data/geo/mun_${uf}.json`)]));
  bboxCache = bboxDe(D.geoMun[uf]); voar(bboxCache); desenha();
}
async function irMun(cd) {
  cd = String(cd);
  Object.assign(E, { nivel: "MUN", mun: cd, local: null });
  if (!D.secoes[cd]) D.secoes[cd] = await carregando(json(`data/secoes/${cd}.json`));
  const ls = D.secoes[cd].filter(l => l.lat && l.lon);
  if (ls.length) {
    const lo = ls.map(l => l.lon).sort((a, b) => a - b), la = ls.map(l => l.lat).sort((a, b) => a - b);
    const q = (a, p) => a[Math.min(a.length - 1, Math.max(0, Math.round(p * (a.length - 1))))];
    bboxCache = [[q(lo, .02) - .01, q(la, .02) - .01], [q(lo, .98) + .01, q(la, .98) + .01]];
    voar(bboxCache);
  }
  desenha();
}

function desenha() {
  mapa.setProps({ layers: camadas() });
  legenda(); trilha(); painel(); botoes();
}
function trilha() {
  const p = [`<a data-ir="BR">Brasil</a>`];
  if (E.uf) p.push(`<a data-ir="UF">${UF_NOME[E.uf]}</a>`);
  if (E.mun) p.push(`<a data-ir="MUN">${D.mun[E.uf][E.mun]?.nome || E.mun}</a>`);
  if (E.local != null) { const l = D.secoes[E.mun].find(x => x.nr === E.local); p.push(`<span>${l?.nome || "local"}</span>`); }
  const el = document.getElementById("trilha");
  el.innerHTML = p.join('<span class="sep">›</span>');
  el.querySelectorAll("a").forEach(a => a.onclick = () => {
    const n = a.dataset.ir; if (n === "BR") irBR(); else if (n === "UF") irUF(E.uf); else { E.local = null; irMun(E.mun); } });
}
function botoes() {
  document.querySelectorAll("#modos button").forEach(b => b.classList.toggle("on", b.dataset.modo === E.modo));
  document.querySelectorAll("#turnos button").forEach(b => { b.classList.toggle("on", +b.dataset.turno === E.turno);
    b.disabled = E.modo === "crescimento"; });
  document.getElementById("escala").hidden = E.modo !== "colunas";
  document.querySelectorAll("#escala button").forEach(b => b.classList.toggle("on", b.dataset.escala === E.escala));
}
document.querySelectorAll("#modos button").forEach(b => b.onclick = () => {
  E.modo = b.dataset.modo;
  mapa.setProps({ initialViewState: { ...vista, pitch: E.modo === "colunas" ? 45 : 0, bearing: 0, transitionDuration: 500 } });
  desenha(); });
document.querySelectorAll("#turnos button").forEach(b => b.onclick = () => { E.turno = +b.dataset.turno; desenha(); });
document.querySelectorAll("#escala button").forEach(b => b.onclick = () => { E.escala = b.dataset.escala; desenha(); });

// ---------- painel ----------
function entidade() {
  if (E.nivel === "BR") return { nome: "Brasil", ...D.meta.BR, fluxo: D.fluxo.BR };
  if (E.nivel === "UF") return { nome: UF_NOME[E.uf], ...D.uf[E.uf], fluxo: D.fluxo.uf[E.uf] };
  const m = D.mun[E.uf][E.mun];
  if (E.local == null) return { nome: m.nome, ...m, fluxo: D.fluxo.mun[E.mun] };
  const l = locaisDe(E.mun).find(x => x.id === E.local);
  return { nome: l.nome, sub: l.bairro, t1: l.t1, t2: l.t2, secoesLista: l.secoes };
}
function filhos() {
  if (E.nivel === "BR") return Object.entries(D.uf).map(([uf, v]) => ({ id: uf, nome: UF_NOME[uf], ...v, ir: () => irUF(uf) }));
  if (E.nivel === "UF") return Object.entries(D.mun[E.uf]).map(([cd, v]) => ({ id: cd, ...v, ir: () => irMun(cd) }));
  if (E.local == null) return locaisDe(E.mun).map(l => ({ ...l, ir: () => { E.local = l.id; trilha(); painel(); } }));
  return null;
}

function placar(t, titulo) {
  if (!t) return "";
  const v = validos(t);
  const c = (k, nome) => `<div class="cand" style="border-color:${HEX[k]}"><div class="nome">${nome}</div>
    <div class="pct">${pct(t["v" + k], v)}</div><div class="det">${fmt(t["v" + k])} votos</div></div>`;
  return `<h3>${titulo}</h3><div class="placar">${c("13", "Lula (PT)")}${c("22", "Flávio Bolsonaro (PL)")}</div>`;
}
function tabelaMetricas(t1, t2) {
  const linhas = [
    ["Eleitores aptos", t => t.aptos, "n"], ["Comparecimento", t => t.comp, "n", t => pct(t.comp, t.aptos)],
    ["Abstenção", t => t.aptos - t.comp, "n", t => pct(t.aptos - t.comp, t.aptos)],
    ["Lula", t => t.v13, "n", t => pct(t.v13, validos(t))], ["Flávio", t => t.v22, "n", t => pct(t.v22, validos(t))],
    ["Outros candidatos", t => t.v70 + t.v14 + t.v55 + t.v30 + t.vout, "n", t => pct(t.v70 + t.v14 + t.v55 + t.v30 + t.vout, validos(t))],
    ["Brancos", t => t.vbr, "n", t => pct(t.vbr, t.comp)], ["Nulos", t => t.vnu, "n", t => pct(t.vnu, t.comp)],
    ["Identificados pela digital", t => t.bio, "n", t => pct(t.bio, t.comp)],
    ["Liberados pelo mesário", t => t.man, "n", t => pct(t.man, t.comp, 2)],
    ["Sem biometria cadastrada", t => t.sem, "n", t => pct(t.sem, t.comp, 2)],
    ["Votos após 17h (Brasília)", t => t.apos17, "n", t => pct(t.apos17, t.comp, 2)],
  ];
  const cel = (t, f, g) => t ? `${fmt(f(t))}${g ? ` <span class="nota">${g(t)}</span>` : ""}` : "–";
  const delta = (f) => t1 && t2 ? `<span class="${f(t2) - f(t1) >= 0 ? "pos" : "neg"}">${sinal(f(t2) - f(t1))}</span>` : "";
  return `<table><tr><th></th><th>1º turno</th><th>2º turno</th><th>Δ</th></tr>${linhas.map(([n, f, , g]) =>
    `<tr><td>${n}</td><td>${cel(t1, f, g)}</td><td>${cel(t2, f, g)}</td><td>${delta(f)}</td></tr>`).join("")}</table>`;
}
function candidatos1(t) {
  if (!t) return "";
  const v = validos(t), nomes = D.meta.candidatos;
  const lista = C1.map(k => [nomes[k]?.nome || k, t["v" + k], HEX[k] || HEX.outros]).concat([["Outros", t.vout, HEX.bn]])
    .sort((a, b) => b[1] - a[1]);
  return `<h3>1º turno · todos os candidatos</h3><div class="cands1">${lista.map(([n, x, c]) =>
    `<div class="l"><span>${n}</span><div class="b" style="width:${(100 * x / Math.max(v, 1)).toFixed(1)}%;background:${c}"></div><span>${pct(x, v)}</span></div>`).join("")}</div>`;
}
function selos(e) {
  const t = e.t2 || e.t1 || {}, t1 = e.t1 || {};
  const div = (t.log_div || 0) + (t1.log_div || 0);
  return `<div><span class="selo">hashes conferidos com a lista assinada</span><span class="selo">RDV = BU</span>
    <span class="selo ${div ? "alerta" : ""}">log = comparecimento${div ? ` (exceto ${div} seç.)` : ""}</span>
    ${(t.contingencia || 0) + (t1.contingencia || 0) ? `<span class="selo alerta">${(t1.contingencia || 0) + (t.contingencia || 0)} urnas de contingência</span>` : ""}</div>`;
}

function painel() {
  const e = entidade(), el = document.getElementById("painel");
  const nSec = e.t2?.secoes || e.secoesLista?.length || "";
  let h = `<h2>${e.nome}</h2><div class="nota">${e.sub ? e.sub + " · " : ""}${nSec ? fmt(nSec) + " seções" : ""}</div>${selos(e)}`;
  h += placar(e.t2, "2º turno") + placar(e.t1, "1º turno");
  h += `<h3>Fluxo de votos do 1º para o 2º turno</h3><div id="sankey"></div>
        <div class="nota">Estimativa estatística (regressão ecológica por seção): o voto é secreto, ninguém sabe em quem
        cada eleitor votou nos dois turnos. Mostra a transição que melhor explica a mudança de cada seção.${e.fluxo ? ` Base: ${fmt(e.fluxo.secoes)} seções.` : ""}</div>`;
  h += `<h3>Métricas</h3>${tabelaMetricas(e.t1, e.t2)}`;
  h += candidatos1(e.t1);
  if (e.secoesLista) h += tabelaSecoes(e.secoesLista);
  const fs = filhos();
  if (fs) h += tabelaFilhos(fs);
  h += `<p class="nota">Fonte: arquivos das urnas publicados pelo TSE (BU, RDV, log, assinatura), verificados um a um.
        Municípios: código TSE → IBGE. Fuso de cada município inferido da hora de abertura das urnas.<br>Gerado em ${D.meta.gerado}.</p>`;
  el.innerHTML = h;
  sankey(e.fluxo);
  el.querySelectorAll("th[data-col]").forEach(th => th.onclick = () => {
    const c = th.dataset.col; E.ordem = { col: c, desc: E.ordem.col === c ? !E.ordem.desc : true }; painel(); });
  el.querySelectorAll("tr[data-i]").forEach(tr => tr.onclick = () => _filhos[+tr.dataset.i].ir());
}
let _filhos = [];
function tabelaFilhos(fs) {
  const col = {
    nome: r => r.nome, aptos: r => r.t2?.aptos || 0,
    lula: r => r.t2 ? r.t2.v13 / Math.max(validos(r.t2), 1) : 0,
    margem: r => r.t2 ? (r.t2.v13 - r.t2.v22) / Math.max(validos(r.t2), 1) : 0,
    dl: r => r.t1 && r.t2 ? (r.t2.v13 / Math.max(validos(r.t2), 1) - r.t1.v13 / Math.max(validos(r.t1), 1)) : 0,
    df: r => r.t1 && r.t2 ? (r.t2.v22 / Math.max(validos(r.t2), 1) - r.t1.v22 / Math.max(validos(r.t1), 1)) : 0,
    abst: r => r.t2 ? 1 - r.t2.comp / Math.max(r.t2.aptos, 1) : 0,
    man: r => r.t2 ? r.t2.man / Math.max(r.t2.comp, 1) : 0,
  };
  const f = col[E.ordem.col] || col.aptos;
  _filhos = fs.slice().sort((a, b) => { const x = f(a), y = f(b);
    return (typeof x === "string" ? x.localeCompare(y) : x - y) * (E.ordem.desc ? -1 : 1); }).slice(0, 400);
  const th = (c, n) => `<th data-col="${c}">${n}${E.ordem.col === c ? (E.ordem.desc ? " ▾" : " ▴") : ""}</th>`;
  const p = x => (100 * x).toFixed(1).replace(".", ",");
  const titulo = E.nivel === "BR" ? "Estados" : E.nivel === "UF" ? "Municípios" : "Locais de votação";
  return `<h3>${titulo} <span class="nota">(2º turno; Δ em p.p. dos válidos vs 1º turno)</span></h3><table>
    <tr>${th("nome", "Nome")}${th("aptos", "Aptos")}${th("lula", "Lula %")}${th("margem", "Margem")}${th("dl", "Δ Lula")}${th("df", "Δ Flávio")}${th("abst", "Abst.")}${th("man", "Mesário")}</tr>
    ${_filhos.map((r, i) => `<tr class="click" data-i="${i}"><td>${r.nome}${r.capital ? " ★" : ""}</td><td>${fmt(r.t2?.aptos)}</td>
      <td>${p(col.lula(r))}</td><td style="color:${col.margem(r) >= 0 ? HEX["13"] : HEX["22"]}">${p(Math.abs(col.margem(r)))}</td>
      <td>${p(col.dl(r))}</td><td>${p(col.df(r))}</td><td>${p(col.abst(r))}</td><td>${(100 * col.man(r)).toFixed(2).replace(".", ",")}</td></tr>`).join("")}
  </table>${fs.length > 400 ? `<div class="nota">Mostrando 400 de ${fmt(fs.length)}. Ordene pelas colunas.</div>` : ""}`;
}
function tabelaSecoes(ss) {
  const c = (t, k) => t ? fmt(t[k]) : "–";
  return `<h3>Seções deste local</h3><table><tr><th>Zona/Seção</th><th>Turno</th><th>Aptos</th><th>Comp.</th><th>Lula</th><th>Flávio</th><th>Br/Nu</th><th>Mesário</th><th>Após 17h</th></tr>
    ${ss.flatMap(s => [1, 2].map(n => { const t = s["t" + n]; return `<tr><td>${n === 1 ? `${s.zona}/${s.secao}` : ""}</td><td>${n}º</td>
      <td>${c(t, "aptos")}</td><td>${c(t, "comp")}</td><td>${c(t, "v13")}</td><td>${c(t, "v22")}</td>
      <td>${t ? fmt(t.vbr + t.vnu) : "–"}</td><td>${c(t, "man")}</td><td>${c(t, "apos17")}${t && t.log_ok === false ? ' <span class="selo alerta">log</span>' : ""}${t && t.contingencia ? ' <span class="selo alerta">cont.</span>' : ""}</td></tr>`; })).join("")}
  </table>`;
}

function sankey(fl) {
  const el = document.getElementById("sankey");
  if (!fl) { el.innerHTML = `<div class="nota">Poucas seções para estimar (mínimo de 30) ou exterior.</div>`; return; }
  const r1 = D.fluxo.rotulos_1, r2 = D.fluxo.rotulos_2;
  const cores = { Lula: HEX["13"], "Flávio": HEX["22"], Outros: HEX.outros, "Branco/Nulo": HEX.bn, "Abstenção": HEX.abst };
  const nodes = r1.map(n => ({ name: n, lado: 1 })).concat(r2.map(n => ({ name: n, lado: 2 })));
  const links = [];
  fl.f.forEach((linha, i) => linha.forEach((v, j) => { if (v > 0) links.push({ source: i, target: r1.length + j, value: v }); }));
  const W = el.clientWidth || 400, H = 300;
  const s = d3.sankey().nodeWidth(12).nodePadding(10).nodeSort(null).extent([[118, 6], [W - 118, H - 6]]);
  const g = s({ nodes: nodes.map(d => ({ ...d })), links: links.map(d => ({ ...d })) });
  const svg = d3.select(el).append("svg").attr("viewBox", `0 0 ${W} ${H}`).attr("height", H);
  svg.append("g").selectAll("path").data(g.links).join("path").attr("d", d3.sankeyLinkHorizontal())
    .attr("stroke", d => cores[d.source.name]).attr("stroke-opacity", .45).attr("fill", "none")
    .attr("stroke-width", d => Math.max(1, d.width))
    .append("title").text(d => `${d.source.name} (1º) → ${d.target.name} (2º): ${fmt(d.value)}`);
  svg.append("g").selectAll("rect").data(g.nodes).join("rect").attr("x", d => d.x0).attr("y", d => d.y0)
    .attr("width", d => d.x1 - d.x0).attr("height", d => Math.max(1, d.y1 - d.y0)).attr("fill", d => cores[d.name]);
  svg.append("g").selectAll("text").data(g.nodes).join("text")
    .attr("x", d => d.lado === 1 ? d.x0 - 6 : d.x1 + 6).attr("y", d => (d.y0 + d.y1) / 2).attr("dy", "0.35em")
    .attr("text-anchor", d => d.lado === 1 ? "end" : "start")
    .text(d => `${d.name} ${(d.value / 1e6 >= 1 ? (d.value / 1e6).toFixed(1).replace(".", ",") + " mi" : fmt(Math.round(d.value)))}`);
}

// ---------- início ----------
(async () => {
  [D.meta, D.uf, D.fluxo, D.geoUF] = await carregando(Promise.all(
    ["data/meta.json", "data/uf.json", "data/fluxo.json", "data/geo/uf.json"].map(json)));
  desenha();
})();
