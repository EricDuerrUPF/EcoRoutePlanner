import osmnx as ox
import folium
from shapely.geometry import MultiPoint
import pandas as pd

# Configuración de tus archivos subidos
files = {
    0: "subgraph_zone_0_enriched.graphml",
    1: "subgraph_zone_1_enriched.graphml",
    2: "subgraph_zone_2_enriched.graphml",
    3: "subgraph_zone_3_enriched.graphml"
}

colors = {0: "#e74c3c", 1: "#2980b9", 2: "#27ae60", 3: "#f39c12"}
labels = {
    0: "Zona 0: Gràcia / Les Corts",
    1: "Zona 1: Eixample Est / Barceloneta",
    2: "Zona 2: Eixample Oest / Sants",
    3: "Zona 3: Sarrià / Sagrada Família"
}

def generate_real_map():
    # Creamos el mapa base centrado en Barcelona
    m = folium.Map(location=[41.3870, 2.1680], zoom_start=13, tiles="CartoDB positron")

    for zone_id, file_path in files.items():
        print(f"[*] Procesando {file_path}...")
        try:
            # 1. Cargar el grafo real de la zona
            G = ox.load_graphml(file_path)
            
            # 2. Extraer coordenadas de los nodos
            nodes_data = []
            for node, data in G.nodes(data=True):
                nodes_data.append({'y': float(data['y']), 'x': float(data['x'])})
            
            df_nodes = pd.DataFrame(nodes_data)
            
            # 3. Calcular el Convex Hull (la forma que encierra a todos los nodos)
            points = MultiPoint(list(zip(df_nodes.x, df_nodes.y)))
            convex_hull = points.convex_hull
            
            # 4. Convertir a formato Folium (lat, lon)
            if convex_hull.geom_type == 'Polygon':
                hull_coords = [(lat, lon) for lon, lat in convex_hull.exterior.coords]
                
                # Dibujar el polígono real de la zona
                folium.Polygon(
                    locations=hull_coords,
                    color=colors[zone_id],
                    weight=3,
                    fill=True,
                    fill_color=colors[zone_id],
                    fill_opacity=0.3,
                    popup=f"<b>{labels[zone_id]}</b><br>Nodos: {len(G.nodes)}",
                    tooltip=f"Zona {zone_id}"
                ).add_to(m)
                
                # Añadir un marcador en el centro de la zona
                center = convex_hull.centroid
                folium.Marker(
                    location=[center.y, center.x],
                    icon=folium.DivIcon(html=f'<div style="color: white; background: {colors[zone_id]}; padding: 2px 5px; border-radius: 5px; font-weight: bold; font-size: 10px;">Z{zone_id}</div>')
                ).add_to(m)

        except Exception as e:
            print(f"[!] Error procesando Zona {zone_id}: {e}")

    # Guardar el mapa
    output_name = "mapa_zonas_reales.html"
    m.save(output_name)
    print(f"\n[OK] Mapa guardado como '{output_name}'.")

if __name__ == "__main__":
    generate_real_map()