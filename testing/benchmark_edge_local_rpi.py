import time
import json
import csv
import numpy as np
import requests

# Environment configs to evaluate environments identically
ENVIRONMENTS = {
    "Localhost": {
        0: "http://127.0.0.1:8000",
        1: "http://127.0.0.1:8001",
        2: "http://127.0.0.1:8002",
        3: "http://127.0.0.1:8003"
    },
    "Raspberry_Pi": {
        0: "http://10.80.152.38:8000",
        1: "http://10.80.152.39:8001",
        2: "http://10.80.152.42:8002",
        3: "http://10.80.152.43:8003"
    }
}

BURST_MODE = False # True for fast queries, False for sequential queries with small delay (more realistic for user interactions)
DELAY_BETWEEN = 0.05 # 50ms delay between queries when not in burst mode (to avoid overwhelming the server and simulate real user behavior)

# Fixed test nodes per zone extracted from your test routes (for a real comparison, these should be the same across environments)
# Took four random routes from the original test set to ensure we are testing the same logic in both environments
TEST_CASES = {
    0: {"start_node": 30243263, "end_node": 30243065, "mode": "balanced"},   # Zone 0 (Aragó / Padilla)
    1: {"start_node": 30343644, "end_node": 30343284, "mode": "greenest"},   # Zone 1 (Sevilla / Maquinista)
    2: {"start_node": 1379023475, "end_node": 30236718, "mode": "fastest"},  # Zone 2 (Espanya / Blasco Garay)
    3: {"start_node": 30243371, "end_node": 3047640929, "mode": "balanced"}  # Zone 3 (Sagrada Família / València)
}

NUM_RUNS = 105  # Launch 105 runs, ignoring the first 5 (accounts for warm-up phase)
WARM_UP  = 5

bench_results = []

print("=" * 65)
print("  EDGE BENCHMARK: Localhost vs Raspberry Pi 5 Cluster")
print("=" * 65)

for env_name, nodes in ENVIRONMENTS.items():
    print(f"\n🚀 Evaluating Environment: {env_name} | Burst Mode: {BURST_MODE}")
    
    for zone_id, payload in TEST_CASES.items():
        url = f"{nodes[zone_id]}/calcular_tramo"
        latencies = []
        
        # CRONÓMETRO DE ZONA
        total_start = time.perf_counter()
        
        for run in range(NUM_RUNS):
            t_start = time.perf_counter()
            try:
                resp = requests.post(url, json=payload, timeout=10) # Timeout to avoid hanging if the server is unresponsive
                if resp.status_code == 200:
                    latencies.append((time.perf_counter() - t_start) * 1000)
            except Exception as e:
                pass
            
            # If not in burst mode, add a small delay between queries to simulate more realistic user interactions and avoid overwhelming the server
            if not BURST_MODE:
                time.sleep(DELAY_BETWEEN)
            else:
                time.sleep(0.01)
                
        total_end = time.perf_counter()
        total_duration = total_end - total_start
        
        valid_latencies = latencies[WARM_UP:]
        print(f"  [➔ Zone {zone_id}] Total wall-time: {total_duration:.2f}s | Avg Latency: {np.mean(valid_latencies):.2f}ms")
        
        if len(valid_latencies) > 0:
            # Statistic processing of the samples with NumPy
            min_lat = np.min(valid_latencies)
            avg_lat = np.mean(valid_latencies)
            max_lat = np.max(valid_latencies)
            p95_lat = np.percentile(valid_latencies, 95)
            std_dev = np.std(valid_latencies)
            
            bench_results.append({
                "Environment": env_name,
                "Zone": f"Zone {zone_id}",
                "Queries_Analyzed": len(valid_latencies),
                "Min_Latency_ms": round(min_lat, 2),
                "Avg_Latency_ms": round(avg_lat, 2),
                "Max_Latency_ms": round(max_lat, 2),
                "Percentile_95_ms": round(p95_lat, 2),
                "Std_Dev_ms": round(std_dev, 2)
            })
            print(f"    ✔ Done | Avg: {avg_lat:.2f}ms | P95: {p95_lat:.2f}ms | StdDev: {std_dev:.2f}ms")
        else:
            print(f"    [X] Error: No successful queries recorded for Zone {zone_id}")

# Save the results to a clean CSV file for further analysis and visualization
csv_columns = ["Environment", "Zone", "Queries_Analyzed", "Min_Latency_ms", "Avg_Latency_ms", "Max_Latency_ms", "Percentile_95_ms", "Std_Dev_ms"]
csv_file = "benchmark_hardware_tfg.csv"

try:
    with open(csv_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns)
        writer.writeheader()
        writer.writerows(bench_results)
    print(f"\n[✓] Benchmark completed successfully! Summary saved to '{csv_file}'")
except IOError:
    print(f"\n[X] Error saving CSV file.")