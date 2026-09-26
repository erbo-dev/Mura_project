# MURA Production Documentation Index

This directory contains the operational, staging, and production runbooks for MURA Wave 1:

- **[Operational Readiness Status](production/WAVE1_OPS_STATUS.md)**: Comprehensive matrix of all Milestone D workstreams (A through H).
- **[Staging Validation Runbook](production/STAGING_VALIDATION.md)**: Step-by-step staging verification using the new operational CLI tools.
- **[Monitoring & Alerting Specification](production/MONITORING.md)**: Health probes, operational endpoints, derived threshold alerts, and SRE playbooks.
- **[Deployment Runbooks](production/RUNBOOKS.md)**: System topology, worker architecture, environment configurations, and zero-downtime deployment protocols.

### CLI Operational Tooling:
- `scripts/ops/backup_restore_drill.py`: Safe PostgreSQL backup and restore drill with schema and storage integrity verification.
- `scripts/ops/reconcile_storage.py`: Report-only storage reconciliation for audio recordings and book exports.
- `scripts/ops/run_load_test.py`: Staging load and worker queue performance benchmark harness.
- `scripts/ops/run_monitoring_smoke.py`: Health, readiness, operations monitoring, and privacy redaction verifier.
- `scripts/ops/run_security_smoke.py`: Staging security smoke runner (auth, BOLA, role matrix, header validation).
