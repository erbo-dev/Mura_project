# MURA (Мұра) — Privacy Lifecycle, Data Retention & Right-to-be-Forgotten
### Privacy Guarantees · Physical Deletion Cascades · Sole-Owner Invariant · Structured Portability Export

MURA is designed for intergenerational family memories. Family audio, ancestral kinship graphs, and autobiographical narratives represent highly sensitive personal data. This document formalizes the privacy architecture, retention model, and deletion guarantees implemented across MURA.

---

## 1. Core Privacy Invariants

1. **Zero Telemetry of Audio or Transcripts**: Sentry, Railway logs, and operational monitoring metrics never log audio URLs, signed tokens, raw transcripts, or story payloads.
2. **Hard Physical Deletion**: Deletion operations do not merely toggle soft-delete flags. Physical audio files in object storage and compiled book artifacts (PDF/EPUB) are permanently unlinked and destroyed.
3. **Strict BOLA Enforcement**: Every mutation and deletion endpoint strictly verifies that the requested resource belongs to the URL `family_id` and that the caller possesses the required capability within that family.
4. **Sole-Owner Protection for Destruction**: Family destruction cannot be executed unilaterally by one owner if multiple owners exist.

---

## 2. Retention & Lifecycle States

```text
┌──────────────┐      Extract & Enrich      ┌──────────────────┐
│ Audio Upload ├───────────────────────────▶│ Family Archive   │
└──────┬───────┘                            │ - People         │
       │                                    │ - Kinship Edges  │
       │ DELETE recording                   │ - Stories        │
       ▼                                    └────────┬─────────┘
┌──────────────┐                                     │ Compile Book
│ Physical     │                                     ▼
│ Audio Purged │                            ┌──────────────────┐
└──────────────┘                            │ Family Book      │
                                            │ - Chapters       │
                                            │ - PDF / EPUB     │
                                            └────────┬─────────┘
                                                     │ DELETE book
                                                     ▼
                                            ┌──────────────────┐
                                            │ Artifacts Purged │
                                            └──────────────────┘
```

---

## 3. Deletion Lifecycle Endpoints

### 3.1 Single Recording Hard Deletion
```http
DELETE /v1/families/{family_id}/recordings/{recording_id}
```
- **Capability Required**: `manage_recordings` (`OWNER` or `EDITOR`).
- **Response**: `204 No Content`.
- **Physical Asset Cleanup**:
  - `storage.delete(storage_key)` permanently deletes the stored audio file from private object storage (Local disk or Supabase S3 bucket).
- **Database Cascade Cleanup**:
  - `recordings` row
  - `recording_jobs` rows
  - `pipeline_results` rows
  - `processing_traces` rows
  - `archive_claims` referencing `recording_id`

### 3.2 Single Book Hard Deletion
```http
DELETE /v1/families/{family_id}/books/{book_id}
```
- **Capability Required**: `create_book` (`OWNER` or `EDITOR`).
- **Response**: `204 No Content`.
- **Physical Asset Cleanup**:
  - Unlinks and deletes all compiled PDF and EPUB artifacts from storage via `book_artifact_storage.delete(storage_key)`.
- **Database Cascade Cleanup**:
  - `books` row
  - `book_jobs` rows
  - `book_plans` row
  - `book_chapters` rows
  - `book_continuity_states` rows
  - `book_source_snapshots` row
  - `book_exports` rows

### 3.3 Complete Family Archive Destruction
```http
DELETE /v1/families/{family_id}
Content-Type: application/json

{
  "confirm_family_id": "fam_a1b2c3d4e5"
}
```
- **Capability Required**: `manage_members` (`OWNER` only).
- **Safety Precondition — Sole Owner Check**:
  - If the family has more than 1 owner (`count_owners > 1`), the API aborts with HTTP `409 Conflict`:
    ```json
    {
      "code": "sole_owner_required",
      "message": "Cannot delete family with multiple owners. Demote or remove other owners first.",
      "request_id": "req_..."
    }
    ```
- **Confirmation Guard**: Request body must contain `confirm_family_id` matching `family_id`.
- **Cascade Deletion Scope**:
  1. Purges all audio files across all family recordings from object storage.
  2. Purges all book exports (PDF/EPUB) across all family books from object storage.
  3. Deletes all database entities:
     - `families`, `family_memberships`
     - `recordings`, `recording_jobs`, `pipeline_results`, `processing_traces`
     - `books`, `book_jobs`, `book_plans`, `book_chapters`, `book_continuity_states`, `book_source_snapshots`, `book_exports`
     - `archive_people`, `family_graph_edges`, `archive_claims`, `archive_conflicts`, `archive_corrections`

---

## 4. Privacy Portability & Data Export (GDPR / Right of Access)

Family members have the right to download all accumulated memory, biographical entities, kinship graphs, and stories in a portable format:

```http
GET /v1/families/{family_id}/privacy/export
```
- **Capability Required**: `read_family` (`OWNER`, `EDITOR`, or `VIEWER`).
- **Response**: `200 OK` with `Content-Type: application/json`.

### 4.1 Export Schema Overview
```json
{
  "family": {
    "family_id": "fam_...",
    "name": "Almaty Family Archive",
    "created_at": "2026-09-01T12:00:00Z"
  },
  "members": [
    {
      "user_id": "usr_...",
      "role": "owner",
      "display_name": "Erlan",
      "joined_at": "2026-09-01T12:00:00Z"
    }
  ],
  "recordings": [
    {
      "recording_id": "rec_...",
      "speaker_name": "Grandfather",
      "original_filename": "interview.m4a",
      "created_at": "2026-09-02T10:30:00Z"
    }
  ],
  "people": [
    {
      "person_id": "per_...",
      "canonical_name": "Erlan Senior",
      "category": "family",
      "created_at": "2026-09-02T10:35:00Z"
    }
  ],
  "relationships": [
    {
      "edge_id": "edg_...",
      "relationship_type": "parent_child",
      "subject_person_id": "per_1",
      "subject_role": "parent",
      "object_person_id": "per_2",
      "object_role": "child",
      "source_claim_ids": ["clm_..."]
    }
  ],
  "stories": [
    {
      "story_id": "st_...",
      "recording_id": "rec_...",
      "title": "Childhood in the Village",
      "summary": "Memories of the apple orchards in summer 1954...",
      "created_at": "2026-09-02T10:40:00Z"
    }
  ],
  "books": [
    {
      "book_id": "bk_...",
      "title": "Echoes of the Steppe",
      "output_language": "kz",
      "word_count": 14200,
      "created_at": "2026-09-10T15:00:00Z"
    }
  ],
  "exported_at": "2026-09-19T14:00:00Z"
}
```

