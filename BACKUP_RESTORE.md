# MURA (Мұра) — Backup, Point-In-Time Recovery & Disaster Recovery Runbook

## 1. Reliability Objectives

| Metric | Target Objective | Implementation Mechanism |
| :--- | :--- | :--- |
| **RPO (Recovery Point Objective)** | **< 1 hour** (target: < 5 min) | Continuous PostgreSQL Write-Ahead Log (WAL) archiving via Supabase Point-in-Time Recovery (PITR) + hourly storage replication |
| **RTO (Recovery Time Objective)** | **< 30 minutes** | Automated database snapshot restoration and stateless container re-pointing |

---

## 2. PostgreSQL Backup & Point-In-Time Recovery (PITR)

### A. Automated Daily & Continuous Backups
- Supabase manages automated daily physical backups retained for 7 days (or 30 days on Pro/Enterprise).
- PITR enables restoring the database to any millisecond within the retention window.

### B. Manual Verification & Logical Export Runbook
Run periodic logical dumps to verify data integrity and migration replay:

```bash
# 1. Take a clean logical dump without owner constraints
pg_dump \
  --format=custom \
  --no-owner \
  --no-privileges \
  --file="mura_backup_$(date +%Y%m%d_%H%M%S).dump" \
  "$DATABASE_URL"

# 2. Test restore into an isolated staging/verification database
createdb mura_verification
pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --dbname="$VERIFICATION_DATABASE_URL" \
  mura_backup_*.dump

# 3. Verify row counts and integrity invariants
python -c "
from mura.storage.database import Database
from sqlalchemy import select, func
from mura.storage.identity import FamilyRow, UserRow
from mura.storage.database import RecordingRow
from mura.storage.book import BookRow

db = Database('$VERIFICATION_DATABASE_URL')
with db.session_factory() as s:
    print('Families:', s.scalar(select(func.count(FamilyRow.family_id))))
    print('Users:', s.scalar(select(func.count(UserRow.user_id))))
    print('Recordings:', s.scalar(select(func.count(RecordingRow.recording_id))))
    print('Books:', s.scalar(select(func.count(BookRow.book_id))))
"
```

---

## 3. Storage Recovery & Integrity Verification

### A. Storage Architecture
MURA stores binary data across two distinct private prefixes:
1. **Audio Storage**: `audio/{family_id}/{recording_id}/{filename}`
2. **Book Artifacts**: `book_artifacts/{family_id}/{book_id}/{format}`

### B. SHA256 Integrity Verification on Restore
Every audio recording and book export stores a verified cryptographic `sha256` hash in PostgreSQL (`recordings.sha256` and `book_exports.sha256`):

```python
import hashlib
from mura.storage.database import Database, RecordingRow
from mura.storage.audio import build_audio_storage
from sqlalchemy import select

def verify_storage_integrity(db: Database, storage) -> None:
    with db.session_factory() as session:
        recordings = session.scalars(select(RecordingRow)).all()
        for rec in recordings:
            if not rec.storage_key or not rec.sha256:
                continue
            with storage.open(rec.storage_key) as f:
                data = f.read()
                actual_hash = hashlib.sha256(data).hexdigest()
                assert actual_hash == rec.sha256, f"Corrupted audio file for {rec.recording_id}"
    print("Storage integrity verified: 0 mismatches.")
```

---

## 4. Disaster Recovery Checklist

1. **Service Outage Triage**:
   - Assess Supabase status (database vs storage).
   - If database is corrupted, initiate PITR restore to 10 minutes prior to corruption incident.
2. **Environment Variable Cutover**:
   - Update Railway `DATABASE_URL` to point to the restored cluster.
   - Restart `mura-api`, `mura-recording-worker`, `mura-book-worker`, and `mura-cleanup-worker`.
3. **Health & Readiness Verification**:
   - Execute `/health` and `/ready` probes.
   - Run deterministic staging test suite: `python -m pytest tests/staging/` to verify zero regression.
4. **Notify Stakeholders**: Post incident summary to status monitoring channel.
