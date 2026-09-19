"""Family Book source snapshot compiler.

The Grounding Compiler transforms an authorized family archive into an immutable,
content-hashed grounding bundle (BookSourceSnapshot).

Load-bearing guarantees:
1. Pure and deterministic: identical archive content yields identical content_hash.
2. Grounded only: allowed_years and material_anchor_candidates are derived strictly
   from evidence and archive records.
3. Precision preservation: DateView precision is preserved in SnapshotDate.
4. Disagreements survive: corrections, uncertainties, and conflicts are preserved.
5. Zero content logging: no transcript, quote, or literary text is ever emitted to logs.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from mura.domain.book_models import (
    SNAPSHOT_SCHEMA_VERSION,
    BookSourceSnapshot,
    CompiledSnapshot,
    SnapshotClaim,
    SnapshotConflict,
    SnapshotCorrection,
    SnapshotDate,
    SnapshotEvent,
    SnapshotEvidence,
    SnapshotManifest,
    SnapshotPerson,
    SnapshotRelationship,
    SnapshotStory,
    SnapshotUncertainty,
)
from mura.storage.archive_read import (
    ArchiveReadRepository,
    GroundingBundle,
    grounding_bundle,
)
from mura.storage.database import Database, utcnow

COMPILER_VERSION = "mura-book-snapshot-compiler-v1"

_YEAR_REGEX = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")

_MATERIAL_ANCHOR_KEYWORDS: tuple[str, ...] = (
    # Russian objects / heirlooms
    "домбра",
    "самовар",
    "кольцо",
    "письмо",
    "письма",
    "часы",
    "сундук",
    "ковёр",
    "ковер",
    "альбом",
    "медаль",
    "медали",
    "шапан",
    "книга",
    "книги",
    "фотография",
    "фотографии",
    "фотоальбом",
    "серьги",
    "браслет",
    "кулон",
    "шкатулка",
    "дневник",
    "библия",
    "коран",
    "серебряная ложка",
    "ложка",
    "платок",
    "рукопись",
    "орден",
    "награда",
    "пояс",
    "тюбетейка",
    "картина",
    # Kazakh objects / heirlooms
    "домбыра",
    "құран",
    "қамшы",
    "торсық",
    "кебеже",
    "тұмар",
    "жүзік",
    "білезік",
    "сырға",
    "ер-тоқым",  # noqa: RUF001
    "бесік",
    "кітап",
    "хат",
    "сағат",
    "сандық",
    "кілем",
    "тон",
    "бөрік",
    "сәукеле",
    "тақия",
)


def _parse_snapshot_date(raw: Any) -> SnapshotDate | None:
    if raw is None:
        return None
    if isinstance(raw, SnapshotDate):
        return raw
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    if isinstance(raw, dict):
        val = raw.get("normalized_value") or raw.get("value")
        orig = raw.get("original_expression")
        prec = str(raw.get("precision") or "unknown")
        approx = bool(raw.get("approximate", False))
        if val is None and orig is None:
            return None
        return SnapshotDate(
            value=val,
            precision=prec,
            original_expression=orig,
            approximate=approx,
        )
    if isinstance(raw, str) and raw.strip():
        return SnapshotDate(value=raw.strip(), precision="unknown")
    return None


def _extract_years_from_text(text: str | None) -> list[int]:
    if not text:
        return []
    matches = _YEAR_REGEX.findall(text)
    return [int(m) for m in matches]


def _harvest_material_candidates(evidence_texts: list[str]) -> list[str]:
    """Harvest object and heirloom candidate phrases grounded in evidence quotes."""
    found: set[str] = set()
    for text in evidence_texts:
        lower_text = text.lower()
        for kw in _MATERIAL_ANCHOR_KEYWORDS:
            if kw in lower_text:
                # Find occurrences and extract the normalized matching keyword
                start_idx = 0
                while True:
                    pos = lower_text.find(kw, start_idx)
                    if pos == -1:
                        break
                    # Word boundary check
                    is_start = pos == 0 or not lower_text[pos - 1].isalnum()
                    end_pos = pos + len(kw)
                    is_end = end_pos == len(lower_text) or not lower_text[end_pos].isalnum()
                    if is_start and is_end:
                        candidate = kw.strip().lower()
                        # Strict invariant: candidate must be in evidence text
                        if candidate and candidate in lower_text:
                            found.add(candidate)
                    start_idx = end_pos
    return sorted(found)


def compute_content_hash(snapshot: BookSourceSnapshot) -> str:
    """Compute deterministic SHA-256 hash over canonical snapshot JSON.

    Excludes volatile manifest.created_at so identical archive content yields
    the exact same content_hash regardless of timestamp.
    """
    data = snapshot.model_dump(mode="json")
    if "manifest" in data and isinstance(data["manifest"], dict):
        manifest_copy = dict(data["manifest"])
        manifest_copy.pop("created_at", None)
        data["manifest"] = manifest_copy
    canonical_json = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compile_source_snapshot(
    source: GroundingBundle | ArchiveReadRepository | Database | Session,
    *,
    family_id: str | None = None,
    recording_ids: list[str] | None = None,
    max_recordings: int = 100,
    max_evidence_quotes: int = 400,
    created_at: datetime | None = None,
) -> CompiledSnapshot:
    """Compile an authorized family archive into an immutable CompiledSnapshot."""
    if isinstance(source, GroundingBundle):
        bundle = source
    else:
        if family_id is None:
            raise ValueError("family_id is required when source is a database/repository")
        bundle = grounding_bundle(
            source,
            family_id=family_id,
            recording_ids=recording_ids,
            max_recordings=max_recordings,
        )

    snapshot_created_at = created_at or utcnow()

    # 1. Harvest evidence spans from pipeline payloads
    all_evidence: list[SnapshotEvidence] = []
    observed_languages_set: set[str] = set()

    for rec in bundle.recordings:
        det_lang = rec.get("detected_language")
        if det_lang:
            observed_languages_set.add(det_lang)

    speaker_map = {r["recording_id"]: r.get("speaker_name", "Narrator") for r in bundle.recordings}

    referenced_evidence_ids: set[str] = set()
    for st in bundle.stories:
        for eid in st.get("evidence_ids", []):
            if isinstance(eid, str):
                referenced_evidence_ids.add(eid)
    for ev in bundle.events:
        for eid in ev.get("evidence_ids", []):
            if isinstance(eid, str):
                referenced_evidence_ids.add(eid)
    for cl in bundle.claims:
        for eid in cl.get("evidence_ids", []):
            if isinstance(eid, str):
                referenced_evidence_ids.add(eid)

    for rec_id, payload in bundle.pipeline_payloads.items():
        if not isinstance(payload, dict):
            continue
        extraction = payload.get("extraction", {})
        if not isinstance(extraction, dict):
            continue
        for lang in extraction.get("languages", []):
            if isinstance(lang, str) and lang:
                observed_languages_set.add(lang)

        speaker_name = speaker_map.get(rec_id, "Narrator")
        for span in extraction.get("evidence_spans", []):
            if not isinstance(span, dict):
                continue
            eid = span.get("evidence_id")
            text = span.get("text")
            if not eid or not text or not isinstance(text, str) or not text.strip():
                continue
            all_evidence.append(
                SnapshotEvidence(
                    evidence_id=str(eid),
                    recording_id=rec_id,
                    speaker_name=speaker_name,
                    text=text.strip(),
                    source_layer="raw_transcript",
                    person_ids=[],
                )
            )

    # Sort evidence deterministically
    all_evidence.sort(key=lambda e: (e.recording_id, e.evidence_id))

    # Apply max_evidence_quotes cap, prioritizing referenced evidence
    if len(all_evidence) > max_evidence_quotes:
        prio_evidence: list[SnapshotEvidence] = []
        rest_evidence: list[SnapshotEvidence] = []
        for ev in all_evidence:
            if ev.evidence_id in referenced_evidence_ids:
                prio_evidence.append(ev)
            else:
                rest_evidence.append(ev)
        remaining = max_evidence_quotes - len(prio_evidence)
        if remaining > 0:
            evidence_list = prio_evidence + rest_evidence[:remaining]
        else:
            evidence_list = prio_evidence[:max_evidence_quotes]
    else:
        evidence_list = all_evidence

    evidence_texts = [e.text for e in evidence_list]

    # 2. Material anchor candidates
    material_anchor_candidates = _harvest_material_candidates(evidence_texts)

    # 3. People
    people: list[SnapshotPerson] = []
    known_places_set: set[str] = set()
    years_set: set[int] = set()

    for p in bundle.people:
        b_date = _parse_snapshot_date(p.get("birth_date"))
        d_date = _parse_snapshot_date(p.get("death_date"))
        if b_date:
            years_set.update(_extract_years_from_text(b_date.value))
            years_set.update(_extract_years_from_text(b_date.original_expression))
        if d_date:
            years_set.update(_extract_years_from_text(d_date.value))
            years_set.update(_extract_years_from_text(d_date.original_expression))

        professions = [
            str(x).strip() for x in p.get("professions", []) if isinstance(x, str) and x.strip()
        ]
        locations = [
            str(x).strip() for x in p.get("locations", []) if isinstance(x, str) and x.strip()
        ]
        for loc in locations:
            known_places_set.add(loc)

        relations = p.get("relations_to_speakers")
        rel_str: str | None = None
        if isinstance(relations, dict):
            for v in relations.values():
                if isinstance(v, str) and v.strip():
                    rel_str = v.strip()
                    break

        people.append(
            SnapshotPerson(
                person_id=p["person_id"],
                display_name=p["canonical_name"],
                aliases=sorted(set(p.get("verified_aliases", []) or p.get("aliases", []))),
                category=str(p.get("category") or "unknown"),
                relation_to_speaker=rel_str,
                birth_date=b_date,
                death_date=d_date,
                professions=sorted(set(professions)),
                locations=sorted(set(locations)),
                descriptions=[],
                source_recording_ids=sorted(set(p.get("source_recording_ids", []))),
            )
        )

    people.sort(key=lambda x: x.person_id)
    known_person_ids = {p.person_id for p in people}

    # 4. Relationships (edges where both endpoints exist in known people)
    relationships: list[SnapshotRelationship] = []
    for r in bundle.relationships:
        sub_id = r.get("subject_person_id")
        obj_id = r.get("object_person_id")
        if sub_id in known_person_ids and obj_id in known_person_ids:
            relationships.append(
                SnapshotRelationship(
                    edge_id=r["edge_id"],
                    relationship_type=r["relationship_type"],
                    subject_person_id=sub_id,
                    subject_role=r.get("subject_role", ""),
                    object_person_id=obj_id,
                    object_role=r.get("object_role", ""),
                    source_claim_ids=sorted(set(r.get("source_claim_ids", []))),
                )
            )
    relationships.sort(key=lambda x: x.edge_id)

    # 5. Events
    events: list[SnapshotEvent] = []
    for e in bundle.events:
        payload = e.get("payload", {}) if isinstance(e.get("payload"), dict) else {}
        title = payload.get("title") or e.get("source_object_id") or "Family Event"
        desc = payload.get("description")
        loc = payload.get("location")
        if loc and isinstance(loc, str) and loc.strip():
            known_places_set.add(loc.strip())

        ev_date = _parse_snapshot_date(payload.get("date"))
        if ev_date:
            years_set.update(_extract_years_from_text(ev_date.value))
            years_set.update(_extract_years_from_text(ev_date.original_expression))
        years_set.update(_extract_years_from_text(title))
        years_set.update(_extract_years_from_text(desc))

        mentions = payload.get("participant_mention_ids", [])
        part_ids: list[str] = []
        rec_id = e.get("recording_id", "")
        for mid in mentions:
            key = f"{rec_id}:{mid}"
            resolved_pid = bundle.resolved_mentions.get(key)
            if resolved_pid and resolved_pid in known_person_ids:
                part_ids.append(resolved_pid)

        events.append(
            SnapshotEvent(
                event_id=e.get("source_object_id") or e.get("claim_id", ""),
                title=title,
                event_type=str(payload.get("event_type") or "event"),
                description=desc,
                location=loc,
                date=ev_date,
                participant_person_ids=sorted(set(part_ids)),
                recording_id=rec_id or None,
                evidence_quote_ids=sorted(set(e.get("evidence_ids", []))),
            )
        )
    events.sort(key=lambda x: x.event_id)

    # 6. Stories
    stories: list[SnapshotStory] = []
    for s in bundle.stories:
        payload = s.get("payload", {}) if isinstance(s.get("payload"), dict) else {}
        rec_id = s.get("recording_id", "")
        mentions = payload.get("person_mention_ids", [])
        p_ids: list[str] = []
        for mid in mentions:
            key = f"{rec_id}:{mid}"
            resolved_pid = bundle.resolved_mentions.get(key)
            if resolved_pid and resolved_pid in known_person_ids:
                p_ids.append(resolved_pid)

        st_title = payload.get("title")
        st_summary = payload.get("summary")
        years_set.update(_extract_years_from_text(st_title))
        years_set.update(_extract_years_from_text(st_summary))

        stories.append(
            SnapshotStory(
                story_id=s.get("source_object_id") or s.get("claim_id", ""),
                title=st_title,
                summary=st_summary,
                recording_id=rec_id,
                speaker_name=speaker_map.get(rec_id, "Narrator"),
                person_ids=sorted(set(p_ids)),
                evidence_quote_ids=sorted(set(s.get("evidence_ids", []))),
            )
        )
    stories.sort(key=lambda x: x.story_id)

    # 7. Claims
    claims: list[SnapshotClaim] = []
    for c in bundle.claims:
        payload = c.get("payload", {}) if isinstance(c.get("payload"), dict) else {}
        summary = payload.get("summary") or payload.get("description")
        years_set.update(_extract_years_from_text(summary))

        sub_pid = c.get("subject_person_id")
        obj_pid = c.get("object_person_id")

        claims.append(
            SnapshotClaim(
                claim_id=c["claim_id"],
                recording_id=c["recording_id"],
                object_type=c["object_type"],
                predicate=c["predicate"],
                subject_person_id=sub_pid if sub_pid in known_person_ids else None,
                object_person_id=obj_pid if obj_pid in known_person_ids else None,
                evidence_class=c.get("evidence_class", "D_UNSPECIFIED"),
                assertion_mode=c.get("assertion_mode"),
                verification_status=c.get("verification_status", "unreviewed"),
                evidence_ids=sorted(set(c.get("evidence_ids", []))),
                summary=summary,
            )
        )
    claims.sort(key=lambda x: x.claim_id)

    # 8. Corrections
    corrections: list[SnapshotCorrection] = []
    for cor in bundle.corrections:
        years_set.update(_extract_years_from_text(cor.get("original_value")))
        years_set.update(_extract_years_from_text(cor.get("corrected_value")))
        years_set.update(_extract_years_from_text(cor.get("explanation")))
        corrections.append(
            SnapshotCorrection(
                correction_id=cor["correction_id"],
                recording_id=cor["recording_id"],
                kind=cor.get("kind", "self_correction"),
                subject=cor.get("subject"),
                original_value=cor["original_value"],
                corrected_value=cor["corrected_value"],
                explanation=cor.get("explanation", ""),
                confidence=cor.get("confidence", "unknown"),
            )
        )
    corrections.sort(key=lambda x: x.correction_id)

    # 9. Uncertainties
    uncertainties: list[SnapshotUncertainty] = []
    for u in bundle.unresolved_questions:
        payload = u.get("payload", {}) if isinstance(u.get("payload"), dict) else {}
        q_text = payload.get("question") or u.get("predicate") or ""
        years_set.update(_extract_years_from_text(q_text))
        uncertainties.append(
            SnapshotUncertainty(
                uncertainty_id=u["claim_id"],
                recording_id=u.get("recording_id"),
                kind="open_question",
                text=q_text,
                person_ids=[],
            )
        )
    uncertainties.sort(key=lambda x: x.uncertainty_id)

    # 10. Conflicts
    conflicts: list[SnapshotConflict] = []
    for cf in bundle.conflicts:
        c_ids = sorted(set(cf.get("claim_ids", [])))
        # Find which recordings these claims touch
        c_recs = sorted(
            {c.recording_id for c in claims if c.claim_id in c_ids}
        )
        conflicts.append(
            SnapshotConflict(
                conflict_id=cf["conflict_id"],
                conflict_type=cf.get("conflict_type", "factual"),
                status=cf.get("status", "open"),
                claim_ids=c_ids,
                preferred_claim_id=cf.get("preferred_claim_id"),
                rationale=cf.get("rationale", ""),
                resolution_note=cf.get("resolution_note"),
                recording_ids=c_recs,
            )
        )
    conflicts.sort(key=lambda x: x.conflict_id)

    # 11. Allowed years: add years from all evidence texts
    for t in evidence_texts:
        years_set.update(_extract_years_from_text(t))
    allowed_years = sorted(years_set)

    # 12. Build manifest
    manifest = SnapshotManifest(
        source_recording_ids=sorted([r["recording_id"] for r in bundle.recordings]),
        source_story_ids=sorted([s.story_id for s in stories]),
        source_claim_ids=sorted([c.claim_id for c in claims]),
        source_event_ids=sorted([e.event_id for e in events]),
        source_person_ids=sorted([p.person_id for p in people]),
        source_evidence_ids=sorted([e.evidence_id for e in evidence_list]),
        correction_count=len(corrections),
        uncertainty_count=len(uncertainties),
        conflict_count=len(conflicts),
        created_at=snapshot_created_at,
    )

    snapshot = BookSourceSnapshot(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        compiler_version=COMPILER_VERSION,
        family_id=bundle.family_id,
        manifest=manifest,
        people=people,
        relationships=relationships,
        events=events,
        stories=stories,
        claims=claims,
        corrections=corrections,
        uncertainties=uncertainties,
        conflicts=conflicts,
        evidence=evidence_list,
        allowed_years=allowed_years,
        material_anchor_candidates=material_anchor_candidates,
        observed_languages=sorted(observed_languages_set),
        known_places=sorted(known_places_set),
    )

    content_hash = compute_content_hash(snapshot)
    return CompiledSnapshot(snapshot=snapshot, content_hash=content_hash)
