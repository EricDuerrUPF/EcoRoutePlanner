"""
central_test.py
───────────────
Central orchestrator for the distributed Barcelona routing system.

- Requests path segments from each edge server
- Accumulates travel_time, distance, and pollution_cost across zones
- Generates an interactive HTML map with:
    · Toggle buttons to switch between fastest / greenest / balanced
    · Each route coloured by per-edge traffic level (green → red)
    · Stats panel showing distance, time, and AQ cost for the active route
"""

import os
import folium
import networkx as nx
import osmnx as ox
import pandas as pd
import requests

# ── Configuration ─────────────────────────────────────────────────────────────

EDGE_NODES = {
    0: "http://127.0.0.1:8000",
    1: "http://127.0.0.1:8001",
    2: "http://127.0.0.1:8002",
    3: "http://127.0.0.1:8003",
}

MODES   = ["fastest", "greenest", "balanced"]
COLOURS = {"fastest": "red", "greenest": "green", "balanced": "blue"}

# ── Zone connectivity ─────────────────────────────────────────────────────────

df_gateways = pd.read_csv("gateways_map.csv")

zone_graph = nx.Graph()
for _, row in df_gateways.iterrows():
    zone_graph.add_edge(int(row["from_zone"]), int(row["to_zone"]))

# ── API call ──────────────────────────────────────────────────────────────────

def ask_edge_api(zone_id: int, start_node: int, end_node: int, mode: str):
    """
    Calls /calcular_tramo on the given edge server.
    Returns (path, stats, edge_traffic) or (None, None, None) on error.
    """
    url     = f"{EDGE_NODES[zone_id]}/calcular_tramo"
    payload = {"start_node": int(start_node), "end_node": int(end_node), "mode": mode}
    print(f"  [→ Zone {zone_id}] {mode.upper()} {start_node} → {end_node}")

    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data["path"], data.get("stats", {}), data.get("edge_traffic", [])
        print(f"  [!] Zone {zone_id} error: {resp.json().get('detail')}")
        return None, None, None
    except Exception as e:
        print(f"  [!] Could not reach Zone {zone_id}: {e}")
        return None, None, None

# ── Orchestrator ──────────────────────────────────────────────────────────────

def get_distributed_route(origin_node, dest_node, origin_zone, dest_zone, mode):
    print(f"\n{'─'*55}")
    print(f"  Mode: {mode.upper()}  |  Z{origin_zone} → Z{dest_zone}")
    print(f"{'─'*55}")

    try:
        zone_path = nx.shortest_path(zone_graph, origin_zone, dest_zone)
    except nx.NetworkXNoPath:
        print(f"  [!] No zone-level path between Z{origin_zone} and Z{dest_zone}")
        return None, None, None

    full_path        = []
    all_edge_traffic = []   # accumulated across all segments
    total_stats      = {"distance_m": 0.0, "travel_time_s": 0.0,
                        "travel_time_min": 0.0, "pollution_cost": 0.0}

    current_start = origin_node

    for i in range(len(zone_path) - 1):
        curr_z = zone_path[i]
        next_z = zone_path[i + 1]

        gw = df_gateways[
            (df_gateways["from_zone"] == curr_z) &
            (df_gateways["to_zone"]   == next_z)
        ].iloc[0]

        exit_node  = int(gw["node_id"])
        entry_next = int(gw["target_node"])

        path, stats, edge_traffic = ask_edge_api(curr_z, current_start, exit_node, mode)
        if path is None:
            return None, None, None

        full_path.extend(path[:-1])
        all_edge_traffic.extend(edge_traffic[:-1] if edge_traffic else [])
        _add_stats(total_stats, stats)
        current_start = entry_next

    # Final segment
    path, stats, edge_traffic = ask_edge_api(dest_zone, current_start, dest_node, mode)
    if path is None:
        return None, None, None

    full_path.extend(path)
    all_edge_traffic.extend(edge_traffic or [])
    _add_stats(total_stats, stats)

    total_stats["travel_time_min"] = round(total_stats["travel_time_s"] / 60, 2)
    total_stats["distance_m"]      = round(total_stats["distance_m"], 1)
    total_stats["travel_time_s"]   = round(total_stats["travel_time_s"], 1)
    total_stats["pollution_cost"]  = round(total_stats["pollution_cost"], 2)

    print(f"  [✓] {len(full_path)} nodes | "
          f"{total_stats['distance_m']} m | "
          f"{total_stats['travel_time_min']} min | "
          f"AQ {total_stats['pollution_cost']}")

    return full_path, total_stats, all_edge_traffic


