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

from mura.book.relationship_semantics import relationship_semantics_match
from mura.book.snapshot_validation import (
    SnapshotClosureError,
    SnapshotSizeError,
    validate_snapshot_closure,
)
from mura.domain.book_models import (
    MAX_BOOK_EVIDENCE_QUOTES,
    MAX_BOOK_SNAPSHOT_BYTES,
    MAX_BOOK_SOURCE_RECORDINGS,
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
    max_recordings: int = MAX_BOOK_SOURCE_RECORDINGS,
    max_evidence_quotes: int = MAX_BOOK_EVIDENCE_QUOTES,
    max_snapshot_bytes: int = MAX_BOOK_SNAPSHOT_BYTES,
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
    normalized_selected_ids = sorted(
        set(
            recording_ids
            if recording_ids is not None
            else [
                str(row.get("recording_id")) for row in bundle.recordings if row.get("recording_id")
            ]
        )
    )
    selected_recording_set = set(normalized_selected_ids)
    bundle_recording_ids = {
        str(row.get("recording_id")) for row in bundle.recordings if row.get("recording_id")
    }
    missing_selected_recordings = sorted(selected_recording_set - bundle_recording_ids)
    if missing_selected_recordings:
        raise SnapshotClosureError(
            "selected recordings are missing from the grounding bundle: "
            f"{missing_selected_recordings}"
        )

    # 1. Harvest evidence spans from selected pipeline payloads only. The
    # compiler is a correctness boundary in its own right: even if a caller
    # accidentally hands it a broader GroundingBundle, excluded recording
    # metadata must not influence the immutable Book snapshot.
    all_evidence: list[SnapshotEvidence] = []
    observed_languages_set: set[str] = set()

    selected_bundle_recordings = [
        rec
        for rec in bundle.recordings
        if str(rec.get("recording_id") or "") in selected_recording_set
    ]
    for rec in selected_bundle_recordings:
        det_lang = rec.get("detected_language")
        if det_lang:
            observed_languages_set.add(det_lang)

    speaker_map = {
        r["recording_id"]: r.get("speaker_name", "Narrator") for r in selected_bundle_recordings
    }

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
        if rec_id not in selected_recording_set:
            continue
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

    # Mandatory provenance may never be truncated to satisfy a context cap.
    evidence_by_id = {item.evidence_id: item for item in all_evidence}
    missing_required_evidence = sorted(referenced_evidence_ids - set(evidence_by_id))
    if missing_required_evidence:
        raise SnapshotClosureError(
            "selected claims reference evidence missing from selected recordings: "
            f"{missing_required_evidence}"
        )

    required_evidence = [
        item for item in all_evidence if item.evidence_id in referenced_evidence_ids
    ]
    optional_evidence = [
        item for item in all_evidence if item.evidence_id not in referenced_evidence_ids
    ]
    if max_evidence_quotes >= 0 and len(required_evidence) > max_evidence_quotes:
        raise SnapshotSizeError(
            "required referenced evidence exceeds the supported Book snapshot budget"
        )
    if max_evidence_quotes < 0:
        evidence_list = all_evidence
    else:
        remaining = max(0, max_evidence_quotes - len(required_evidence))
        evidence_list = required_evidence + optional_evidence[:remaining]
        evidence_list.sort(key=lambda item: (item.recording_id, item.evidence_id))

    evidence_texts = [e.text for e in evidence_list]

    # 2. Material anchor candidates
    material_anchor_candidates = _harvest_material_candidates(evidence_texts)

    # 3. People
    people: list[SnapshotPerson] = []
    known_places_set: set[str] = set()
    years_set: set[int] = set()

    for p in bundle.people:
        generic_sources = sorted(
            {
                str(value)
                for value in p.get("source_recording_ids", [])
                if isinstance(value, str) and value
            }
        )
        generic_is_fully_selected = (
            bool(generic_sources) and set(generic_sources) <= selected_recording_set
        )
        raw_attribute_sources = (
            p.get("attribute_sources") if isinstance(p.get("attribute_sources"), dict) else {}
        )

        def attribute_sources(
            key: str,
            raw_sources: dict[str, object] = raw_attribute_sources,
            fallback_sources: list[str] = generic_sources,
            fallback_allowed: bool = generic_is_fully_selected,
        ) -> list[str]:
            raw = raw_sources.get(key)
            if isinstance(raw, list):
                values = sorted({str(value) for value in raw if isinstance(value, str) and value})
                return values if values and set(values) <= selected_recording_set else []
            # Backward-compatible safe case: the person row itself declares
            # that every contributing recording is selected. If an excluded
            # recording appears in the aggregate provenance, optional
            # attributes need their own explicit provenance or are omitted.
            return fallback_sources if fallback_allowed else []

        display_sources = attribute_sources("display_name")
        display_name = str(p.get("canonical_name") or "").strip()
        if not display_name or not display_sources:
            continue

        b_date = (
            _parse_snapshot_date(p.get("birth_date")) if attribute_sources("birth_date") else None
        )
        d_date = (
            _parse_snapshot_date(p.get("death_date")) if attribute_sources("death_date") else None
        )
        if b_date:
            years_set.update(_extract_years_from_text(b_date.value))
            years_set.update(_extract_years_from_text(b_date.original_expression))
        if d_date:
            years_set.update(_extract_years_from_text(d_date.value))
            years_set.update(_extract_years_from_text(d_date.original_expression))

        aliases: list[str] = []
        projected_sources: dict[str, list[str]] = {"display_name": display_sources}
        if b_date:
            projected_sources["birth_date"] = attribute_sources("birth_date")
        if d_date:
            projected_sources["death_date"] = attribute_sources("death_date")
        # Only aliases explicitly verified by entity resolution may become
        # Book identity forms. Falling back from an empty verified_aliases list
        # to raw aliases would promote an unverified mention variant into a
        # truth boundary and let prose resolve an invented/ambiguous name.
        raw_aliases = p.get("verified_aliases", [])
        if isinstance(raw_aliases, list):
            for value in raw_aliases:
                if not isinstance(value, str) or not value.strip():
                    continue
                alias = value.strip()
                sources = attribute_sources(f"alias:{alias}")
                if sources:
                    aliases.append(alias)
                    projected_sources[f"alias:{alias}"] = sources

        category_sources = attribute_sources("category")
        category = str(p.get("category") or "unknown") if category_sources else "unknown"
        if category_sources:
            projected_sources["category"] = category_sources

        relation_sources = attribute_sources("relation_to_speaker")
        relations = p.get("relations_to_speakers")
        rel_str: str | None = None
        if relation_sources and isinstance(relations, dict):
            for value in relations.values():
                if isinstance(value, str) and value.strip():
                    rel_str = value.strip()
                    break
        if rel_str:
            projected_sources["relation_to_speaker"] = relation_sources

        professions: list[str] = []
        for value in p.get("professions", []):
            if not isinstance(value, str) or not value.strip():
                continue
            profession = value.strip()
            sources = attribute_sources(f"profession:{profession}")
            if sources:
                professions.append(profession)
                projected_sources[f"profession:{profession}"] = sources

        locations: list[str] = []
        for value in p.get("locations", []):
            if not isinstance(value, str) or not value.strip():
                continue
            location = value.strip()
            sources = attribute_sources(f"location:{location}")
            if sources:
                locations.append(location)
                projected_sources[f"location:{location}"] = sources
                known_places_set.add(location)

        descriptions: list[str] = []
        raw_descriptions = p.get("descriptions", [])
        if isinstance(raw_descriptions, list):
            for value in raw_descriptions:
                if not isinstance(value, str) or not value.strip():
                    continue
                description = value.strip()
                sources = attribute_sources(f"description:{description}")
                if sources:
                    descriptions.append(description)
                    projected_sources[f"description:{description}"] = sources

        source_ids = sorted(
            {recording_id for values in projected_sources.values() for recording_id in values}
        )
        people.append(
            SnapshotPerson(
                person_id=p["person_id"],
                display_name=display_name,
                aliases=sorted(set(aliases)),
                category=category,
                relation_to_speaker=rel_str,
                birth_date=b_date,
                death_date=d_date,
                professions=sorted(set(professions)),
                locations=sorted(set(locations)),
                descriptions=sorted(set(descriptions)),
                source_recording_ids=source_ids,
                attribute_sources=projected_sources,
            )
        )

    people.sort(key=lambda x: x.person_id)
    known_person_ids = {p.person_id for p in people}

    # 4. Relationships. A family graph edge is materialized family-wide;
    # only the selected supporting claims can authorize it for this Book.
    relationships: list[SnapshotRelationship] = []
    bundle_claim_by_id = {
        str(item.get("claim_id")): item for item in bundle.claims if item.get("claim_id")
    }
    for r in bundle.relationships:
        sub_id = r.get("subject_person_id")
        obj_id = r.get("object_person_id")
        support_ids: list[str] = []
        for claim_id in r.get("source_claim_ids", []):
            claim = bundle_claim_by_id.get(str(claim_id))
            if not claim:
                continue
            if claim.get("recording_id") not in selected_recording_set:
                continue
            if claim.get("object_type") != "relationship":
                continue
            payload = claim.get("payload") if isinstance(claim.get("payload"), dict) else {}
            if not relationship_semantics_match(
                left_type=str(payload.get("relationship_type") or claim.get("predicate") or ""),
                left_subject_person_id=claim.get("subject_person_id"),
                left_subject_role=str(payload.get("subject_role") or ""),
                left_object_person_id=claim.get("object_person_id"),
                left_object_role=str(payload.get("object_role") or ""),
                right_type=str(r.get("relationship_type") or ""),
                right_subject_person_id=sub_id,
                right_subject_role=str(r.get("subject_role") or ""),
                right_object_person_id=obj_id,
                right_object_role=str(r.get("object_role") or ""),
            ):
                continue
            support_ids.append(str(claim_id))

        if support_ids and sub_id in known_person_ids and obj_id in known_person_ids:
            relationships.append(
                SnapshotRelationship(
                    edge_id=r["edge_id"],
                    relationship_type=r["relationship_type"],
                    subject_person_id=sub_id,
                    subject_role=r.get("subject_role", ""),
                    object_person_id=obj_id,
                    object_role=r.get("object_role", ""),
                    source_claim_ids=sorted(set(support_ids)),
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
                subject_role=(
                    str(payload.get("subject_role"))
                    if c.get("object_type") == "relationship" and payload.get("subject_role")
                    else None
                ),
                object_person_id=obj_pid if obj_pid in known_person_ids else None,
                object_role=(
                    str(payload.get("object_role"))
                    if c.get("object_type") == "relationship" and payload.get("object_role")
                    else None
                ),
                evidence_class=c.get("evidence_class", "D_UNSPECIFIED"),
                assertion_mode=c.get("assertion_mode"),
                verification_status=c.get("verification_status", "unreviewed"),
                archive_status=c.get("archive_status", "active"),
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
        correction_kind = str(cor.get("kind") or "").casefold()
        correction_subject = str(cor.get("subject") or "").casefold()
        corrected_value = str(cor.get("corrected_value") or "").strip()
        if corrected_value and any(
            marker in correction_kind or marker in correction_subject
            for marker in (
                "city",
                "location",
                "place",
                "город",
                "мест",
                "қала",
                "ауыл",
                "жер",
            )
        ):
            known_places_set.add(corrected_value)
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

    # 10. Conflicts. Mixed selected/excluded conflicts are omitted rather
    # than importing the excluded side's content or rationale.
    conflicts: list[SnapshotConflict] = []
    included_claim_ids = {claim.claim_id for claim in claims}
    for cf in bundle.conflicts:
        c_ids = sorted(set(cf.get("claim_ids", [])))
        preferred = cf.get("preferred_claim_id")
        if (
            not c_ids
            or any(claim_id not in included_claim_ids for claim_id in c_ids)
            or (preferred is not None and preferred not in c_ids)
        ):
            continue
        c_recs = sorted({c.recording_id for c in claims if c.claim_id in c_ids})
        conflicts.append(
            SnapshotConflict(
                conflict_id=cf["conflict_id"],
                conflict_type=cf.get("conflict_type", "factual"),
                status=cf.get("status", "open"),
                claim_ids=c_ids,
                preferred_claim_id=preferred,
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
        source_recording_ids=normalized_selected_ids,
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

    validate_snapshot_closure(
        snapshot,
        expected_recording_ids=normalized_selected_ids,
    )
    snapshot_size = len(
        json.dumps(
            snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    if max_snapshot_bytes >= 0 and snapshot_size > max_snapshot_bytes:
        raise SnapshotSizeError(
            f"compiled Book snapshot is {snapshot_size} bytes; limit is {max_snapshot_bytes}"
        )
    content_hash = compute_content_hash(snapshot)
    return CompiledSnapshot(snapshot=snapshot, content_hash=content_hash)
