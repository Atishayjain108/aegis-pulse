"""
AEGIS Pulse load tests using Locust.
https://locust.io — 25k stars, MIT

Tests alert API and dashboard under concurrent load.

Run headless 30s (omit --host so each class uses its own host attribute):
    uv run locust -f tests/load/locustfile.py --headless \\
      -u 10 -r 2 -t 30s \\
      --html tests/load/report.html
"""
from locust import HttpUser, between, task


class AlertAPIUser(HttpUser):
    wait_time = between(0.1, 0.5)
    host = "http://localhost:8200"

    @task(5)
    def health_check(self):
        with self.client.get("/healthz", catch_response=True) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"health: HTTP {r.status_code}")

    @task(2)
    def ready_check(self):
        with self.client.get("/readyz", catch_response=True) as r:
            if r.status_code in (200, 503):
                r.success()
            else:
                r.failure(f"ready: HTTP {r.status_code}")

    @task(1)
    def killswitch_state(self):
        with self.client.get(
            "/killswitch",
            catch_response=True,
            name="/killswitch",
        ) as r:
            if r.status_code in (200, 404):
                r.success()
            else:
                r.failure(f"killswitch: HTTP {r.status_code}")


class DashboardUser(HttpUser):
    wait_time = between(0.5, 2.0)
    host = "http://localhost:8300"

    @task(5)
    def dashboard_health(self):
        with self.client.get("/healthz", catch_response=True) as r:
            if r.status_code == 200:
                r.success()
            else:
                r.failure(f"dashboard health: HTTP {r.status_code}")

    @task(3)
    def get_signals(self):
        with self.client.get(
            "/api/signals/recent?limit=20",
            catch_response=True,
            name="/api/signals/recent",
        ) as r:
            if r.status_code in (200, 204):
                r.success()
            else:
                r.failure(f"signals: HTTP {r.status_code}")

    @task(2)
    def get_alerts(self):
        with self.client.get(
            "/api/execute/alerts?limit=10",
            catch_response=True,
            name="/api/execute/alerts",
        ) as r:
            if r.status_code in (200, 204):
                r.success()
            else:
                r.failure(f"alerts: HTTP {r.status_code}")