def _add_stats(total: dict, segment: dict):
    if not segment:
        return
    total["distance_m"]     += segment.get("distance_m",     0.0)
    total["travel_time_s"]  += segment.get("travel_time_s",  0.0)
    total["pollution_cost"] += segment.get("pollution_cost",  0.0)

# ── Traffic colour helper ─────────────────────────────────────────────────────

def traffic_colour(level: float) -> str:
    """Map traffic_level (0–1) to a hex colour, green → yellow → orange → red."""
    if level < 0.1:   return "#2ecc71"   # green   – free flow
    if level < 0.3:   return "#f1c40f"   # yellow  – light
    if level < 0.6:   return "#e67e22"   # orange  – moderate
    return                    "#e74c3c"  # red     – heavy

# ── Map generation ────────────────────────────────────────────────────────────

def build_map(results: dict, G_mapa, output="comparativa_rutas_tfg.html"):
    """
    results: {mode: {"path": [...], "stats": {...}, "edge_traffic": [...]}}

    Routes are drawn ONLY via JS (not Folium polylines) so toggling can
    cleanly remove all segments of the hidden modes.
    Folium provides only the base tile layer and start/end markers.
    """
    import json as _json

    first_mode  = next(iter(results))
    first_nodes = results[first_mode]["path"]
    n0          = _get_node(G_mapa, first_nodes[0])
    m           = folium.Map(location=[n0["y"], n0["x"]], zoom_start=15,
                             tiles="OpenStreetMap")

    # ── Start / end markers (Folium handles these fine) ────────────────────
    start_n = _get_node(G_mapa, first_nodes[0])
    end_n   = _get_node(G_mapa, first_nodes[-1])
    if start_n:
        folium.Marker([start_n["y"], start_n["x"]], popup="Start",
                      icon=folium.Icon(color="green", icon="play")).add_to(m)
    if end_n:
        folium.Marker([end_n["y"], end_n["x"]], popup="End",
                      icon=folium.Icon(color="red", icon="stop")).add_to(m)

    # ── Build route data for JS ────────────────────────────────────────────
    # No Folium polylines at all — JS draws everything from this JSON blob.
    js_data = {}
    for mode, data in results.items():
        path         = data["path"]
        edge_traffic = data["edge_traffic"]
        stats        = data["stats"]

        tl_lookup = {}
        for entry in edge_traffic:
            tl_lookup[(entry[0], entry[1])] = entry[2]
            tl_lookup[(entry[1], entry[0])] = entry[2]

        segments = []
        for i in range(len(path) - 1):
            u, v   = path[i], path[i + 1]
            tl     = tl_lookup.get((u, v), 0.0)
            n_u    = _get_node(G_mapa, u)
            n_v    = _get_node(G_mapa, v)
            if n_u and n_v:
                segments.append({
                    "coords": [[n_u["y"], n_u["x"]], [n_v["y"], n_v["x"]]],
                    "colour": traffic_colour(tl),
                    "tl":     round(tl, 3),
                })

        js_data[mode] = {"segments": segments, "stats": stats}

    routes_json = _json.dumps(js_data)

    # ── JS + UI panel — injected after Folium's own scripts ───────────────
    # The map variable name Folium generates is the id of the first map div.
    # We grab it reliably via the Leaflet internal registry instead.
    panel_html = f"""
    <div id="ctrl-panel" style="
        position:fixed; top:20px; right:20px; z-index:9999;
        background:white; padding:16px 20px; border-radius:10px;
        box-shadow:0 2px 12px rgba(0,0,0,0.2); min-width:230px;
        font-family:sans-serif; font-size:13px; color:#222;">

      <div style="font-weight:700;font-size:14px;margin-bottom:10px;">Route mode</div>

      <div style="display:flex;gap:6px;margin-bottom:14px;">
        <button id="btn-fastest"
          onclick="showRoute('fastest')"
          style="flex:1;padding:7px 4px;border-radius:6px;
                 border:2px solid #e74c3c;background:#e74c3c;
                 color:white;font-weight:700;cursor:pointer;font-size:12px;">
          Fastest
        </button>
        <button id="btn-greenest"
          onclick="showRoute('greenest')"
          style="flex:1;padding:7px 4px;border-radius:6px;
                 border:2px solid #27ae60;background:white;
                 color:#27ae60;font-weight:700;cursor:pointer;font-size:12px;">
          Greenest
        </button>
        <button id="btn-balanced"
          onclick="showRoute('balanced')"
          style="flex:1;padding:7px 4px;border-radius:6px;
                 border:2px solid #2980b9;background:white;
                 color:#2980b9;font-weight:700;cursor:pointer;font-size:12px;">
          Balanced
        </button>
      </div>

      <div style="border-top:1px solid #eee;padding-top:10px;margin-bottom:10px;">
        <div style="display:flex;justify-content:space-between;margin-bottom:5px;">
          <span style="color:#666;">Distance</span>
          <span id="stat-dist" style="font-weight:600;">—</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-bottom:5px;">
          <span style="color:#666;">Travel time</span>
          <span id="stat-time" style="font-weight:600;">—</span>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span style="color:#666;">AQ cost</span>
          <span id="stat-aq" style="font-weight:600;">—</span>
        </div>
      </div>

      <div style="border-top:1px solid #eee;padding-top:10px;font-size:11px;color:#888;">
        Traffic level
        <div style="display:flex;align-items:center;gap:6px;margin-top:5px;flex-wrap:wrap;">
          <span style="display:flex;align-items:center;gap:3px;">
            <span style="width:14px;height:8px;background:#2ecc71;border-radius:2px;display:inline-block;"></span>Free
          </span>
          <span style="display:flex;align-items:center;gap:3px;">
            <span style="width:14px;height:8px;background:#f1c40f;border-radius:2px;display:inline-block;"></span>Light
          </span>
          <span style="display:flex;align-items:center;gap:3px;">
            <span style="width:14px;height:8px;background:#e67e22;border-radius:2px;display:inline-block;"></span>Moderate
          </span>
          <span style="display:flex;align-items:center;gap:3px;">
            <span style="width:14px;height:8px;background:#e74c3c;border-radius:2px;display:inline-block;"></span>Heavy
          </span>
        </div>
      </div>
    </div>

    <script>
    var ROUTES       = {routes_json};
    var activeLayers = [];   // Leaflet polyline objects currently on the map
    var theMap       = null; // resolved once on first showRoute call

    function getMap() {{
      if (theMap) return theMap;
      // Leaflet stores every map instance in L.Map._instances (v1.x)
      // Fall back to scanning window for the folium-generated variable
      var instances = Object.values(L.Map._instances || {{}});
      if (instances.length) {{ theMap = instances[0]; return theMap; }}
      // last-resort: find the first property on window that looks like a map
      for (var k in window) {{
        try {{
          if (window[k] && window[k]._container && typeof window[k].addLayer === 'function') {{
            theMap = window[k]; return theMap;
          }}
        }} catch(e) {{}}
      }}
      return null;
    }}

    function showRoute(mode) {{
      var map = getMap();
      if (!map) {{ console.warn('Map not ready yet'); return; }}

      // 1. Remove every polyline from the previous selection
      activeLayers.forEach(function(pl) {{ pl.remove(); }});
      activeLayers = [];

      // 2. Draw the selected route edge by edge
      var route = ROUTES[mode];
      if (!route) return;
      route.segments.forEach(function(seg) {{
        var pl = L.polyline(seg.coords, {{
          color:   seg.colour,
          weight:  6,
          opacity: 0.9,
        }});
        pl.addTo(map);
        activeLayers.push(pl);
      }});

      // 3. Update stats panel
      var s = route.stats;
      document.getElementById('stat-dist').textContent =
        (s.distance_m / 1000).toFixed(2) + ' km';
      document.getElementById('stat-time').textContent =
        s.travel_time_min + ' min';
      document.getElementById('stat-aq').textContent =
        s.pollution_cost.toFixed(0);

      // 4. Update button active state
      var accent = {{fastest:'#e74c3c', greenest:'#27ae60', balanced:'#2980b9'}};
      ['fastest','greenest','balanced'].forEach(function(m) {{
        var btn = document.getElementById('btn-' + m);
        if (!btn) return;
        if (m === mode) {{
          btn.style.background = accent[m];
          btn.style.color      = 'white';
        }} else {{
          btn.style.background = 'white';
          btn.style.color      = accent[m];
        }}
      }});
    }}

    // Initialise after page load so Leaflet map is fully ready
    window.addEventListener('load', function() {{
      showRoute('fastest');
    }});
    </script>
    """

    m.get_root().html.add_child(folium.Element(panel_html))
    m.save(output)
    print(f"\n[✓] Map saved → {output}")


