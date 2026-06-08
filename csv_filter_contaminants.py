import pandas as pd

# 1. Carregar el fitxer
nom_fitxer = "data/Qualitat_Aire_Detall.csv"

try:
    # Llegim només les primeres files per identificar la columna correcta
    df_preview = pd.read_csv(nom_fitxer, nrows=5)
    
    # Busquem la columna que conté la paraula 'CONTAMINANT'
    col_codi = [c for c in df_preview.columns if 'CONTAMINANT' in c.upper()]
    
    if not col_codi:
        print("[!] No s'ha trobat cap columna amb el nom 'CONTAMINANT'.")
        print(f"Les columnes disponibles són: {list(df_preview.columns)}")
    else:
        col_nom = col_codi[0]
        print(f"[*] Analitzant la columna: '{col_nom}'")
        
        # 2. Llegim tot el fitxer però només la columna que ens interessa (per anar més ràpid)
        df_full = pd.read_csv(nom_fitxer, usecols=[col_nom])
        
        # 3. Obtenir valors únics i ordenar-los
        codis_reals = sorted(df_full[col_nom].unique())
        
        print("\n--- CODIS DE CONTAMINANT TROBATS AL TEU CSV ---")
        for codi in codis_reals:
            print(f"ID Contaminant: {codi}")
        
        print(f"\nTotal de contaminants diferents: {len(codis_reals)}")

except FileNotFoundError:
    print(f"[!] Error: No s'ha trobat el fitxer '{nom_fitxer}'. Assegura't que està a la mateixa carpeta.")
except Exception as e:
    print(f"[!] Error inesperat: {e}")