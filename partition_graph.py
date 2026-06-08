"""
partition_graph.py
──────────────────
Partitions the enriched Barcelona road graph into 4 zones using K-Means
clustering, then saves one subgraph per zone and a gateways_map.csv.

Key improvements over the original:
  - Each zone subgraph includes a buffer of neighbouring nodes from adjacent
    zones (BUFFER_HOPS), so zones overlap at boundaries. This fills the
    geographic gaps that K-Means creates and ensures gateway nodes have
    connectivity on both sides of a zone boundary.
  - Each subgraph is then reduced to its largest strongly connected component
    (SCC), eliminating any remaining isolated nodes.
  - Gateway nodes outside their zone's main SCC are remapped to the nearest
    node inside it using a KDTree.

Usage:
    python partition_graph.py
"""

import json
import ast

import numpy as np
import pandas as pd
import networkx as nx
import osmnx as ox
from scipy.spatial import cKDTree
from sklearn.cluster import KMeans

# ── Configuration ─────────────────────────────────────────────────────────────

CENTER_LAT, CENTER_LON = 41.3870, 2.1680   # Plaza Catalunya
RADIUS      = 2000                          # metres
JSON_FILE   = 'barcelona_osm_tomtom_pollution.json'
N_ZONES     = 4

# Number of graph hops to expand each zone beyond its K-Means boundary.
# 2-3 hops ≈ 1-2 street blocks of overlap at zone edges — enough for
# connectivity without zones becoming too large.
BUFFER_HOPS = 3

# ── 1. Load OSM graph ─────────────────────────────────────────────────────────

print("[1] Loading OSM graph...")
G = ox.graph_from_point((CENTER_LAT, CENTER_LON), dist=RADIUS,
                        network_type='drive', simplify=True)
print(f"    {len(G.nodes)} nodes, {len(G.edges)} edges")

# ── 2. Load enriched JSON and merge attributes ────────────────────────────────

print("[2] Loading enriched JSON...")
with open(JSON_FILE, 'r') as f:
    data = json.load(f)

df_edges = pd.DataFrame([feat['properties'] for feat in data])

def parse_osm_id(osm_id_str):
    return ast.literal_eval(osm_id_str)

df_edges[['u', 'v', 'key']] = pd.DataFrame(
    df_edges['osm_id'].apply(parse_osm_id).tolist(),
    index=df_edges.index
)
df_edges = df_edges[['u', 'v', 'key', 'speed_kmh', 'no2', 'pm10',
                     'length_m', 'road_type', 'name', 'source']]

edges_gdf    = ox.graph_to_gdfs(G, nodes=False, edges=True).reset_index()
edges_merged = edges_gdf.merge(df_edges, on=['u', 'v', 'key'], how='left')
edges_merged = edges_merged.dropna(subset=['speed_kmh'])
print(f"    {len(edges_merged)} edges after merge")

# ── 3. Build enriched graph ───────────────────────────────────────────────────

print("[3] Building enriched graph...")
G_enriched = nx.MultiDiGraph()
for node, d in G.nodes(data=True):
    G_enriched.add_node(node, **d)
for _, row in edges_merged.iterrows():
    u, v, key = row['u'], row['v'], row['key']
    attrs = row.drop(['u', 'v', 'key', 'geometry']).to_dict()
    G_enriched.add_edge(u, v, key=key, **attrs)
G_enriched.graph = G.graph.copy()
print(f"    {len(G_enriched.nodes)} nodes, {len(G_enriched.edges)} edges")

# ── 4. Compute edge weights ───────────────────────────────────────────────────

print("[4] Computing edge weights...")
max_no2  = df_edges['no2'].max()
max_pm10 = df_edges['pm10'].max()

for u, v, key, d in G_enriched.edges(keys=True, data=True):
    length = d.get('length_m', 1.0)
    speed  = d.get('speed_kmh', 30)
    speed  = speed if speed and speed > 0 else 30
    d['travel_time']   = length / speed * 3.6
    d['norm_no2']      = d.get('no2',  0) / max_no2  if max_no2  > 0 else 0
    d['norm_pm10']     = d.get('pm10', 0) / max_pm10 if max_pm10 > 0 else 0
    d['cost_fast']     = d['travel_time']
    d['cost_balanced'] = 0.5 * d['travel_time'] + 0.25 * d['norm_no2'] + 0.25 * d['norm_pm10']
    d['cost_green']    = 0.2 * d['travel_time'] + 0.4  * d['norm_no2'] + 0.4  * d['norm_pm10']

# ── 5. K-Means zone assignment ────────────────────────────────────────────────

