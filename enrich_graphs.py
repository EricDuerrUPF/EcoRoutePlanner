import pandas as pd
import networkx as nx
import osmnx as ox
from scipy.spatial import cKDTree
import numpy as np
import os

# --- CONFIGURATION ---
DATA_FILE = "data/Qualitat_Aire_Detall.csv"
STATIONS_FILE = "data/2026_qualitat_aire_estacions.csv"
ZONAS = ["subgraph_zone_0.graphml", "subgraph_zone_1.graphml", "subgraph_zone_2.graphml", "subgraph_zone_3.graphml"]

def get_actual_pollution_data():
    """Processes the CSV of values to get the last valid hour of NO2, PM10, and PM2.5 for each station"""
    df = pd.read_csv(DATA_FILE)
    codis_interes = {8: 'no2', 10: 'pm10', 9: 'pm25'}
    df_filtrado = df[df['CODI_CONTAMINANT'].isin(codis_interes.keys())].copy()

    def obtener_ultimo_valor(row):
        for i in range(24, 0, -1):
            h, v = f'H{i:02d}', f'V{i:02d}'
            if row[v] == 'V': return row[h]
        return np.nan

    df_filtrado['valor'] = df_filtrado.apply(obtener_ultimo_valor, axis=1)
    # Pivot to have one row per station with its 3 pollutants as columns
    res = df_filtrado.groupby(['ESTACIO', 'CODI_CONTAMINANT'])['valor'].last().unstack()
    res.columns = [codis_interes[c] for c in res.columns]
    return res.fillna(res.mean()) # Fill missing values with the mean of each pollutant across all stations, ensuring we have data for every station

def get_stations_geo():
    """Obtains lat/lon of each station from the provided file, ensuring we have unique stations and only the necessary columns"""
    df = pd.read_csv(STATIONS_FILE)
    return df.drop_duplicates(subset=['Estacio'])[['Estacio', 'Latitud', 'Longitud']]

def enrich():
    print("[*] Starting process of enriching with real data...")
    
    # 1. Prepare master data
    df_valores = get_actual_pollution_data()
    df_geo = get_stations_geo()
    # Join: ID | Lat | Lon | no2 | pm10 | pm25
    master_aire = df_geo.merge(df_valores, left_on='Estacio', right_index=True)
    
    # 2. Spatial search tree for nearest station lookup
    tree = cKDTree(master_aire[['Latitud', 'Longitud']].values)
    
    # 3. Process each zone graph
    for file_zone in ZONAS:
        if not os.path.exists(file_zone):
            continue
            
        print(f" -> Processing {file_zone}...")
        G = ox.load_graphml(file_zone)
        
        for u, v, k, data in G.edges(keys=True, data=True):
            # Origin node coordinates
            node = G.nodes[u]
            point = [float(node['y']), float(node['x'])] # Ensure float here as well
            
            # Search for the nearest station
            dist, idx = tree.query(point)
            estacion = master_aire.iloc[idx]
            
            # 1. Inject attributes by forcing float()
            no2_val = float(estacion['no2'])
            pm10_val = float(estacion['pm10'])
            pm25_val = float(estacion['pm25'])
            length_val = float(data.get('length', 1.0))
            
            data['real_no2'] = no2_val
            data['real_pm10'] = pm10_val
            data['real_pm25'] = pm25_val
            
            # 2. Calculation of the impact (Ensure all are float arithmetic)
            impacto = (no2_val/40.0 + pm10_val/50.0 + pm25_val/25.0) / 3.0
            
            # 3. Final assignment (This is what Dijkstra will sum up as "cost" for the edge)
            #data['pollution_cost'] = float(length_val * (1.0 + impacto))
            data['pollution_cost'] = float(length_val * (1.0 + (impacto * 5)))
            data['balanced_weight'] = float((length_val * 0.5) + (data['pollution_cost'] * 0.5))
            data['length'] = length_val # Over-write length in case of str

        # Save the updated graphml with enriched attributes
        ox.save_graphml(G, file_zone.replace(".graphml", "_enriched.graphml"))
        print(f" [OK] {file_zone} enriched.")

if __name__ == "__main__":
    enrich()