def _get_node(G, node_id):
    """Try int and str forms of node_id in G.nodes."""
    for key in (node_id, str(node_id), int(node_id) if str(node_id).isdigit() else None):
        if key is not None and key in G.nodes:
            return G.nodes[key]
    return None

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Route parameters — adjust as needed ───────────────────────────────
    ID_INICIO    = 1379023475#30243263
    ID_FIN       = 30243371 #30343284
    ORIGIN_ZONE  = 2
    DEST_ZONE    = 3

    print("=" * 55)
    print("  Distributed Route Planner — Barcelona")
    print("=" * 55)

    results = {}
    for mode in MODES:
        path, stats, edge_traffic = get_distributed_route(
            ID_INICIO, ID_FIN, ORIGIN_ZONE, DEST_ZONE, mode=mode
        )
        if path:
            results[mode] = {
                "path":         path,
                "stats":        stats,
                "edge_traffic": edge_traffic,
            }
            print(f"  Stats → dist: {stats['distance_m']} m | "
                  f"time: {stats['travel_time_min']} min | "
                  f"AQ: {stats['pollution_cost']}")

    if not results:
        print("\n[!] No routes generated. Check that all edge servers are running.")
    else:
        print(f"\n[*] Routes generated: {list(results.keys())}")
        print("[*] Loading Barcelona graph for map…")

        if os.path.exists("barcelona.graphml"):
            G_mapa = ox.load_graphml("barcelona.graphml")
        else:
            print("[*] Downloading OSM graph (first run only)…")
            G_mapa = ox.graph_from_place("Barcelona, Spain", network_type="drive")
            ox.save_graphml(G_mapa, "barcelona.graphml")

        build_map(results, G_mapa)

