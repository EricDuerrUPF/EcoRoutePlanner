# Eco-Route Planner — Distributed Edge Computing System

A distributed routing system for Barcelona that calculates eco-friendly vehicle routes by combining real-time traffic data (TomTom API) and live air quality measurements (OpenData Barcelona). Built as a Bachelor's Thesis (TFG) at Universitat Pompeu Fabra.

The city is split into 4 geographic zones, each managed by an independent edge server. A central orchestrator decomposes cross-zone route requests, queries the relevant edge nodes, and stitches the results into a complete route with three modes: **Fastest**, **Greenest**, and **Balanced**.

---

## System Demonstration

The following figure showcases the final output of the proposed system, demonstrating the effective integration of real-time air quality data into the routing algorithm:

![Final system output](images/system_final_output.jpg)

---

## Full Pipeline

The system is built and run in the following order:

```
jsonfilemaker.py
    → barcelona_osm_tomtom.json
jsontodataframe.py
    → barcelona_osm_tomtom_pollution.json
partition_graph.py
    → subgraph_zone_{0..3}.graphml + gateways_map.csv
enrich_graphs.py
    → subgraph_zone_{0..3}_enriched.graphml
tomtom_zone_fetcher.py
    → traffic_zone_{0..3}.json
edge_server.py × 4   (one per zone, ports 8000–8003)
central_test.py      (orchestrator + map output)
```

---

## File Structure

### Core scripts

- **`jsonfilemaker.py`** — Downloads the Barcelona road network from OpenStreetMap and queries the TomTom Point Flow API for real-time speeds on a sample of edges. Produces `barcelona_osm_tomtom.json`.

- **`jsontodataframe.py`** — Merges the road JSON with NO₂ and PM10 pollution GeoPackages from OpenData Barcelona using spatial joins. Produces `barcelona_osm_tomtom_pollution.json`.

- **`partition_graph.py`** — Partitions the enriched road graph into 4 zones using K-Means clustering, expands each zone with a 3-hop buffer for boundary overlap, extracts the largest strongly connected component per zone, and remaps gateway nodes. Produces `subgraph_zone_N.graphml` and `gateways_map.csv`.

- **`enrich_graphs.py`** — Reads hourly air quality CSVs from OpenData Barcelona and injects `pollution_cost` onto every edge in each zone subgraph using KDTree spatial matching. Produces `subgraph_zone_N_enriched.graphml`.

- **`tomtom_zone_fetcher.py`** — Fetches real-time traffic flow tiles from the TomTom Flow Tiles API for each zone's bounding box. Estimates `travel_time` per segment using a BPR congestion model. Produces `traffic_zone_N.json`.

- **`edge_server.py`** — FastAPI edge node server, one instance per zone. At startup, loads the enriched GraphML, snaps TomTom traffic data to graph edges via KDTree, and computes three weight layers per edge. Exposes `/calcular_tramo` which runs Dijkstra and returns the path, statistics, and per-edge traffic levels.

- **`central_test.py`** — Central orchestrator. Resolves the zone path, queries the relevant edge servers in sequence, stitches path segments together, accumulates route statistics, and generates the interactive HTML comparison map.

- **`mapa_bueno_.py`** — Generates `mapa_zonas_reales.html`, an interactive map showing the convex hull of each zone's actual road nodes on a Barcelona base map.

### Generated data files

- **`gateways_map.csv`** — Directed gateway connections between zones. Each row: exit node in one zone → entry node in the adjacent zone. Used by the orchestrator to stitch cross-zone routes.
- **`subgraph_zone_N_enriched.graphml`** — Enriched road subgraph for zone N (0–3). Contains OSM topology plus air quality attributes on every edge.
- **`traffic_zone_N.json`** — TomTom traffic segments for zone N with `travel_time`, `current_speed`, and `traffic_level`.
- **`barcelona.graphml`** — Full Barcelona road graph used by the orchestrator to render the output map.

### Output files

- **`comparison_routes_tfg.html`** — Interactive Leaflet map. Toggle between Fastest / Greenest / Balanced routes, with per-edge traffic colouring and a stats panel.
- **`mapa_zonas_reales.html`** — Zone boundary visualisation.
- **`estadisticas_rutas_tfg.csv`** — Route statistics for all tested origin-destination pairs and modes.

### Data folder (`data/`)

Required input files — must be downloaded manually from OpenData Barcelona:

