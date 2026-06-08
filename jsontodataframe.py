import geopandas as gpd
import pandas as pd
import json
import numpy as np
from shapely.geometry import LineString
from shapely.ops import nearest_points

# --- Load your OSM + TomTom roads ---
with open('barcelona_osm_tomtom.json', 'r') as f:
    road_data = json.load(f)

# Convert to GeoDataFrame (CRS = EPSG:4326)
features = []
for feat in road_data:
    coords = feat['geometry']['coordinates']
    line = LineString([(lon, lat) for lon, lat in coords])
    props = feat['properties']
    features.append({
        'geometry': line,
        'osm_id': props['osm_id'],
        'road_type': props['road_type'],
        'name': props['name'],
        'length_m': props['length_m'],
        'speed_kmh': props['speed_kmh'],
        'source': props['source']
    })
roads_gdf = gpd.GeoDataFrame(features, crs='EPSG:4326')
print("Roads loaded.")

# --- Load pollution GeoPackages ---
no2_gdf = gpd.read_file('data/2023_tramer_no2_mapa_qualitat_aire_bcn.gpkg')
pm10_gdf = gpd.read_file('data/2023_tramer_pm10_mapa_qualitat_aire_bcn.gpkg')

# Print column names to verify
print("\nNO2 columns:", no2_gdf.columns.tolist())
print("PM10 columns:", pm10_gdf.columns.tolist())

# --- Convert pollution data to WGS84 (EPSG:4326) ---
if no2_gdf.crs != 'EPSG:4326':
    no2_gdf = no2_gdf.to_crs('EPSG:4326')
if pm10_gdf.crs != 'EPSG:4326':
    pm10_gdf = pm10_gdf.to_crs('EPSG:4326')

# --- Extract numeric value from 'Rang' column ---
# Example: "20-30 µg/m³" -> 25, "<20 µg/m³" -> 15, ">40 µg/m³" -> 45, etc.
def rang_to_numeric(rang_str):
    if pd.isna(rang_str):
        return None
    rang_str = str(rang_str)
    # Extract numbers (simple approach: find all digits and dashes)
    import re
    numbers = re.findall(r'\d+', rang_str)
    if len(numbers) == 2:
        # Range like "20-30"
        return (int(numbers[0]) + int(numbers[1])) / 2.0
    elif len(numbers) == 1:
        # Single number like "<20" or ">40"
        return int(numbers[0])  # Use threshold as value
    else:
        return None

no2_gdf['no2_value'] = no2_gdf['Rang'].apply(rang_to_numeric)
pm10_gdf['pm10_value'] = pm10_gdf['Rang'].apply(rang_to_numeric)

# Drop rows where value could not be parsed
no2_gdf = no2_gdf.dropna(subset=['no2_value'])
pm10_gdf = pm10_gdf.dropna(subset=['pm10_value'])

print(f"NO2 data: {len(no2_gdf)} segments with numeric values")
print(f"PM10 data: {len(pm10_gdf)} segments with numeric values")

# --- Spatial join: For each road, find the nearest pollution segment ---
# Because both are now in geographic CRS, we need to use a projected CRS for accurate distance.
# We'll temporarily project to a local UTM zone (EPSG:25831 for Barcelona).
roads_proj = roads_gdf.to_crs('EPSG:25831')
no2_proj = no2_gdf.to_crs('EPSG:25831')
pm10_proj = pm10_gdf.to_crs('EPSG:25831')

# Function to get pollution value from nearest segment
def get_nearest_value(point_geom, pollution_proj_gdf, value_col):
    # Find nearest pollution segment to the point
    distances = pollution_proj_gdf.distance(point_geom)
    min_idx = distances.idxmin()
    return pollution_proj_gdf.loc[min_idx, value_col]

# For each road, we'll use its midpoint for the join
def assign_pollution(road_geom_proj, pollution_proj_gdf, value_col):
    midpoint = road_geom_proj.interpolate(0.5, normalized=True)
    return get_nearest_value(midpoint, pollution_proj_gdf, value_col)

roads_proj['no2'] = roads_proj.geometry.apply(
    lambda geom: assign_pollution(geom, no2_proj, 'no2_value')
)
roads_proj['pm10'] = roads_proj.geometry.apply(
    lambda geom: assign_pollution(geom, pm10_proj, 'pm10_value')
)

# Convert back to geographic CRS and merge attributes
roads_final = roads_proj.to_crs('EPSG:4326')

# --- Reconstruct original JSON format with pollution fields ---
updated_features = []
for idx, row in roads_final.iterrows():
    coords = [(lon, lat) for lon, lat in row.geometry.coords]
    feat = {
        'geometry': {
            'type': 'LineString',
            'coordinates': coords
        },
        'properties': {
            'osm_id': row['osm_id'],
            'road_type': row['road_type'],
            'name': row['name'],
            'length_m': row['length_m'],
            'speed_kmh': row['speed_kmh'],
            'source': row['source'],
            'no2': float(row['no2']) if pd.notna(row['no2']) else None,
            'pm10': float(row['pm10']) if pd.notna(row['pm10']) else None
        }
    }
    updated_features.append(feat)

# Save enriched JSON
with open('barcelona_osm_tomtom_pollution.json', 'w') as f:
    json.dump(updated_features, f, indent=2)
print("\n✅ Enriched dataset saved to 'barcelona_osm_tomtom_pollution.json'")