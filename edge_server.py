"""
edge_server.py
──────────────
FastAPI edge node server for one zone of the distributed routing system.

Weights per routing mode
────────────────────────
  fastest  → travel_time   (seconds, derived from TomTom traffic + road type)
  greenest → pollution_cost (length × air-quality penalty, from enrich_graphs.py)
  balanced → 0.5 × travel_time + 0.5 × pollution_cost

Start one server per zone, e.g.:
  ZONE_ID=0 uvicorn edge_server:app --port 8000
  ZONE_ID=1 uvicorn edge_server:app --port 8001 test
  ...
"""

import json
import math
import os
import random

import networkx as nx
import osmnx as ox
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from scipy.spatial import cKDTree
import numpy as np
from prometheus_fastapi_instrumentator import Instrumentator

# ── Configuration ─────────────────────────────────────────────────────────────

ZONE_ID     = os.getenv("ZONE_ID", "0")
GRAPH_FILE  = f"subgraph_zone_{ZONE_ID}_enriched.graphml"
TRAFFIC_FILE = f"traffic_zone_{ZONE_ID}.json"

# Snapping: max distance (degrees, ~333 m) to match a traffic segment to an edge.
# Empirically derived from zone 3 distance distribution (p75 ≈ 0.0029°).
# We index ALL segment vertices (not just midpoints) so short edges near the
# ends of long TomTom segments still get a match.
SNAP_RADIUS_DEG = 0.003

# Fallback speed for edges with no traffic match (small alleys, unmatched roads)
FALLBACK_SPEED_MS = 30 * 1000 / 3600   # 30 km/h in m/s

# Balanced mode blend ratio
BALANCED_TRAVEL_W    = 0.5
BALANCED_POLLUTION_W = 0.5

# ── App & globals ─────────────────────────────────────────────────────────────

app = FastAPI(title=f"Edge Node Server - Zona {ZONE_ID}")
Instrumentator().instrument(app).expose(app) #Automatic Instrumentation for Prometheus
G_local = None   # The zone graph, loaded once at startup

# ── Geometry helpers ──────────────────────────────────────────────────────────

