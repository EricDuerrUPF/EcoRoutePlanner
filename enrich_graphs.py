import pandas as pd
import networkx as nx
import osmnx as ox
from scipy.spatial import cKDTree
import numpy as np
import os

# --- CONFIGURACIÓN ---
FILE_VALORES = "data/Qualitat_Aire_Detall.csv"
FILE_ESTACIONES = "data/2026_qualitat_aire_estacions.csv"
ZONAS = ["subgraph_zone_0.graphml", "subgraph_zone_1.graphml", "subgraph_zone_2.graphml", "subgraph_zone_3.graphml"]

def get_actual_pollution_data():
    """Procesa el CSV de valores para obtener la última hora válida de NO2, PM10 y PM2.5"""
    df = pd.read_csv(FILE_VALORES)
    codis_interes = {8: 'no2', 10: 'pm10', 9: 'pm25'}
    df_filtrado = df[df['CODI_CONTAMINANT'].isin(codis_interes.keys())].copy()

    def obtener_ultimo_valor(row):
        for i in range(24, 0, -1):
            h, v = f'H{i:02d}', f'V{i:02d}'
            if row[v] == 'V': return row[h]
        return np.nan

    df_filtrado['valor'] = df_filtrado.apply(obtener_ultimo_valor, axis=1)
    # Pivotamos para tener una fila por estación con sus 3 contaminantes
    res = df_filtrado.groupby(['ESTACIO', 'CODI_CONTAMINANT'])['valor'].last().unstack()
    res.columns = [codis_interes[c] for c in res.columns]
    return res.fillna(res.mean()) # Rellenamos huecos con la media de la ciudad

def get_stations_geo():
    """Obtiene lat/lon de cada estación del archivo que me has pasado"""
    df = pd.read_csv(FILE_ESTACIONES)
    return df.drop_duplicates(subset=['Estacio'])[['Estacio', 'Latitud', 'Longitud']]

def enrich():
    print("[*] Iniciando proceso de enriquecimiento con datos reales...")
    
    # 1. Preparar datos maestros
    df_valores = get_actual_pollution_data()
    df_geo = get_stations_geo()
    # Unimos: ID | Lat | Lon | no2 | pm10 | pm25
    master_aire = df_geo.merge(df_valores, left_on='Estacio', right_index=True)
    
    # 2. Árbol de búsqueda espacial
    tree = cKDTree(master_aire[['Latitud', 'Longitud']].values)
    
    # 3. Procesar cada zona
    for zona_file in ZONAS:
        if not os.path.exists(zona_file):
            continue
            
        print(f" -> Procesando {zona_file}...")
        G = ox.load_graphml(zona_file)
        
        for u, v, k, data in G.edges(keys=True, data=True):
            # Coordenadas del nodo origen
            node = G.nodes[u]
            point = [float(node['y']), float(node['x'])] # Assegurem float aquí també
            
            # Buscar estación más cercana
            dist, idx = tree.query(point)
            estacion = master_aire.iloc[idx]
            
            # 1. Inyectar atributos forzando float()
            no2_val = float(estacion['no2'])
            pm10_val = float(estacion['pm10'])
            pm25_val = float(estacion['pm25'])
            length_val = float(data.get('length', 1.0))
            
            data['real_no2'] = no2_val
            data['real_pm10'] = pm10_val
            data['real_pm25'] = pm25_val
            
            # 2. Càlcul de l'impacte (Assegurem que tot és aritmètica de floats)
            impacto = (no2_val/40.0 + pm10_val/50.0 + pm25_val/25.0) / 3.0
            
            # 3. Assignació definitiva (Això és el que Dijkstra sumarà)
            #data['pollution_cost'] = float(length_val * (1.0 + impacto))
            data['pollution_cost'] = float(length_val * (1.0 + (impacto * 5)))
            data['balanced_weight'] = float((length_val * 0.5) + (data['pollution_cost'] * 0.5))
            data['length'] = length_val # També sobreescrivim length per si era un str

        # Guardar el grafo actualizado
        ox.save_graphml(G, zona_file.replace(".graphml", "_enriched.graphml"))
        print(f" [OK] {zona_file} enriquecido.")

if __name__ == "__main__":
    enrich()