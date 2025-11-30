#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GEXF -> HTML interativo (D3.js + Tabulator) para a rede de coautoria entre PPGs (Fiocruz)

- Lê o arquivo .gexf informado na linha de comando.
- Calcula layout com spring_layout (seed=42, k=0.5, iterations=400, scale=0.7) e grava x,y nos nós.
- Cor do vértice: proporcional a "Conexões" (menor→maior) via paleta de 10 cores.
- Tamanho do vértice: constante para todos.
- Gera um arquivo .html (mesmo prefixo do .gexf) com:
  * Grafo D3 (zoom/drag, busca, ocultar/mostrar rótulos, limpar seleção)
  * Tabela Tabulator de vértices (id, label, Publicações_totais, Publicações_em_coautoria, Conexões, Proporção_da_coautoria_Fiocruz, cor)
  * Tabela Tabulator de arestas (par label-label, peso, nº de vizinhos em comum)

Requisitos: networkx (>=2.8)
"""

import argparse
import json
import os
import sys
import math
import html
import networkx as nx

PALETTE = [
    "#5e4fa2", "#3288bd", "#66c2a5", "#abdda4", "#e6f598",
    "#fee08b", "#fdae61", "#f46d43", "#d53e4f", "#9e0142"
]

def parse_args():
    ap = argparse.ArgumentParser(description="GEXF -> HTML interativo (D3 + Tabulator) para rede de coautoria PPGs Fiocruz")
    ap.add_argument("gexf_path", help="Caminho do arquivo .gexf de entrada")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--k", type=float, default=0.5)
    ap.add_argument("--iterations", type=int, default=400)
    ap.add_argument("--scale", type=float, default=0.7)
    ap.add_argument("--node_radius", type=float, default=8.0, help="Raio (constante) dos nós no D3")
    return ap.parse_args()

def linear_color(value, vmin, vmax, palette):
    if value is None or math.isnan(float(value)):
        return palette[0]
    if vmax <= vmin:
        return palette[0]
    t = (float(value) - float(vmin)) / (float(vmax) - float(vmin))
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    idx = int(round(t * (len(palette) - 1)))
    if idx < 0: idx = 0
    if idx >= len(palette): idx = len(palette) - 1
    return palette[idx]

def build_graph(gexf_path, seed, k, iterations, scale):
    # Lê o grafo (NetworkX garante leitura de atributos do GEXF)
    G = nx.read_gexf(gexf_path)

    # Se não houver posições, calcula com spring_layout
    #pos = nx.spring_layout(G, seed=seed, k=k, iterations=iterations, scale=scale)
    pos = nx.kamada_kawai_layout(G, scale=1.0)

    # Extrai "Conexões" (fall-back para grau de grafo se não existir)
    conexoes_values = []
    for n in G.nodes():
        conex = None
        data = G.nodes[n]
        # ‘Conexões’ pode vir como string; padroniza para int
        if "Conexões" in data:
            try:
                conex = int(data["Conexões"])
            except Exception:
                try:
                    conex = int(float(str(data["Conexões"]).replace(",", ".")))
                except Exception:
                    conex = None
        if conex is None:
            conex = int(G.degree[n])  # fallback
            G.nodes[n]["Conexões"] = conex
        conexoes_values.append(conex)

    vmin = min(conexoes_values) if conexoes_values else 0
    vmax = max(conexoes_values) if conexoes_values else 1

    # Atribui x,y e cor (de acordo com Conexões)
    for n, (x, y) in pos.items():
        G.nodes[n]["x"] = float(x)
        G.nodes[n]["y"] = float(y)
        conex = G.nodes[n].get("Conexões", 0)
        G.nodes[n]["color"] = linear_color(conex, vmin, vmax, PALETTE)

    # Peso da aresta (fallback = 1)
    for u, v, d in G.edges(data=True):
        w = d.get("weight", 1)
        try:
            w = float(str(w).replace(",", "."))
        except Exception:
            w = 1.0
        d["weight"] = w

    return G

def compute_common_neighbors_table(G, id_to_label):
    """
    Retorna lista de linhas para a tabela de arestas:
    { "aresta": "LabelA, LabelB", "colabs": weight, "comuns": n_comum }
    (par único por ordem alfabética de ID)
    """
    # vizinhanças por nó (IDs em string)
    neigh = {str(n): set(map(str, G.neighbors(n))) for n in G.nodes()}
    seen = set()
    rows = []
    for u, v, d in G.edges(data=True):
        a, b = sorted([str(u), str(v)])
        key = a + "||" + b
        if key in seen:
            continue
        seen.add(key)
        w = float(d.get("weight", 1) or 1)
        comuns = len((neigh.get(a, set()) & neigh.get(b, set())) - {a, b})
        la = id_to_label.get(a, a)
        lb = id_to_label.get(b, b)
        aresta_str = ", ".join(sorted([la, lb]))
        rows.append({
            "aresta": aresta_str,
            "colabs": w,
            "comuns": comuns
        })
    # ordena: colabs desc, comuns desc, aresta asc
    rows.sort(key=lambda r: (-r["colabs"], -r["comuns"], r["aresta"]))
    return rows

def graph_to_embeddable_json(G, node_radius_const):
    """
    Constrói o objeto (dict) { nodes: [...], links: [...] } apropriado para o D3,
    *mantendo* x,y, cor e raio constante.
    Também prepara datasets das tabelas (vértices/arestas).
    """
    nodes = []
    id_to_label = {}
    for n, data in G.nodes(data=True):
        nid = str(n)
        label = str(data.get("label", nid))
        id_to_label[nid] = label

    for n, data in G.nodes(data=True):
        nid = str(n)
        label = str(data.get("label", nid))

        # atributos esperados do GEXF
        pub_tot = data.get("Publicações_totais", 0)
        pub_coa = data.get("Publicações_em_coautoria", 0)
        conex   = data.get("Conexões", 0)
        prop    = data.get("Proporção_da_coautoria_Fiocruz", None)

        # normalizações de tipo
        def to_int(x):
            try: return int(x)
            except: 
                try: return int(float(str(x).replace(",", ".")))
                except: return 0
        def to_float(x):
            try: return float(str(x).replace(",", "."))
            except: return None

        pub_tot = to_int(pub_tot)
        pub_coa = to_int(pub_coa)
        conex   = to_int(conex)
        prop    = to_float(prop)

        x = float(data.get("x", 0.0))
        y = float(data.get("y", 0.0))
        color = data.get("color", "#1f77b4")

        nodes.append({
            "id": nid,
            "label": label,
            "x": x, "y": y,
            "fx": None, "fy": None,  # livres para drag no front
            "r": float(node_radius_const),
            "color": color,
            # campos para a Tabela de Vértices:
            "Publicações_totais": pub_tot,
            "Publicações_em_coautoria": pub_coa,
            "Conexões": conex,
            "Proporção_da_coautoria_Fiocruz": prop,
        })

    links = []
    for u, v, d in G.edges(data=True):
        links.append({
            "source": str(u),
            "target": str(v),
            "weight": float(d.get("weight", 1) or 1),
            "color": d.get("color", "#aaa")
        })

    # TABELA DE ARESTAS (pares únicos + vizinhos em comum)
    edge_rows = compute_common_neighbors_table(G, id_to_label)

    # TABELA DE VÉRTICES (campos que vamos exibir)
    vertex_rows = []
    for n in nodes:
        vertex_rows.append({
            "id": n["id"],
            "nome": n["label"],
            "Publicações_totais": n["Publicações_totais"],
            "Publicações_em_coautoria": n["Publicações_em_coautoria"],
            "Conexões": n["Conexões"],
            "Proporção_da_coautoria_Fiocruz": n["Proporção_da_coautoria_Fiocruz"],
            "cor": n["color"]
        })

    return {
        "graph": {"nodes": nodes, "links": links},
        "vertex_table": vertex_rows,
        "edge_table": edge_rows
    }

def html_template(graph_json_str, vertex_table_str, edge_table_str):
    D3_CDN = "d3.min.js"
    TABULATOR_CSS = "tabulator_site.min.css"
    TABULATOR_JS  = "tabulator.min.js"
    XLSX_JS       = "xlsx.full.min.js"

    # Usar placeholders únicos (sem chaves) para evitar conflitos:
    html_str = """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>Rede de Coautoria entre PPGs (Fiocruz)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<link rel="stylesheet" href::__TABULATOR_CSS__>
