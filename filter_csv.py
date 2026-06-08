import pandas as pd
import numpy as np

def processar_dades_reals(csv_path):
    # 1. Carregar dades
    df = pd.read_csv(csv_path)

    # Això et dirà quantes estacions diferents hi ha al CSV original
    print("Estacions totals al fitxer:", df['ESTACIO'].nunique())
    print("Llista de totes les estacions:", df['ESTACIO'].unique())
    
    # 2. Filtrar pels contaminants que hem decidit (8, 9, 10)
    codis_interes = [8, 9, 10]
    df_filtrat = df[df['CODI_CONTAMINANT'].isin(codis_interes)].copy()
    
    # 3. Trobar la darrera hora amb dades vàlides (anant enrere de H24 a H01)
    # Crearem una nova columna 'VALOR_ACTUAL'
    def obtenir_ultim_valor(row):
        for i in range(24, 0, -1):
            hora_col = f'H{i:02d}'
            val_col = f'V{i:02d}'
            if row[val_col] == 'V':  # Si la dada és vàlida
                return row[hora_col]
        return np.nan

    df_filtrat['VALOR_ACTUAL'] = df_filtrat.apply(obtenir_ultim_valor, axis=1)
    
    # 4. Agrupar per Estació i Contaminant (per si hi ha dades de diversos dies, agafem la més recent)
    # Ens quedem amb una taula neta: ESTACIO | CODI_CONTAMINANT | VALOR_ACTUAL
    resultat = df_filtrat.groupby(['ESTACIO', 'CODI_CONTAMINANT'])['VALOR_ACTUAL'].last().unstack()
    
    # Renombrar columnes per tenir noms més clars
    resultat.columns = ['NO2', 'PM2.5', 'PM10']
    
    return resultat

# --- PROVA DE FUNCIONAMENT ---
nodes_data = processar_dades_reals("data/Qualitat_Aire_Detall.csv")
print(nodes_data.head())

