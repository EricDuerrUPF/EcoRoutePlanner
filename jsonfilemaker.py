import osmnx as ox
import folium
import requests
import math
import random
import json
from shapely.geometry import Point, LineString

# ---------- CONFIGURATION ----------
API_KEY = 'a4Xcu605ECTspKiBCpGAWAq7m7ebkhtQ'   # Your TomTom API key
CENTER_LAT, CENTER_LON = 41.3870, 2.1680       # Plaza Catalunya
RADIUS = 2000                                   # meters – covers central Barcelona
MAX_POINTS = 100                                # stay under 2500/day
HOUR = 14                                       # 2 PM (simulation baseline)

# ---------- 0. HELPER: SAFELY EXTRACT ROAD TYPE ----------
def get_road_type(highway_tag):
    """
    Convert OSM highway tag (which may be a string, list, or None) to a clean string.
    """
    if highway_tag is None:
        return 'unclassified'
    if isinstance(highway_tag, list):
        # Take the first element if list is non-empty
        return highway_tag[0] if highway_tag else 'unclassified'
    if isinstance(highway_tag, str):
        return highway_tag
    return 'unclassified'   # fallback

# ---------- 1. FETCH OSM ROAD NETWORK ----------
print("📡 Downloading OSM road network (centered on Plaza Catalunya)...")
G = ox.graph_from_point((CENTER_LAT, CENTER_LON), dist=RADIUS,
                        network_type='drive', simplify=True)
nodes, edges = ox.graph_to_gdfs(G, nodes=True, edges=True)
print(f"   {len(edges)} road segments, {len(nodes)} intersections.")

# ---------- 2. PREPARE QUERY POINTS (midpoints of edges) ----------
edge_ids = list(edges.index)
if len(edge_ids) > MAX_POINTS:
    sampled_ids = random.sample(edge_ids, MAX_POINTS)
else:
    sampled_ids = edge_ids

print(f"\n🔎 Sampling {len(sampled_ids)} points for TomTom queries...")
query_points = []   # each: (lat, lon, edge_id)
for idx in sampled_ids:
    geom = edges.loc[idx].geometry
    if geom.geom_type == 'LineString':
        pt = geom.interpolate(0.5, normalized=True)   # midpoint
        query_points.append((pt.y, pt.x, idx))        # (lat, lon, edge_id)

# ---------- 3. TOMTOM POINT API HELPER ----------
def get_tomtom_speed(lat, lon):
    """Return current speed (km/h) for the road segment containing (lat,lon)."""
    url = f'https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json'
    params = {
        'point': f'{lat},{lon}',
        'key': API_KEY
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            seg = data.get('flowSegmentData', {})
            return seg.get('currentSpeed', None)
        else:
            print(f"   ⚠️  TomTom error {r.status_code} at ({lat:.4f},{lon:.4f})")
            return None
    except Exception as e:
        print(f"   ⚠️  Request failed: {e}")
        return None

# ---------- 4. QUERY AND STORE REAL SPEEDS ----------
real_speeds = {}   # edge_id -> speed (km/h)
print("\n🚗 Querying TomTom for real speeds...")
for lat, lon, eid in query_points:
    speed = get_tomtom_speed(lat, lon)
    if speed is not None and speed > 0:
        real_speeds[eid] = speed
    else:
        real_speeds[eid] = None

print(f"   Got {len([v for v in real_speeds.values() if v])} real speeds.")

# ---------- 5. SIMULATE TRAFFIC FOR ALL EDGES ----------
def simulate_speed(road_type, hour=14):
    """Return a plausible speed (km/h) based on road type and time."""
    base_speeds = {
        'motorway': 100,
        'trunk': 80,
        'primary': 60,
        'secondary': 50,
        'tertiary': 40,
        'residential': 30,
        'living_street': 20,
        'unclassified': 40,
        'service': 20
    }
    base = base_speeds.get(road_type, 40)
    if 7 <= hour <= 9 or 17 <= hour <= 19:
        factor = 0.6
    elif 10 <= hour <= 16:
        factor = 0.8
    else:
        factor = 1.0
    var = random.uniform(0.9, 1.1)
    return base * factor * var

# ---------- 6. BUILD FINAL DATASET ----------
features = []
for idx, row in edges.iterrows():
    if row.geometry.geom_type != 'LineString':
        continue
    coords = [(lon, lat) for lon, lat in row.geometry.coords]

    # Safely get road type
    raw_highway = row.get('highway', 'unclassified')
    road_type = get_road_type(raw_highway)

    if idx in real_speeds and real_speeds[idx]:
        speed = real_speeds[idx]
        source = 'real'
    else:
        speed = simulate_speed(road_type, HOUR)
        source = 'simulated'

    # Approximate length in meters (1 degree ~ 111 km)
    length_m = row.geometry.length * 111320

    features.append({
        'geometry': {
            'type': 'LineString',
            'coordinates': coords
        },
        'properties': {
            'osm_id': str(idx),
            'road_type': road_type,
            'name': row.get('name', ''),
            'length_m': round(length_m, 1),
            'speed_kmh': round(speed, 1),
            'source': source
        }
    })

print(f"\n✅ Dataset built: {len(features)} edges total")

# ---------- 7. SAVE TO JSON ----------
out_json = 'barcelona_osm_tomtom.json'
with open(out_json, 'w') as f:
    json.dump(features, f, indent=2)
print(f"   Saved to {out_json}")

# ---------- 8. CREATE FOLIUM MAP ----------
m = folium.Map(location=[CENTER_LAT, CENTER_LON], zoom_start=13, tiles='CartoDB positron')

def speed_color(speed):
    if speed < 20:
        return 'red'
    elif speed < 40:
        return 'orange'
    elif speed < 60:
        return 'yellow'
    else:
        return 'green'

for feat in features:
    props = feat['properties']
    coords = feat['geometry']['coordinates']
    folium_coords = [(lat, lon) for lon, lat in coords]

    folium.PolyLine(
        locations=folium_coords,
        color=speed_color(props['speed_kmh']),
        weight=3,
        opacity=0.8,
        popup=(f"<b>OSM road</b><br>"
               f"Name: {props['name']}<br>"
               f"Type: {props['road_type']}<br>"
               f"Speed: {props['speed_kmh']} km/h ({props['source']})"),
        tooltip="Click for details"
    ).add_to(m)

legend_html = '''
<div style="position: fixed; bottom: 50px; left: 50px; z-index:1000; background:white; padding:10px; border:2px solid grey; border-radius:5px;">
<p><strong>Traffic Speed</strong></p>
<p><span style="color:red;">▉</span> < 20 km/h</p>
<p><span style="color:orange;">▉</span> 20–40 km/h</p>
<p><span style="color:yellow;">▉</span> 40–60 km/h</p>
<p><span style="color:green;">▉</span> > 60 km/h</p>
</div>
'''
m.get_root().html.add_child(folium.Element(legend_html))

out_map = 'misc/barcelona_traffic_osm.html'
m.save(out_map)
print(f"   Map saved to {out_map} – open in your browser.")