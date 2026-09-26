# MURA Monitoring & Operations Guide

See the full operational monitoring specification at [docs/production/MONITORING.md](docs/production/MONITORING.md).

### Quick Health Checks:
- Liveness: `GET /health` -> `{"status": "ok"}`
- Readiness: `GET /ready` -> `{"status": "ready", "database": "connected"}`
- Operational Summary: `GET /v1/operations/monitoring/summary` (requires `Authorization: Bearer <OPERATIONS_API_KEY>`)

### Smoke Runner:
```bash
python scripts/ops/run_monitoring_smoke.py --base-url http://127.0.0.1:8000 --operations-key "$OPERATIONS_API_KEY"
```
