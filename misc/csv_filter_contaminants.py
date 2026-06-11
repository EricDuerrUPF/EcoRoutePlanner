import pandas as pd

# 1. Load file
file_name = "../data/Qualitat_Aire_Detall.csv"

try:
    # Only read the first few rows to identify the correct column
    df_preview = pd.read_csv(file_name, nrows=5)
    
    # Search for the column that contains the word 'CONTAMINANT'
    col_codi = [c for c in df_preview.columns if 'CONTAMINANT' in c.upper()]
    
    if not col_codi:
        print("[!] No pollutant column found with the name 'CONTAMINANT'.")
        print(f"Available columns are: {list(df_preview.columns)}")
    else:
        col_nom = col_codi[0]
        print(f"[*] Analyzing column: '{col_nom}'")
        
        # 2. Read the whole file but only the column we are interested in (to go faster)
        df_full = pd.read_csv(file_name, usecols=[col_nom])
        
        # 3. Get unique values and sort them
        codis_reals = sorted(df_full[col_nom].unique())
        
        print("\n--- POLLUTANT CODES FOUND IN YOUR CSV ---")
        for codi in codis_reals:
            print(f"Pollutant ID: {codi}")
        
        print(f"\nTotal different pollutants found: {len(codis_reals)}")

except FileNotFoundError:
    print(f"[!] Error: File '{file_name}' not found. Make sure it's in the same folder.")
except Exception as e:
    print(f"[!] Unexpected error: {e}")