def haversine_m(lon1, lat1, lon2, lat2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Traffic snapping ──────────────────────────────────────────────────────────

def build_traffic_index(traffic_file: str):
    """
    Loads traffic_zone_N.json and builds a KDTree over ALL segment vertices
    (not just midpoints). Each point in the tree stores a back-reference to
    its parent segment index, so a nearest-point query still returns the
    full segment dict.

    Indexing all vertices means short graph edges near the ends of long
    TomTom segments are still matched correctly.

    Returns
    -------
    tree        : cKDTree  (indexed by [lat, lon])
    point_to_seg: list[int] mapping tree point index → segment index
    segments    : list of segment dicts
    (None, [], []) if the file is missing or invalid.
    """
    if not os.path.exists(traffic_file):
        print(f"[!] Traffic file not found: {traffic_file}  — travel_time will use fallback.")
        return None, [], []

    with open(traffic_file) as f:
        segments = json.load(f)

    if not segments:
        return None, [], []

    all_points    = []   # [lat, lon] for every vertex of every segment
    point_to_seg  = []   # parallel list: which segment index owns this point

    for seg_idx, seg in enumerate(segments):
        coords = seg.get("geometry", [])
        if not coords:
            continue
        for c in coords:
            lon, lat = c[0], c[1]
            all_points.append([lat, lon])
            point_to_seg.append(seg_idx)

    if not all_points:
        return None, [], segments

    tree = cKDTree(np.array(all_points))
    print(f"[*] Traffic index built: {len(segments)} segments / "
          f"{len(all_points)} vertices from {traffic_file}")
    return tree, point_to_seg, segments


def snap_traffic_to_edges(G, tree, point_to_seg, segments):
    """
    For every edge in G, find the nearest traffic segment vertex within
    SNAP_RADIUS_DEG and write travel_time onto the edge.

    Falls back to  length / FALLBACK_SPEED_MS  when no match is found.
    Also recomputes balanced_weight using the new travel_time.
    """
    if tree is None:
        print("[!] No traffic index available. Using fallback travel_time for all edges.")
        for u, v, k, data in G.edges(keys=True, data=True):
            length = float(data.get("length", 1.0))
            data["travel_time"] = length / FALLBACK_SPEED_MS
            _recompute_balanced(data)
        return

    matched = 0
    fallback = 0

    for u, v, k, data in G.edges(keys=True, data=True):
        n_u = G.nodes[u]
        n_v = G.nodes[v]
        edge_lat = (float(n_u["y"]) + float(n_v["y"])) / 2
        edge_lon = (float(n_u["x"]) + float(n_v["x"])) / 2
        length   = float(data.get("length", 1.0))

        dist, pt_idx = tree.query([edge_lat, edge_lon])

        if dist <= SNAP_RADIUS_DEG:
            seg      = segments[point_to_seg[pt_idx]]
            seg_len  = float(seg.get("length_m", length))
            seg_tt   = float(seg.get("travel_time", length / FALLBACK_SPEED_MS))
            speed_ms = (seg_len / seg_tt) if seg_tt > 0 else FALLBACK_SPEED_MS
            speed_ms = max(speed_ms, 0.1)   # floor at 0.1 m/s

            data["travel_time"]   = length / speed_ms
            data["traffic_level"] = float(seg.get("traffic_level", 0.0))
            matched += 1
        else:
            data["travel_time"]   = length / FALLBACK_SPEED_MS
            data["traffic_level"] = 0.0
            fallback += 1

        _recompute_balanced(data)

    total = matched + fallback
    pct   = 100 * matched // total if total else 0
    print(f"[*] Traffic snapping complete: "
          f"{matched}/{total} edges matched ({pct}%), "
          f"{fallback} fallbacks.")


def _recompute_balanced(data: dict):
    """
    balanced_weight = 0.5 × travel_time + 0.5 × pollution_cost
    Both are length-derived so their scales are compatible.
    """
    tt = float(data.get("travel_time", 0.0))
    pc = float(data.get("pollution_cost", data.get("length", 1.0)))
    data["balanced_weight"] = BALANCED_TRAVEL_W * tt + BALANCED_POLLUTION_W * pc

# ── Graph loading & preparation ───────────────────────────────────────────────

@app.on_event("startup")
def load_graph():
    global G_local
    print(f"\n[*] Starting Edge Server — Zone {ZONE_ID}")

    if not os.path.exists(GRAPH_FILE):
        print(f"[!] ERROR: Graph file not found: {GRAPH_FILE}")
        return

    print(f"[*] Loading graph from {GRAPH_FILE} …")
    temp_G  = ox.load_graphml(GRAPH_FILE)
    G_local = nx.relabel_nodes(temp_G, str) #lambda x: str(x).strip()

    # ── 1. Normalise all edge attribute types ──────────────────────────────
    print("[*] Normalising edge attribute types …")
    for u, v, k, data in G_local.edges(keys=True, data=True):
        length = float(data.get("length", 1.0))
        data["length"] = length

        # pollution_cost (from enrich_graphs.py, may be missing)
        if "pollution_cost" in data:
            try:
                data["pollution_cost"] = float(data["pollution_cost"])
            except (ValueError, TypeError):
                data["pollution_cost"] = length * (1.0 + random.random())
        else:
            data["pollution_cost"] = length * (1.0 + random.random())

    # ── 2. Build traffic index and snap to edges ───────────────────────────
    print(f"[*] Loading TomTom traffic data from {TRAFFIC_FILE} …")
    tree, point_to_seg, segments = build_traffic_index(TRAFFIC_FILE)
    snap_traffic_to_edges(G_local, tree, point_to_seg, segments)

    # ── 3. Sanity check on a sample edge ──────────────────────────────────
    sample = list(G_local.edges(data=True))[0][2]
    print(
        f"[OK] Sample edge weights — "
        f"length: {sample['length']:.1f} m | "
        f"travel_time: {sample['travel_time']:.2f} s | "
        f"pollution_cost: {sample['pollution_cost']:.4f} | "
        f"balanced_weight: {sample['balanced_weight']:.4f}"
    )
    print(f"[+] Zone {ZONE_ID} ready.\n")

# ── Request model ─────────────────────────────────────────────────────────────

class RouteRequest(BaseModel):
    start_node: int
    end_node:   int
    mode:       str = "fastest"   # "fastest" | "greenest" | "balanced"

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/ping")
def ping():
    return {"status": "ok", "zona": ZONE_ID}


@app.post("/calcular_tramo")
def calcular_tramo(request: RouteRequest):
    if G_local is None:
        raise HTTPException(status_code=500, detail="Graph not loaded.")

    s_node = str(request.start_node)
    e_node = str(request.end_node)

    #Depuration log
    if s_node not in G_local.nodes:
        print(f"[!] ZONE ERROR {ZONE_ID}: Origin Node {s_node} NOT FOUND")
    if e_node not in G_local.nodes:
        print(f"[!] ZONE ERROR {ZONE_ID}: Destination Node {e_node} NOT FOUND")

    if s_node not in G_local.nodes or e_node not in G_local.nodes:
        # Using 422 to know that the error is of DATA (nodes), not URL
        raise HTTPException(status_code=422, 
                            detail=f"Nodes {s_node}-{e_node} do not exist in Zone {ZONE_ID}")
    
    
    MODE_TO_WEIGHT = {
        "fastest":  "travel_time",      # seconds  (TomTom-aware)
        "greenest": "pollution_cost",    # length × air-quality penalty
        "balanced": "balanced_weight",   # 0.5×travel_time + 0.5×pollution_cost
    }
    weight = MODE_TO_WEIGHT.get(request.mode.lower(), "travel_time")

    print(f"\n--- Zone {ZONE_ID} | mode: {request.mode.upper()} | weight: {weight} ---")

    if s_node not in G_local.nodes or e_node not in G_local.nodes:
        raise HTTPException(status_code=404,
                            detail=f"Node(s) not found in zone {ZONE_ID}.")

    if s_node == e_node:
        return {"status": "success", "path": [int(s_node)], "nodes_count": 1}

    try:
        path = nx.shortest_path(G_local,
                                source=s_node,
                                target=e_node,
                                weight=weight)
        path_ints = [int(n) for n in path]

        # ── Accumulate per-edge stats along the path ───────────────────────
        total_length       = 0.0
        total_travel_time  = 0.0
        total_pollution    = 0.0
        edge_traffic       = []   # [[node_a, node_b, traffic_level], ...]

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            # MultiDiGraph may have parallel edges — pick the one with min weight
            edges = G_local[u][v]
            best  = min(edges.values(), key=lambda d: d.get(weight, 0.0))

            total_length      += float(best.get("length",       0.0))
            total_travel_time += float(best.get("travel_time",  0.0))
            total_pollution   += float(best.get("pollution_cost", 0.0))
            edge_traffic.append([
                int(u), int(v),
                round(float(best.get("traffic_level", 0.0)), 4)
            ])

        stats = {
            "distance_m":      round(total_length,      1),
            "travel_time_s":   round(total_travel_time, 1),
            "travel_time_min": round(total_travel_time / 60, 2),
            "pollution_cost":  round(total_pollution,   2),
        }

        print(f"[OK] Path: {len(path_ints)} nodes | "
              f"{stats['distance_m']} m | "
              f"{stats['travel_time_min']} min | "
              f"AQ cost {stats['pollution_cost']}")

        return {
            "status":        "success",
            "zona":          ZONE_ID,
            "path":          path_ints,
            "nodes_count":   len(path_ints),
            "modo_aplicado": request.mode,
            "weight_used":   weight,
            "stats":         stats,
            "edge_traffic":  edge_traffic,
        }

    except nx.NetworkXNoPath:
        raise HTTPException(status_code=404,
                            detail="No physical path in this zone.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))