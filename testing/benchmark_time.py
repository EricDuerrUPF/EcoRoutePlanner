import requests
import time

endpoints = ["/ping", "/empty", "/calculate_route"]
url_base = "http://10.80.152.38:8000"

for ep in endpoints:
    start = time.perf_counter()
    for _ in range(100):
        # Using a short timeout to detect if the system blocks
        requests.get(url_base + ep, timeout=2) 
    end = time.perf_counter()
    print(f"Endpoint {ep}: Total time for 100 reqs = {end - start:.2f}s")