<style>
  body { margin: 16px; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, Ubuntu, sans-serif; color:#111; }
  h1,h2,h3,h4 { margin: 0.2rem 0 0.7rem; }
  .viz-wrap { display:flex; gap:1rem; align-items: stretch; }
  #viz-container { flex:3; min-height: 620px; height: 70vh; border:1px solid #ccc; position:relative; }
  #viz { width:100%; height:100%; display:block; }
  #info-pane { flex:1; max-height: 70vh; overflow:auto; border:1px solid #ddd; padding:0.75rem; background:#fafafa; }
  .toolbar { display:flex; gap:0.5rem; align-items:center; margin-bottom:0.5rem; flex-wrap: wrap; }
  .toolbar input[type="text"] { flex:1; min-width: 220px; padding:0.5rem; border:1px solid #ccc; border-radius:8px; }
  .toolbar button { padding:0.5rem 0.75rem; border:1px solid #ccc; border-radius:8px; background:#fff; cursor:pointer; }
  .legend { display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; font-size:13px; }
  .legend .item { display:inline-flex; align-items:center; gap:.35rem; margin-right:.75rem; }
  .legend .swatch { width:14px; height:14px; display:inline-block; border-radius:2px; border:1px solid rgba(0,0,0,.2); }
  .node circle { cursor:grab; stroke:#333; stroke-width:0.17px; }
  .node:active circle { cursor:grabbing; }
  .node.selected circle { stroke:#111; stroke-width:1px; }
  .dimmed { opacity:0.15; }
  .label { font: 10px/1.2 system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, sans-serif; pointer-events:none; }
  .edge { stroke:#aaa; stroke-opacity:0.85; fill:none; }
  .edge.highlight { stroke:#333; }
  .edge-label { font-size: 9px; line-height: 1; fill:#555; }
  .hidden { display:none !important; }

  .sl-controls { margin: 0.25rem 0 0.5rem; display:flex; gap:0.5rem; }
  #tabela-vertices .tabulator-cell, #tabela-arestas .tabulator-cell {
    white-space: nowrap; text-overflow: ellipsis; overflow: hidden;
  }
</style>
</head>
<body>

<h2>Rede de Coautoria entre PPGs (Fiocruz)</h2>
<p>
  Visualização interativa do grafo (nós = PPGs; arestas = colaborações). As cores dos nós são proporcionais ao número de <em>Conexões</em> (menor→maior), e a espessura das arestas ao <em>peso</em>.
</p>

<div class="viz-wrap">
  <div style="flex:3; display:flex; flex-direction:column;">
    <div class="toolbar">
      <div class="legend" id="legend"></div>
      <button id="btnToggleLabels" title="Mostrar/ocultar rótulos (vértices e arestas)">Ocultar rótulos</button>
      <input id="searchBox" type="text" placeholder="Buscar PPG (nome/ID)..." />
      <button id="btnSearch">Buscar</button>
      <button id="btnClear">Limpar seleção</button>
    </div>
    <div id="viz-container">
      <svg id="viz"></svg>
    </div>
  </div>
  <div id="info-pane">
    <h4>Informações do PPG</h4>
    <p>Clique em um nó (ou use a busca) para ver detalhes aqui.</p>
  </div>
</div>

<hr style="margin:1.25rem 0">

<h3>Vértices</h3>
<p>Tabela com atributos dos PPGs.</p>
<div class="sl-controls">
  <button id="vert-csv">⬇️ CSV</button>
  <button id="vert-xlsx">⬇️ XLSX</button>
</div>
<div id="tabela-vertices"></div>

<h3 style="margin-top:1.5rem;">Arestas (pares de PPGs)</h3>
<p>Veja os pares que mais colaboram e quantos vizinhos em comum possuem.</p>
<div class="sl-controls">
  <button id="edge-csv">⬇️ CSV</button>
  <button id="edge-xlsx">⬇️ XLSX</button>
</div>
<div id="tabela-arestas"></div>

<script src="__D3_CDN__"></script>
<script src="__TABULATOR_JS__"></script>
<script src="__XLSX_JS__"></script>

<script>
  // ===== Dados embutidos (gerados no Python) =====
  var GRAPH_DATA = __GRAPH_JSON__;
  var DATA_VERT  = __VERTEX_TABLE_JSON__;
  var DATA_EDGES = __EDGE_TABLE_JSON__;

  // ===== Render básico (usa x,y do Python; sem simulação) =====
  var svg = d3.select("#viz"),
      g = svg.append("g"),
      edgeLayer = g.append("g").attr("class","edges"),
      nodeLayer = g.append("g").attr("class","nodes"),
      labelLayer = g.append("g").attr("class","labels"),
      edgeLabelLayer = g.append("g").attr("class","edge-labels");

  var nodes = GRAPH_DATA.nodes.map(function(n){ return Object.assign({}, n); });
  var links = GRAPH_DATA.links.map(function(e){ return Object.assign({}, e); });

  var zoom = d3.zoom().scaleExtent([0.2, 8]).on("zoom", function(ev){ g.attr("transform", ev.transform); });
  svg.call(zoom);

  function getBox(){ return document.getElementById("viz-container").getBoundingClientRect(); }
  var box = getBox(), width = box.width, height = box.height;
  svg.attr("width", width).attr("height", height);

  var weights = links.map(function(d){ return +d.weight || 0; });
  var minW = d3.min(weights) || 0, maxW = d3.max(weights) || 1;
  var edgeWidth   = d3.scaleLinear().domain([minW, maxW]).range([1.2, 10]).clamp(true);
  var edgeOpacity = d3.scaleLinear().domain([minW, maxW]).range([0.56, 0.82]).clamp(true);

  var edge = edgeLayer.selectAll("path")
    .data(links)
    .join("path")
    .attr("class","edge")
    .attr("id", function(_,i){ return "e"+i; })
    .attr("stroke", function(d){ return d.color || "#aaa"; })
    .attr("stroke-width", function(d){ return edgeWidth(+d.weight || 0); })
    .attr("stroke-opacity", function(d){ return edgeOpacity(+d.weight || 0); })
    .attr("fill","none");

  var edgeLabels = edgeLabelLayer.selectAll("text")
    .data(links).join("text")
    .attr("class","edge-label")
    .append("textPath")
    .attr("href", function(_,i){ return "#e"+i; })
    .attr("startOffset","50%")
    .attr("text-anchor","middle")
    .text(function(d){ return d.weight ? String(d.weight) : ""; });

  var node = nodeLayer.selectAll("g.node")
    .data(nodes, function(d){ return d.id; })
    .join(function(enter){
      var g = enter.append("g").attr("class","node");
      g.append("circle")
        .attr("r", function(d){ return +d.r || 8; })
        .attr("fill", function(d){ return d.color || "#1f77b4"; });
      return g;
    });

  var labels = labelLayer.selectAll("text")
    .data(nodes, function(d){ return d.id; })
    .join("text")
    .attr("class","label")
    .attr("text-anchor","middle")
    .text(function(d){ return d.label; });

  function updatePositions(){
    edge.attr("d", function(d){
      var sx = (typeof d.source === 'object') ? d.source.x : (nodes.find(n=>n.id===String(d.source))||{x:0}).x;
      var sy = (typeof d.source === 'object') ? d.source.y : (nodes.find(n=>n.id===String(d.source))||{y:0}).y;
      var tx = (typeof d.target === 'object') ? d.target.x : (nodes.find(n=>n.id===String(d.target))||{x:0}).x;
      var ty = (typeof d.target === 'object') ? d.target.y : (nodes.find(n=>n.id===String(d.target))||{y:0}).y;
      return "M"+sx+","+sy+" L"+tx+","+ty;
    });
    node.attr("transform", function(d){ return "translate("+d.x+", "+d.y+")"; });
    labels.attr("x", function(d){ return d.x; }).attr("y", function(d){ return d.y - (d.r || 8) - 2; });
  }
  updatePositions();

  node.call(d3.drag()
    .on("start", function(ev, d){ d.fx = d.x; d.fy = d.y; })
    .on("drag",  function(ev, d){ d.x = ev.x; d.y = ev.y; updatePositions(); })
    .on("end",   function(ev, d){ d.fx = null; d.fy = null; })
  );

  var selectedId = null;
  function neighborSet(id){
    var set = new Set([id]);
    links.forEach(function(e){
      var s = (typeof e.source === 'object') ? e.source.id : String(e.source);
      var t = (typeof e.target === 'object') ? e.target.id : String(e.target);
      if (s === id) set.add(t);
      if (t === id) set.add(s);
    });
    return set;
  }

  function renderSelection(){
    if (!selectedId){
      node.classed("selected", false);
      node.classed("dimmed", false);
      labels.classed("dimmed", false);
      edge.classed("highlight", false).classed("dimmed", false);
      edgeLabels.classed("dimmed", false);
      return;
    }
    var keep = neighborSet(selectedId);
    node.classed("selected", function(d){ return d.id === selectedId; })
        .classed("dimmed", function(d){ return !keep.has(d.id); });
    labels.classed("dimmed", function(d){ return !keep.has(d.id); });
    edge.classed("highlight", function(d){
          var s = (typeof d.source === 'object') ? d.source.id : String(d.source);
          var t = (typeof d.target === 'object') ? d.target.id : String(d.target);
          return (s === selectedId || t === selectedId);
        })
        .classed("dimmed", function(d){
          var s = (typeof d.source === 'object') ? d.source.id : String(d.source);
          var t = (typeof d.target === 'object') ? d.target.id : String(d.target);
          return !(s === selectedId || t === selectedId);
        });
    edgeLabels.classed("dimmed", function(d){
      var s = (typeof d.source === 'object') ? d.source.id : String(d.source);
      var t = (typeof d.target === 'object') ? d.target.id : String(d.target);
      return !(s === selectedId || t === selectedId);
    });
  }

  function fillInfo(d){
    var pane = document.getElementById("info-pane");
    var campos = {
      "PPG": d.label,
      "ID": d.id,
      "Publicações totais": d.Publicações_totais,
      "Publicações em coautoria": d.Publicações_em_coautoria,
      "Conexões": d.Conexões,
      "Proporção da coautoria Fiocruz (%)": d.Proporção_da_coautoria_Fiocruz
    };
    var html = "<h4>"+(d.label || d.id)+"</h4><ul>";
    Object.keys(campos).forEach(function(k){
      var v = campos[k];
      if (v === undefined || v === null || v === "") return;
      html += "<li><strong>"+k+":</strong> "+v+"</li>";
    });
    html += "</ul>";
    pane.innerHTML = html;
  }

  function selectNodeById(id){
    var d = nodes.find(function(n){ return n.id === id; });
    if (!d) return;
    selectedId = d.id; fillInfo(d); renderSelection();
    var t = d3.zoomTransform(svg.node());
    var scale = Math.max(1.2, t.k);
    var x = width/2 - d.x * scale;
    var y = height/2 - d.y * scale;
    svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity.translate(x, y).scale(scale));
  }
  node.on("click", function(_, d){ selectNodeById(d.id); });

  function norm(s){
    return (s||"").toString().normalize("NFD").replace(/\\p{Diacritic}/gu,"").toLowerCase().trim();
  }
  function searchAndSelect(q){
    var nq = norm(q); if (!nq) return;
    var hit = nodes.find(function(n){ return norm(n.label) === nq || norm(n.id) === nq; });
    if (!hit) hit = nodes.find(function(n){ return norm(n.label).indexOf(nq) >= 0; });
    if (hit) selectNodeById(hit.id);
  }
  document.getElementById("btnSearch").addEventListener("click", function(){
    searchAndSelect(document.getElementById("searchBox").value);
  });
  document.getElementById("searchBox").addEventListener("keydown", function(ev){
    if (ev.key === "Enter") searchAndSelect(ev.target.value);
  });
  document.getElementById("btnClear").addEventListener("click", function(){
    selectedId = null; renderSelection();
    document.getElementById("info-pane").innerHTML =
      "<h4>Informações do PPG</h4><p>Clique em um nó (ou use a busca) para ver detalhes aqui.</p>";
  });

  var labelsVisible = true;
  var btnToggle = document.getElementById("btnToggleLabels");
  function applyLabelsVisibility(){
    labelLayer.classed("hidden", !labelsVisible);
    d3.select(".edge-labels").classed("hidden", !labelsVisible);
  }
  btnToggle.addEventListener("click", function(){
    labelsVisible = !labelsVisible;
    btnToggle.textContent = labelsVisible ? "Ocultar rótulos" : "Mostrar rótulos";
    applyLabelsVisibility();
  });
  applyLabelsVisibility();

  // ===== Legenda (por faixas lineares de Conexões) =====
  (function legend(){
    var vals = DATA_VERT.map(function(v){ return +v.Conexões || 0; });
    var vmin = d3.min(vals) || 0, vmax = d3.max(vals) || 1;
    var palette = __PALETTE_ARRAY__;
    var steps = palette.length, legendData = [];
    for (var i=0; i<steps; i++){
      var a = vmin + (i/steps)*(vmax - vmin);
      var b = vmin + ((i+1)/steps)*(vmax - vmin);
      legendData.push({ color: palette[i], range: (Math.round(a*100)/100) + " – " + (Math.round(b*100)/100) });
    }
    var L = d3.select("#legend");
    L.append("span").text("Cor por Conexões: ");
    L.selectAll("span.item")
      .data(legendData)
      .join("span")
      .attr("class","item")
      .html(function(d){ return '<span class="swatch" style="background:'+d.color+'"></span>'+d.range; });
  }());

  function maybeFit(){
    var xs = nodes.map(function(d){ return d.x; }), ys = nodes.map(function(d){ return d.y; });
    var minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
    var minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
    var w = Math.max(1, maxX - minX), h = Math.max(1, maxY - minY);
    var margin = 40;
    var kx = (width  - margin*2) / w;
    var ky = (height - margin*2) / h;
    var scale = Math.max(0.2, Math.min(3, Math.min(kx, ky)));
    var tx = (width  - scale*(minX + maxX)) / 2;
    var ty = (height - scale*(minY + maxY)) / 2;
    svg.transition().duration(600)
      .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }
  maybeFit();

  var ro = new ResizeObserver(function(){
    var b = getBox();
    width = b.width; height = b.height;
    svg.attr("width", width).attr("height", height);
    maybeFit();
  });
  ro.observe(document.getElementById("viz-container"));

  // ===== Tabelas (Tabulator) =====
  function heightFor(n){
    if (n <= 12) return "auto";
    if (n <= 60) return "40vh";
    return "60vh";
  }
  var HEIGHT_VERT = heightFor(DATA_VERT.length);
  var HEIGHT_EDG  = heightFor(DATA_EDGES.length);

  const LANG_PT = {
    "pt-br": {
      "pagination": {
        "first":"«","first_title":"Primeira",
        "last":"»","last_title":"Última",
        "prev":"‹","prev_title":"Anterior",
        "next":"›","next_title":"Próxima"
      },
      "headerFilters": { "default":"⎯ Filtrar ⎯" }
    }
  };

  function createTable(el, data, columns, initialSort, height){
    const base = {
      data,
      layout: "fitColumns",
      responsiveLayout: "collapse",
      pagination: "local",
      paginationSize: 50,
      paginationSizeSelector: [10, 25, 50, 100, 200, 500],
      movableColumns: true,
      initialSort,
      headerFilterLiveFilter: true,
      columns,
      langs: LANG_PT, locale: "pt-br",
      tooltips: true
    };
    if (height !== "auto") base.height = height;
    return new Tabulator(el, base);
  }

  (function VERTICES(){
    const columns = [
      { title:"ID", field:"id", headerFilter:"input", tooltip:true },
      { title:"Nome", field:"nome", headerFilter:"input", widthGrow:2, tooltip:true },
      { title:"Publicações totais", field:"Publicações_totais", hozAlign:"right", sorter:"number", headerFilter:"input", tooltip:true },
      { title:"Publicações em coautoria", field:"Publicações_em_coautoria", hozAlign:"right", sorter:"number", headerFilter:"input", tooltip:true },
      { title:"Conexões", field:"Conexões", hozAlign:"right", sorter:"number", headerFilter:"input", tooltip:true },
      { title:"Proporção da coautoria Fiocruz (%)", field:"Proporção_da_coautoria_Fiocruz", hozAlign:"right", sorter:"number", headerFilter:"input", tooltip:true },
      { title:"Cor", field:"cor", headerFilter:"input", tooltip:true }
    ];
    const sort = [{ column:"Conexões", dir:"desc" }];
    const t = createTable("#tabela-vertices", DATA_VERT, columns, sort, HEIGHT_VERT);
    document.getElementById("vert-csv").onclick  = () => t.download("csv",  "vertices.csv", { bom:true });
    document.getElementById("vert-xlsx").onclick = () => t.download("xlsx", "vertices.xlsx", { sheetName:"Vértices" });
  })();

  (function ARESTAS(){
    const columns = [
      { title:"Aresta", field:"aresta", headerFilter:"input", widthGrow:3, tooltip:true },
      { title:"Número de colaborações", field:"colabs", hozAlign:"right", sorter:"number", headerFilter:"input", tooltip:true },
      { title:"Nº de vizinhos em comum", field:"comuns", hozAlign:"right", sorter:"number", headerFilter:"input", tooltip:true }
    ];
    const sort = [{ column:"colabs", dir:"desc" }, { column:"comuns", dir:"desc" }];
    const t = createTable("#tabela-arestas", DATA_EDGES, columns, sort, HEIGHT_EDG);
    document.getElementById("edge-csv").onclick  = () => t.download("csv",  "arestas.csv", { bom:true });
    document.getElementById("edge-xlsx").onclick = () => t.download("xlsx", "arestas.xlsx", { sheetName:"Arestas" });
  })();
</script>

</body>
</html>
"""
    # Substituições finais
    html_str = (html_str
        .replace("__TABULATOR_CSS__", TABULATOR_CSS)
        .replace("__D3_CDN__", D3_CDN)
        .replace("__TABULATOR_JS__", TABULATOR_JS)
        .replace("__XLSX_JS__", XLSX_JS)
        .replace("__GRAPH_JSON__", graph_json_str)
        .replace("__VERTEX_TABLE_JSON__", vertex_table_str)
        .replace("__EDGE_TABLE_JSON__", edge_table_str)
        .replace("__PALETTE_ARRAY__", "[" + ",".join(json.dumps(c) for c in PALETTE) + "]")
    )
    return html_str


def main():
    args = parse_args()
    gexf_path = args.gexf_path
    if not os.path.isfile(gexf_path):
        print(f"Arquivo não encontrado: {gexf_path}", file=sys.stderr)
        sys.exit(1)

    out_html = os.path.splitext(gexf_path)[0] + ".html"

    G = build_graph(
        gexf_path,
        seed=args.seed,
        k=args.k,
        iterations=args.iterations,
        scale=args.scale
    )

    data = graph_to_embeddable_json(G, node_radius_const=args.node_radius)

    graph_json_str  = json.dumps(data["graph"], ensure_ascii=False)
    vertex_table_str = json.dumps(data["vertex_table"], ensure_ascii=False)
    edge_table_str   = json.dumps(data["edge_table"], ensure_ascii=False)

    html_str = html_template(graph_json_str, vertex_table_str, edge_table_str)

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html_str)

    print(f"Arquivo gerado: {out_html}")

if __name__ == "__main__":
    main()