| File | Source |
|------|--------|
| `Qualitat_Aire_Detall.csv` | [OpenData BCN — Air Quality Detail](https://opendata-ajuntament.barcelona.cat/data/es/dataset/qualitat-aire-detall-bcn) |
| `2026_qualitat_aire_estacions.csv` | [OpenData BCN — Air Quality Stations](https://opendata-ajuntament.barcelona.cat/data/es/dataset/qualitat-aire-estacions-bcn) |
| `2023_tramer_no2_mapa_qualitat_aire_bcn.gpkg` | [OpenData BCN — NO₂ map](https://opendata-ajuntament.barcelona.cat/data/ca/dataset/mapes-immissio-qualitat-aire) |
| `2023_tramer_pm10_mapa_qualitat_aire_bcn.gpkg` | [OpenData BCN — PM10 map](https://opendata-ajuntament.barcelona.cat/data/ca/dataset/mapes-immissio-qualitat-aire) |

---

## How to Run

### 1. Install dependencies

```bash
pip install fastapi uvicorn osmnx networkx scipy numpy pandas requests folium \
            mapbox-vector-tile geopandas shapely scikit-learn prometheus-fastapi-instrumentator
```

### 2. Build the road + pollution JSON (run once)

```bash
python jsonfilemaker.py       # Downloads OSM graph + TomTom point speeds
python jsontodataframe.py     # Merges with pollution GeoPackages
```

### 3. Partition the graph into zones (run once, or to reset zones)

```bash
python partition_graph.py
```

### 4. Enrich zones with air quality data (run once, or to refresh)

```bash
python enrich_graphs.py
```

### 5. Fetch TomTom traffic data (run before each session)

```bash
# All 4 zones
python tomtom_zone_fetcher.py

# Single zone (faster for testing)
python tomtom_zone_fetcher.py --zone 0
```

### 6. Start the edge servers

Open 4 terminals, one per zone:

```bash
ZONE_ID=0 uvicorn edge_server:app --port 8000
ZONE_ID=1 uvicorn edge_server:app --port 8001
ZONE_ID=2 uvicorn edge_server:app --port 8002
ZONE_ID=3 uvicorn edge_server:app --port 8003
```

### 7. Run the orchestrator

```bash
python central_test.py
```

Prints route statistics for all three modes and generates `comparativa_rutas_tfg.html`. Open it in a browser to see the interactive map.

---

## Routing Modes

| Mode | Edge weight | Optimises for |
|------|-------------|---------------|
| `fastest` | `travel_time` (seconds) | Minimum travel time, TomTom-aware |
| `greenest` | `pollution_cost` | Minimum pollution exposure (NO₂, PM10, PM2.5) |
| `balanced` | `0.5 × travel_time + 0.5 × pollution_cost` | Trade-off between time and air quality |

---

## Architecture

### System Architecture Diagram
```mermaid
flowchart TD
    classDef user fill:#fff,stroke:#333,stroke-width:3px
    classDef script fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef server fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    classDef data fill:#e1f5fe,stroke:#01579b,stroke-width:2px

    U((User)):::user -- "Route request (A → B, mode)" --> CO(central_test.py):::script

    subgraph Orchestration
        CO -- "Zone path resolution" --> GW[(gateways_map.csv)]:::data
    end

    subgraph "Edge Layer (distributed nodes)"
        CO -- "POST /calcular_tramo" --> S0[Edge Server Zone 0\nport 8000]:::server
        CO -- "POST /calcular_tramo" --> S1[Edge Server Zone 1\nport 8001]:::server
        CO -- "POST /calcular_tramo" --> S2[Edge Server Zone 2\nport 8002]:::server
        CO -- "POST /calcular_tramo" --> S3[Edge Server Zone 3\nport 8003]:::server
    end

    subgraph "Local data per node"
        S0 & S1 & S2 & S3 --- G[subgraph_zone_N_enriched.graphml]:::data
        S0 & S1 & S2 & S3 --- T[traffic_zone_N.json]:::data
    end

    subgraph "Data preparation (offline)"
        G --- E[enrich_graphs.py]:::data
        T --- D[tomtom_zone_fetcher.py]:::data
        E --- F[(OpenData BCN\nAir Quality CSVs)]:::data
        D --- H[(TomTom API\nFlow Tiles)]:::data
    end

    CO -- "Stitched route + stats" --> Out([comparativa_rutas_tfg.html])
```

### Sequence diagram
```mermaid
sequenceDiagram
    actor User
    participant C as Central Orchestrator
    participant G as Gateway Map
    participant Z1 as Zone 1 Edge Server
    participant Z2 as Zone 2 Edge Server
    participant Z3 as Zone 3 Edge Server

    User->>C: Route request (origin, destination, mode)
    C->>C: Find zone path via zone graph
    C->>G: Look up gateway nodes
    G-->>C: exit node / entry node per boundary

    C->>Z1: POST /calcular_tramo (start → gateway, mode)
    Z1->>Z1: Dijkstra on local subgraph
    Z1-->>C: path + stats + edge_traffic

    C->>Z2: POST /calcular_tramo (gateway → gateway, mode)
    Z2->>Z2: Dijkstra on local subgraph
    Z2-->>C: path + stats + edge_traffic

    C->>Z3: POST /calcular_tramo (gateway → end, mode)
    Z3->>Z3: Dijkstra on local subgraph
    Z3-->>C: path + stats + edge_traffic

    C->>C: Stitch paths + accumulate stats
    Note over C: Repeated × 3 modes (fastest, greenest, balanced)
    C-->>User: 3 complete routes + distance + time + AQ cost
    Note over User: Interactive map — toggle between modes
```
