import time
import requests

url = "http://10.80.152.38:8000/ping" # RPi IP
n = 100

start = time.perf_counter()
for _ in range(n):
    requests.get(url)
end = time.perf_counter()

avg = (end - start) / n * 1000
print(f"Transport + Framework average latency: {avg:.2f} ms")