print("[5] Assigning zones with K-Means...")
nodes_df = ox.graph_to_gdfs(G_enriched, edges=False)
coords   = nodes_df[['y', 'x']].values
kmeans   = KMeans(n_clusters=N_ZONES, random_state=42, n_init=10).fit(coords)
nodes_df['zone'] = kmeans.labels_

for node_id, zone in nodes_df['zone'].items():
    G_enriched.nodes[node_id]['zone'] = zone

# ── 6. Build buffered zone subgraphs + extract SCC ───────────────────────────

print(f"[6] Building zone subgraphs with {BUFFER_HOPS}-hop buffer + SCC extraction...")

main_comp_nodes  = {}
main_comp_coords = {}
main_comp_trees  = {}

for i in range(N_ZONES):
    # Start with nodes assigned to this zone by K-Means
    core_nodes = {n for n, d in G_enriched.nodes(data=True) if d.get('zone') == i}

    # Expand by BUFFER_HOPS hops into neighbouring zones
    # ego_graph gives all nodes reachable within N hops from any seed node
    buffered_nodes = set(core_nodes)
    frontier = set(core_nodes)
    for hop in range(BUFFER_HOPS):
        next_frontier = set()
        for n in frontier:
            next_frontier.update(G_enriched.predecessors(n))
            next_frontier.update(G_enriched.successors(n))
        new_nodes   = next_frontier - buffered_nodes
        buffered_nodes.update(new_nodes)
        frontier    = new_nodes

    sub_G = G_enriched.subgraph(buffered_nodes).copy()

    # Extract largest SCC to guarantee full internal connectivity
    scc       = max(nx.strongly_connected_components(sub_G), key=len)
    sub_G_scc = sub_G.subgraph(scc).copy()

    print(f"    Zone {i}: {len(core_nodes)} core → {len(buffered_nodes)} buffered "
          f"→ {len(sub_G_scc.nodes)} after SCC")

    ox.save_graphml(sub_G_scc, filepath=f"subgraph_zone_{i}.graphml")

    node_list  = list(sub_G_scc.nodes())
    coords_arr = np.array([
        [float(sub_G_scc.nodes[n]['y']), float(sub_G_scc.nodes[n]['x'])]
        for n in node_list
    ])
    main_comp_nodes[i]  = node_list
    main_comp_coords[i] = coords_arr
    main_comp_trees[i]  = cKDTree(coords_arr)

# ── 7. Build gateways — remap broken nodes ───────────────────────────────────

print("[7] Building gateway map with SCC-aware remapping...")

raw_gateways = []
for u, v, k, d in G_enriched.edges(keys=True, data=True):
    zone_u = G_enriched.nodes[u].get('zone')
    zone_v = G_enriched.nodes[v].get('zone')
    if zone_u != zone_v:
        raw_gateways.append({
            'node_id':     u,
            'from_zone':   zone_u,
            'to_zone':     zone_v,
            'target_node': v,
        })

df_raw = pd.DataFrame(raw_gateways).drop_duplicates(subset=['node_id', 'to_zone'])

def remap_to_scc(node_id, zone_id):
    node_list = main_comp_nodes[zone_id]
    if node_id in node_list:
        return node_id
    node_data = G_enriched.nodes.get(node_id, {})
    if node_data:
        lat = float(node_data['y'])
        lon = float(node_data['x'])
    else:
        lat = main_comp_coords[zone_id][:, 0].mean()
        lon = main_comp_coords[zone_id][:, 1].mean()
    _, idx   = main_comp_trees[zone_id].query([lat, lon])
    remapped = node_list[idx]
    print(f"    [remap] Zone {zone_id}: {node_id} → {remapped}")
    return remapped

remapped_gateways = []
for _, row in df_raw.iterrows():
    from_z = int(row['from_zone'])
    to_z   = int(row['to_zone'])
    exit_node  = remap_to_scc(row['node_id'],     from_z)
    entry_node = remap_to_scc(row['target_node'], to_z)
    remapped_gateways.append({
        'node_id':     exit_node,
        'from_zone':   from_z,
        'to_zone':     to_z,
        'target_node': entry_node,
    })

df_gateways = (pd.DataFrame(remapped_gateways)
               .drop_duplicates(subset=['node_id', 'to_zone'])
               .reset_index(drop=True))

df_gateways.to_csv("gateways_map.csv", index=False)
print(f"    {len(df_gateways)} gateway entries saved → gateways_map.csv")

# ── 8. Summary ────────────────────────────────────────────────────────────────

print("\n[8] Zone connectivity summary:")
print(df_gateways.groupby(['from_zone', 'to_zone']).size()
      .reset_index(name='gateways').to_string(index=False))

print("\n[✓] Done. Next steps:")
print("    1. python enrich_graphs.py        (add air quality data)")
print("    2. python tomtom_zone_fetcher.py  (fetch traffic data)")
print("    3. Start edge servers and run central_test.py")
