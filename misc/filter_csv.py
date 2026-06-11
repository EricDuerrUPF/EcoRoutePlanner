import pandas as pd
import numpy as np

def processar_dades_reals(csv_path):
    # 1. Load data
    df = pd.read_csv(csv_path)

    # This will tell you how many different stations there are in the original CSV
    print("Total stations in the file:", df['ESTACIO'].nunique())
    print("List of all stations:", df['ESTACIO'].unique())
    
    # 2. Filter for the contaminants we decided (8, 9, 10)
    codis_interes = [8, 9, 10]
    df_filtrat = df[df['CODI_CONTAMINANT'].isin(codis_interes)].copy()
    
    # 3. Find the last hour with valid data (going back from H24 to H01)
    # We will create a new column 'VALOR_ACTUAL'
    def obtenir_ultim_valor(row):
        for i in range(24, 0, -1):
            hora_col = f'H{i:02d}'
            val_col = f'V{i:02d}'
            if row[val_col] == 'V':  # If the data is valid
                return row[hora_col]
        return np.nan

    df_filtrat['VALOR_ACTUAL'] = df_filtrat.apply(obtenir_ultim_valor, axis=1)
    
    # 4. Group by Station and Pollutant (in case there are data from different days, we take the most recent)
    # We are left with a clean table: STATION | POLLUTANT_CODE | CURRENT_VALUE
    resultat = df_filtrat.groupby(['ESTACIO', 'CODI_CONTAMINANT'])['VALOR_ACTUAL'].last().unstack()
    
    # Rename columns to have clearer names
    resultat.columns = ['NO2', 'PM2.5', 'PM10']
    
    return resultat

# --- WORKING TEST ---
nodes_data = processar_dades_reals("../data/Qualitat_Aire_Detall.csv")
print(nodes_data.head())

