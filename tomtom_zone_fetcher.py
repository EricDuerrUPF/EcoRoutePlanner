"""
tomtom_zone_fetcher.py
─────────────────────
Fetches TomTom traffic flow tiles for each zone subgraph and saves
one clean traffic_zone_N.json per zone.

Each saved segment contains:
  - geometry      : list of [lon, lat] coordinates
  - road_type     : TomTom road type string
  - traffic_level : 0.0 (free) → 1.0 (fully congested)
  - free_flow_spd : km/h estimated from road type
  - current_speed : km/h after congestion penalty
  - travel_time   : seconds = (length_m / current_speed_ms)
  - length_m      : approximate haversine length of the segment

Usage
─────
  # Fetch all 4 zones (reads subgraph_zone_N_enriched.graphml from cwd):
  python tomtom_zone_fetcher.py

  # Fetch a single zone:
  python tomtom_zone_fetcher.py --zone 2
"""

import argparse
import json
import math
import os
from collections import defaultdict

import mapbox_vector_tile
import requests

try:
    import osmnx as ox
except ImportError:
    ox = None  # Will raise a clear error below if needed

# ── Configuration ────────────────────────────────────────────────────────────

API_KEY   = "a4Xcu605ECTspKiBCpGAWAq7m7ebkhtQ"   # TomTom key
ZOOM      = 12                                      # Tile zoom level (12 ≈ 150m tiles)
N_ZONES   = 4
GRAPH_DIR = "."                                     # Where the .graphml files live
OUTPUT_DIR = "."                                    # Where to write traffic_zone_N.json

# BPR-style free-flow speeds (km/h) keyed on TomTom road_type strings.
# Source: TomTom road classification + typical urban speed limits.
FREE_FLOW_SPEEDS = {
    "Motorway":         120,
    "International road": 100,
    "Major road":        80,
    "Secondary road":    60,
    "Connecting road":   50,
    "Major local road":  50,
    "Local road":        30,
    "Minor local road":  20,
}
DEFAULT_SPEED = 30  # km/h fallback for unknown road types

# Congestion model:  current_speed = free_flow × (1 − ALPHA × traffic_level)
# ALPHA=0.8 means traffic_level=1.0 reduces speed to 20 % of free-flow.
CONGESTION_ALPHA = 0.8

# ── Geometry helpers ──────────────────────────────────────────────────────────

def lat_lon_to_tile(lat_deg: float, lon_deg: float, zoom: int):
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_to_lon_lat(px: float, py: float, tile_x: int, tile_y: int,
                    zoom: int, extent: int = 4096):
    lon = (tile_x + px / extent) / (2 ** zoom) * 360.0 - 180.0
    n   = math.pi - 2 * math.pi * (tile_y + py / extent) / (2 ** zoom)
    lat = math.degrees(math.atan(0.5 * (math.exp(n) - math.exp(-n))))
    return lon, lat


def haversine_m(lon1, lat1, lon2, lat2) -> float:
    R = 6_371_000  # metres
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def segment_length_m(coords) -> float:
    """coords: list of [lon, lat]"""
    total = 0.0
    for i in range(len(coords) - 1):
        total += haversine_m(coords[i][0], coords[i][1],
                             coords[i+1][0], coords[i+1][1])
    return total

# ── Zone bounding box ─────────────────────────────────────────────────────────

def bbox_from_graphml(zone_id: int):
    """
    Returns (min_lat, min_lon, max_lat, max_lon) by reading node coordinates
    from the enriched graphml for the given zone.
    """
    if ox is None:
        raise ImportError("osmnx is required to derive bounding boxes from .graphml files. "
                          "Install it with:  pip install osmnx")

    path = os.path.join(GRAPH_DIR, f"subgraph_zone_{zone_id}_enriched.graphml")
    if not os.path.exists(path):
        # Fallback: try the un-enriched version
        path = os.path.join(GRAPH_DIR, f"subgraph_zone_{zone_id}.graphml")

    if not os.path.exists(path):
        raise FileNotFoundError(f"Graph file not found for zone {zone_id}: {path}")

    print(f"  [bbox] Loading {os.path.basename(path)} …")
    G = ox.load_graphml(path)

    lats = [data["y"] for _, data in G.nodes(data=True)]
    lons = [data["x"] for _, data in G.nodes(data=True)]

    # Add a small buffer (≈ 500 m) so border edges are covered
    BUFFER = 0.005  # degrees
    return (
        min(lats) - BUFFER,
        min(lons) - BUFFER,
        max(lats) + BUFFER,
        max(lons) + BUFFER,
    )


def tiles_for_bbox(min_lat, min_lon, max_lat, max_lon, zoom: int):
    """Return the set of (tile_x, tile_y) tuples that cover the bbox."""
    x0, y0 = lat_lon_to_tile(max_lat, min_lon, zoom)  # top-left  (y is inverted)
    x1, y1 = lat_lon_to_tile(min_lat, max_lon, zoom)  # bottom-right
    tiles = []
    for tx in range(x0, x1 + 1):
        for ty in range(y0, y1 + 1):
            tiles.append((tx, ty))
    return tiles

# ── Speed / time calculation ──────────────────────────────────────────────────

