"""Mock API server for mle-solver eval. Serves deterministic sensor data.

Run: python mock_api_server.py
Endpoints:
  GET /sensors         -> list of sensor IDs
  GET /readings?sensor_id=X&page=N  -> paginated readings (20 per page)
  GET /metadata?sensor_id=X  -> sensor metadata
"""

import json
import math
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

random.seed(42)

SENSORS = {
    "sensor_01": {"location": "Building A, Floor 1", "type": "temperature", "unit": "celsius"},
    "sensor_02": {"location": "Building A, Floor 2", "type": "temperature", "unit": "celsius"},
    "sensor_03": {"location": "Building B, Floor 1", "type": "humidity", "unit": "percent"},
    "sensor_04": {"location": "Building B, Floor 2", "type": "humidity", "unit": "percent"},
    "sensor_05": {"location": "Outdoor", "type": "temperature", "unit": "celsius"},
}

def generate_readings(sensor_id: str) -> list[dict]:
    rng = random.Random(hash(sensor_id))
    meta = SENSORS[sensor_id]
    readings = []
    for hour in range(168):  # 7 days of hourly data
        day = hour // 24
        h = hour % 24
        if meta["type"] == "temperature":
            base = 22.0 if "Building" in meta["location"] else 15.0
            seasonal = 3.0 * math.sin(2 * math.pi * h / 24)
            noise = rng.gauss(0, 0.5)
            value = round(base + seasonal + noise, 2)
        else:
            base = 45.0
            seasonal = 10.0 * math.sin(2 * math.pi * h / 24 + math.pi)
            noise = rng.gauss(0, 2.0)
            value = round(max(20, min(90, base + seasonal + noise)), 2)
        readings.append({
            "timestamp": f"2025-03-{day+1:02d}T{h:02d}:00:00Z",
            "value": value,
        })
    return readings

ALL_READINGS = {sid: generate_readings(sid) for sid in SENSORS}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/sensors":
            self._json(200, {"sensors": list(SENSORS.keys())})
        elif parsed.path == "/metadata":
            sid = params.get("sensor_id", [None])[0]
            if sid in SENSORS:
                self._json(200, {"sensor_id": sid, **SENSORS[sid]})
            else:
                self._json(404, {"error": "sensor not found"})
        elif parsed.path == "/readings":
            sid = params.get("sensor_id", [None])[0]
            page = int(params.get("page", [1])[0])
            per_page = 20
            if sid not in ALL_READINGS:
                self._json(404, {"error": "sensor not found"})
                return
            data = ALL_READINGS[sid]
            start = (page - 1) * per_page
            chunk = data[start:start + per_page]
            self._json(200, {
                "sensor_id": sid,
                "page": page,
                "total_pages": math.ceil(len(data) / per_page),
                "total_readings": len(data),
                "readings": chunk,
            })
        else:
            self._json(404, {"error": "not found"})

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress logs

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8777), Handler)
    print("Mock API running on http://127.0.0.1:8777")
    server.serve_forever()