import csv

# 1. Define tus 10 rutas (debes buscar los Node IDs reales en tu barcelona.graphml)
# Formato: (Nombre, ID_Origen, ID_Destino, Zona_Origen, Zona_Destino)
rutas_test = [
    ("Espanya-Sagrada", 1379023475, 30243371, 2, 3), # Ejemplo
    ("Aragó-CarrerdeMaquinista", 30243263, 30343284, 0, 1),
    ("CarrerSevilla-Av.Diag", 30343644, 8338986129, 1, 0),
    ("BlasodeGaray-PassatgeMarimon", 30236718, 30248585, 2, 3),
    ("València- Av.Roma", 3047640929, 1121909742, 3, 2),
    ("Av.Drassanes-CarrerTapioles", 100409765, 13734642240, 1, 2),
    ("CarrerStCarles-CarrerGirona", 30343615, 1311619424, 1, 3),
    ("CarrerMuntaner-CarrerVilaJoiosa", 559026424, 30343595, 2, 1),
    ("CarrerTrilla-CarrerPadilla", 81321297, 30243065, 3, 0), #Ruta chula
    ("EmiliaLlorcaMartin-CarrerCastillejos", 30343643, 243339207, 1, 0)
]

resultados_finales = []

print("\n🚀 Iniciando batería de tests para la memoria del TFG...")

for nombre, start_node, end_node, z_orig, z_dest in rutas_test:
    for modo in ["fastest", "greenest", "balanced"]:
        path, stats, _ = get_distributed_route(start_node, end_node, z_orig, z_dest, mode=modo)
        
        if stats:
            resultados_finales.append({
                "Ruta": nombre,
                "Modo": modo,
                "Distancia_km": round(stats["distance_m"] / 1000, 2),
                "Tiempo_min": stats["travel_time_min"],
                "AQ_Cost": stats["pollution_cost"],
                "Zonas": f"Z{z_orig}->Z{z_dest}"
            })

# 2. Guardar en CSV
keys = resultados_finales[0].keys()
with open('estadisticas_rutas_tfg.csv', 'w', newline='') as f:
    dict_writer = csv.DictWriter(f, fieldnames=keys)
    dict_writer.writeheader()
    dict_writer.writerows(resultados_finales)

print("\n✅ ¡Tabla generada! Abre 'estadisticas_rutas_tfg.csv' para ver los datos.")