def estimate_travel_time(length_m: float, road_type: str,
                         traffic_level: float) -> dict:
    """
    Returns a dict with free_flow_spd, current_speed, travel_time.
    Uses BPR-style congestion:  v = v0 × (1 − α × tl)
    """
    v0  = FREE_FLOW_SPEEDS.get(road_type, DEFAULT_SPEED)          # km/h
    v   = v0 * (1.0 - CONGESTION_ALPHA * min(traffic_level, 1.0)) # km/h
    v   = max(v, 1.0)                                             # floor at 1 km/h
    v_ms = v * 1000 / 3600                                        # m/s
    tt   = length_m / v_ms if length_m > 0 else 0.0              # seconds

    return {
        "free_flow_spd": round(v0, 2),
        "current_speed": round(v,  2),
        "travel_time":   round(tt, 4),
    }

# ── TomTom tile fetching ──────────────────────────────────────────────────────

def fetch_tile(tile_x: int, tile_y: int) -> list:
    """
    Fetches one TomTom flow tile and returns a list of cleaned segment dicts.
    Returns [] on any error.
    """
    url = (
        f"https://api.tomtom.com/traffic/map/4/tile/flow/relative"
        f"/{ZOOM}/{tile_x}/{tile_y}.pbf?key={API_KEY}"
    )
    try:
        resp = requests.get(url, timeout=10)
    except requests.RequestException as e:
        print(f"    [WARN] Network error for tile ({tile_x},{tile_y}): {e}")
        return []

    if resp.status_code != 200:
        print(f"    [WARN] HTTP {resp.status_code} for tile ({tile_x},{tile_y})")
        return []

    try:
        tile_data = mapbox_vector_tile.decode(resp.content)
    except Exception as e:
        print(f"    [WARN] PBF decode error for tile ({tile_x},{tile_y}): {e}")
        return []

    if "Traffic flow" not in tile_data:
        return []

    segments = []
    for feature in tile_data["Traffic flow"]["features"]:
        props = feature.get("properties", {})
        geom  = feature.get("geometry", {})

        # Skip closed roads and zero-data segments
        if props.get("road_closure"):
            continue
        traffic_level = props.get("traffic_level")
        if traffic_level is None:
            continue

        road_type = props.get("road_type", "Unknown")

        # Convert tile-local pixel coords → WGS84
        raw_coords = []
        if geom["type"] == "LineString":
            raw_coords = geom["coordinates"]
        elif geom["type"] == "MultiLineString":
            for line in geom["coordinates"]:
                raw_coords.extend(line)

        if not raw_coords:
            continue

        coords = [
            list(tile_to_lon_lat(c[0], c[1], tile_x, tile_y, ZOOM))
            for c in raw_coords
        ]

        length_m = segment_length_m(coords)
        speed_info = estimate_travel_time(length_m, road_type, traffic_level)

        segments.append({
            "geometry":     coords,          # [[lon, lat], ...]
            "road_type":    road_type,
            "traffic_level": round(float(traffic_level), 6),
            "length_m":     round(length_m, 2),
            **speed_info,                    # free_flow_spd, current_speed, travel_time
        })

    return segments

# ── Per-zone orchestration ────────────────────────────────────────────────────

def fetch_zone(zone_id: int) -> list:
    print(f"\n[Zone {zone_id}] Deriving bounding box …")
    min_lat, min_lon, max_lat, max_lon = bbox_from_graphml(zone_id)
    print(f"  bbox → lat [{min_lat:.4f}, {max_lat:.4f}]  "
          f"lon [{min_lon:.4f}, {max_lon:.4f}]")

    tiles = tiles_for_bbox(min_lat, min_lon, max_lat, max_lon, ZOOM)
    print(f"  Tiles to fetch: {len(tiles)}")

    all_segments = []
    type_counts  = defaultdict(int)

    for tile_x, tile_y in tiles:
        segs = fetch_tile(tile_x, tile_y)
        for s in segs:
            type_counts[s["road_type"]] += 1
        all_segments.extend(segs)

    print(f"  Segments collected: {len(all_segments)}")
    print(f"  Road type breakdown:")
    for rt, cnt in sorted(type_counts.items()):
        print(f"    {rt}: {cnt}")

    return all_segments


def save_zone(zone_id: int, segments: list):
    out_path = os.path.join(OUTPUT_DIR, f"traffic_zone_{zone_id}.json")
    with open(out_path, "w") as f:
        json.dump(segments, f, indent=2)
    print(f"  [OK] Saved → {out_path}  ({len(segments)} segments)")

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch TomTom traffic data per zone.")
    parser.add_argument("--zone", type=int, default=None,
                        help="Fetch a single zone (0-3). Omit to fetch all.")
    args = parser.parse_args()

    zones = [args.zone] if args.zone is not None else list(range(N_ZONES))

    print("=" * 60)
    print(f"TomTom Zone Fetcher  |  zoom={ZOOM}  |  zones={zones}")
    print("=" * 60)

    for z in zones:
        try:
            segments = fetch_zone(z)
            save_zone(z, segments)
        except FileNotFoundError as e:
            print(f"[ERROR] {e}  — skipping zone {z}")
        except Exception as e:
            print(f"[ERROR] Unexpected error for zone {z}: {e}")
            raise

    print("\n[DONE] All requested zones fetched.")


if __name__ == "__main__":
    main()
