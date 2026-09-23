"""Product read models over the archive.

The archive is stored the way extraction produces it: claims keyed by object
type, an evidence-gated edge table, materialized profiles, conflicts. That is
the right shape for the pipeline and the wrong shape for a browser, which would
otherwise have to reassemble a family graph out of `archive_claims` payloads
and re-derive the rules about which of them may be trusted.

So the projection happens here, once, on the server. Two things follow from
that and are worth stating because they are easy to lose:

*Nothing is invented.* Every field below comes from a persisted row. Where the
archive does not know something -- a date, a title, who someone is -- the DTO
says so with a null rather than a plausible default, because a family archive
that fills in gaps convincingly is worse than one that admits them.

*The trust rules stay in Core.* Which relationship claims are strong enough to
appear as an edge is already decided when the graph is materialized, using the
evidence-class ladder. This module reads that decision; it does not re-derive
it, and it must never introduce a confidence threshold of its own.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mura.book.relationship_semantics import relationship_semantics_match
from mura.book.truth_eligibility import is_book_truth_eligible
from mura.domain.models import ClaimObjectType, StrictModel
from mura.storage.archive import (
    ArchiveClaimRow,
    ArchiveConflictRow,
    ArchiveCorrectionRow,
    ArchivePersonRow,
    FamilyGraphEdgeRow,
)
from mura.storage.database import Database, PipelineResultRow, RecordingRow

#: Claims whose object never appears in the product as a first-class thing.
STORY_PREDICATE = "story"
QUESTION_PREDICATE = "question"

#: A hard ceiling on any list endpoint. A family archive grows for years, and
#: an unbounded list would eventually return every story ever recorded to a
#: phone. Callers page with `limit`/`offset`; this is the backstop.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

#: Longest excerpt a *list* endpoint will ever return. Detail endpoints return
#: the stored summary in full; a list must not become a way to pull every
#: transcript in the family in one request.
LIST_EXCERPT_CHARS = 280


class DateView(StrictModel):
    """A date the archive actually knows, with its precision preserved.

    `precision` is the point. Rendering "1 января 1978" when the archive only
    ever heard "в семьдесят восьмом" invents a day and a month nobody said.
    """

    value: str | None = None
    precision: str = "unknown"
    original_expression: str | None = None
    approximate: bool = False


class SourceView(StrictModel):
    """Where a fact came from, in terms a person can follow.

    Deliberately not the evidence machinery: `evidence_class`, assertion mode
    and confidence stay on the row. What a family member needs is which story,
    told by whom, on what recording.
    """

    recording_id: str
    speaker_name: str
    recorded_at: datetime
    claim_ids: list[str] = Field(default_factory=list)


class PersonSummaryView(StrictModel):
    person_id: str
    display_name: str
    #: Only aliases entity resolution actually verified.
    aliases: list[str] = Field(default_factory=list)
    category: str
    #: "бабушка", "муж" — as said about the speaker, never inferred.
    relation_to_speaker: str | None = None
    story_count: int = 0
    recording_count: int = 0


class RelationshipView(StrictModel):
    """One materialized edge. Both endpoints are canonical people."""

    edge_id: str
    relationship_type: str
    subject_person_id: str
    subject_role: str
    object_person_id: str
    object_role: str
    source_claim_ids: list[str] = Field(default_factory=list)


class StorySummaryView(StrictModel):
    story_id: str
    title: str | None = None
    excerpt: str | None = None
    recording_id: str
    speaker_name: str
    recorded_at: datetime
    person_ids: list[str] = Field(default_factory=list)


class StoryDetailView(StrictModel):
    story_id: str
    title: str | None = None
    summary: str | None = None
    recording_id: str
    speaker_name: str
    recorded_at: datetime
    people: list[PersonSummaryView] = Field(default_factory=list)
    events: list[EventView] = Field(default_factory=list)
    source: SourceView
    #: True only when canonical audio is genuinely retrievable for this
    #: recording. The UI must not offer a player on the strength of a guess.
    audio_available: bool = False
    evidence_quotes: list[str] = Field(default_factory=list)
    transcript: str | None = None


class EventView(StrictModel):
    event_id: str
    title: str
    event_type: str
    description: str | None = None
    location: str | None = None
    date: DateView | None = None
    participant_person_ids: list[str] = Field(default_factory=list)


class ReviewItemView(StrictModel):
    """Something the archive cannot settle on its own.

    `kind` is the backend's classification, not a frontend heuristic. The
    product asks the question in human words; the category decides which words.
    """

    review_id: str
    kind: str
    question: str
    detail: str | None = None
    recording_id: str | None = None
    person_ids: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class ArchiveOverviewView(StrictModel):
    family_id: str
    people_count: int = 0
    relationship_count: int = 0
    story_count: int = 0
    recording_count: int = 0
    review_count: int = 0
    open_conflict_count: int = 0
    recent_stories: list[StorySummaryView] = Field(default_factory=list)


class Page(StrictModel):
    total: int = 0
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


class StoryPageView(StrictModel):
    page: Page
    items: list[StorySummaryView] = Field(default_factory=list)


class GroundingBundle(StrictModel):
    """The raw, authorized grounding material loaded for one family book."""

    family_id: str
    recordings: list[dict[str, Any]] = Field(default_factory=list)
    pipeline_payloads: dict[str, dict[str, Any]] = Field(default_factory=dict)
    people: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    stories: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_questions: list[dict[str, Any]] = Field(default_factory=list)
    resolved_mentions: dict[str, str] = Field(default_factory=dict)


class ArchiveResourceNotFound(LookupError):
    """Absent, or belonging to another family. The caller is told neither."""


class GroundingSourceLimitExceeded(ValueError):
    """The requested immutable Book source set exceeds the supported limit."""

    def __init__(self, *, count: int, limit: int) -> None:
        super().__init__(f"book source selection contains {count} recordings; limit is {limit}")
        self.count = count
        self.limit = limit


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    if len(collapsed) <= LIST_EXCERPT_CHARS:
        return collapsed
    return collapsed[: LIST_EXCERPT_CHARS - 1].rstrip() + "…"


def _date_view(payload: Any) -> DateView | None:
    if not isinstance(payload, dict):
        return None
    return DateView(
        value=payload.get("normalized_value") or payload.get("value"),
        precision=str(payload.get("precision") or "unknown"),
        original_expression=payload.get("original_expression"),
        approximate=bool(payload.get("approximate", False)),
    )


class ArchiveReadRepository:
    """Family-scoped reads. Every query is filtered by `family_id`.

    Scoping is a predicate in the query, never a fetch-then-compare: a row
    belonging to another family is not loaded and then rejected, it is never
    selected. That is what keeps a guessed id from being a timing signal.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    # ------------------------------------------------------------------ people

    def list_people(self, *, family_id: str) -> list[PersonSummaryView]:
        with self._database.session_factory() as session:
            people = list(
                session.scalars(
                    select(ArchivePersonRow)
                    .where(ArchivePersonRow.family_id == family_id)
                    .order_by(ArchivePersonRow.canonical_name)
                )
            )
            if not people:
                return []
            return self._person_views(session, family_id=family_id, rows=people)

    def get_person(self, *, family_id: str, person_id: str) -> PersonSummaryView:
        with self._database.session_factory() as session:
            row = session.scalars(
                select(ArchivePersonRow).where(
                    ArchivePersonRow.family_id == family_id,
                    ArchivePersonRow.person_id == person_id,
                )
            ).one_or_none()
            if row is None:
                raise ArchiveResourceNotFound(person_id)
            return self._person_views(session, family_id=family_id, rows=[row])[0]

    def _person_views(
        self,
        session: Session,
        *,
        family_id: str,
        rows: list[ArchivePersonRow],
    ) -> list[PersonSummaryView]:
        """One extra query for all people, not one per person.

        The obvious shape here is a story count per person, which on a family
        with a hundred people is a hundred round trips. Story claims are read
        once and counted in memory instead.
        """

        story_claims = list(
            session.scalars(
                select(ArchiveClaimRow).where(
                    ArchiveClaimRow.family_id == family_id,
                    ArchiveClaimRow.object_type == ClaimObjectType.STORY.value,
                    ArchiveClaimRow.status == "active",
                )
            )
        )
        resolved = resolve_mentions(
            session,
            family_id=family_id,
            recording_ids={claim.recording_id for claim in story_claims},
        )
        stories_by_person: dict[str, int] = defaultdict(int)
        for claim in story_claims:
            for person_id in _story_person_ids(claim, resolved):
                stories_by_person[person_id] += 1

        return [
            PersonSummaryView(
                person_id=row.person_id,
                display_name=row.canonical_name,
                # Verified aliases only: an unverified variant is a guess about
                # who someone is, which is exactly what must not be presented
                # as a fact about a family member.
                aliases=list(row.verified_aliases or []),
                category=row.category,
                relation_to_speaker=_first_relation(row.relations_to_speakers),
                story_count=stories_by_person.get(row.person_id, 0),
                recording_count=len(row.source_recording_ids or []),
            )
            for row in rows
        ]

    # ----------------------------------------------------------- relationships

    def list_relationships(self, *, family_id: str) -> list[RelationshipView]:
        """The materialized graph, as decided when the archive was written.

        Only edges whose endpoints are both still canonical people are
        returned, so the frontend can render an edge without checking that the
        nodes exist.
        """

        with self._database.session_factory() as session:
            known = set(
                session.scalars(
                    select(ArchivePersonRow.person_id).where(
                        ArchivePersonRow.family_id == family_id
                    )
                )
            )
            edges = session.scalars(
                select(FamilyGraphEdgeRow)
                .where(FamilyGraphEdgeRow.family_id == family_id)
                .order_by(FamilyGraphEdgeRow.edge_id)
            )
            return [
                RelationshipView(
                    edge_id=edge.edge_id,
                    relationship_type=edge.relationship_type,
                    subject_person_id=edge.subject_person_id,
                    subject_role=edge.subject_role,
                    object_person_id=edge.object_person_id,
                    object_role=edge.object_role,
                    source_claim_ids=list(edge.source_claim_ids or []),
                )
                for edge in edges
                if edge.subject_person_id in known and edge.object_person_id in known
            ]

    # ----------------------------------------------------------------- stories

    def list_stories(
        self,
        *,
        family_id: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> StoryPageView:
        size = clamp_limit(limit)
        start = max(0, offset)
        with self._database.session_factory() as session:
            total = (
                session.scalar(
                    select(func.count())
                    .select_from(ArchiveClaimRow)
                    .where(
                        ArchiveClaimRow.family_id == family_id,
                        ArchiveClaimRow.object_type == ClaimObjectType.STORY.value,
                        ArchiveClaimRow.status == "active",
                    )
                )
                or 0
            )
            claims = list(
                session.scalars(
                    select(ArchiveClaimRow)
                    .where(
                        ArchiveClaimRow.family_id == family_id,
                        ArchiveClaimRow.object_type == ClaimObjectType.STORY.value,
                        ArchiveClaimRow.status == "active",
                    )
                    # Newest first by when the memory was recorded. There is no
                    # invented chronology here: an undated story is ordered by
                    # its recording, not by a date nobody said.
                    .order_by(ArchiveClaimRow.created_at.desc(), ArchiveClaimRow.claim_id)
                    .limit(size)
                    .offset(start)
                )
            )
            recordings = self._recordings_for(session, family_id=family_id, claims=claims)
            resolved = resolve_mentions(
                session,
                family_id=family_id,
                recording_ids={claim.recording_id for claim in claims},
            )
            items = [
                self._story_summary(claim, recordings.get(claim.recording_id), resolved)
                for claim in claims
            ]
            return StoryPageView(
                page=Page(total=total, limit=size, offset=start),
                items=[item for item in items if item is not None],
            )

    def get_story(self, *, family_id: str, story_id: str) -> StoryDetailView:
        with self._database.session_factory() as session:
            claim = session.scalars(
                select(ArchiveClaimRow).where(
                    ArchiveClaimRow.family_id == family_id,
                    ArchiveClaimRow.object_type == ClaimObjectType.STORY.value,
                    ArchiveClaimRow.source_object_id == story_id,
                    ArchiveClaimRow.status == "active",
                )
            ).first()
            if claim is None:
                raise ArchiveResourceNotFound(story_id)

            recording = session.get(RecordingRow, claim.recording_id)
            if recording is None or recording.family_id != family_id:
                raise ArchiveResourceNotFound(story_id)

            # Evidence grounding: the narrator's exact words backing the story.
            pipeline_row = session.get(PipelineResultRow, claim.recording_id)
            evidence_quotes: list[str] = []
            clean_transcript: str | None = None
            if pipeline_row is not None and isinstance(pipeline_row.payload, dict):
                extraction = pipeline_row.payload.get("extraction", {})
                if isinstance(extraction, dict):
                    for span in extraction.get("evidence_spans", []):
                        if isinstance(span, dict):
                            text = span.get("text")
                            if isinstance(text, str) and text.strip():
                                evidence_quotes.append(text.strip())
                cleaned = pipeline_row.payload.get("cleaned_transcript", {})
                if isinstance(cleaned, dict):
                    readable = cleaned.get("full_readable_text")
                    if isinstance(readable, str) and readable.strip():
                        clean_transcript = readable.strip()

            payload = claim.payload if isinstance(claim.payload, dict) else {}
            person_ids = _story_person_ids(
                claim,
                resolve_mentions(
                    session,
                    family_id=family_id,
                    recording_ids={claim.recording_id},
                ),
            )

            people = (
                self._person_views(
                    session,
                    family_id=family_id,
                    rows=list(
                        session.scalars(
                            select(ArchivePersonRow).where(
                                ArchivePersonRow.family_id == family_id,
                                ArchivePersonRow.person_id.in_(person_ids),
                            )
                        )
                    ),
                )
                if person_ids
                else []
            )

            return StoryDetailView(
                story_id=story_id,
                title=_clean(payload.get("title")),
                summary=_clean(payload.get("summary")),
                recording_id=claim.recording_id,
                speaker_name=recording.speaker_name,
                recorded_at=recording.created_at,
                people=people,
                events=self._events_for(
                    session,
                    family_id=family_id,
                    event_ids=[
                        value for value in payload.get("event_ids", []) if isinstance(value, str)
                    ],
                ),
                source=SourceView(
                    recording_id=claim.recording_id,
                    speaker_name=recording.speaker_name,
                    recorded_at=recording.created_at,
                    claim_ids=[claim.claim_id],
                ),
                # A storage key is the only proof the bytes exist; a legacy row
                # with only an audio_path is not offered for playback.
                audio_available=bool(recording.storage_key),
                evidence_quotes=evidence_quotes,
                transcript=clean_transcript,
            )

    def _recordings_for(
        self,
        session: Session,
        *,
        family_id: str,
        claims: list[ArchiveClaimRow],
    ) -> dict[str, RecordingRow]:
        ids = {claim.recording_id for claim in claims}
        if not ids:
            return {}
        rows = session.scalars(
            select(RecordingRow).where(
                RecordingRow.family_id == family_id,
                RecordingRow.recording_id.in_(ids),
            )
        )
        return {row.recording_id: row for row in rows}

    @staticmethod
    def _story_summary(
        claim: ArchiveClaimRow,
        recording: RecordingRow | None,
        resolved: dict[tuple[str, str], str],
    ) -> StorySummaryView | None:
        if recording is None:
            return None
        payload = claim.payload if isinstance(claim.payload, dict) else {}
        return StorySummaryView(
            story_id=claim.source_object_id,
            title=_clean(payload.get("title")),
            excerpt=_excerpt(_clean(payload.get("summary"))),
            recording_id=claim.recording_id,
            speaker_name=recording.speaker_name,
            recorded_at=recording.created_at,
            person_ids=_story_person_ids(claim, resolved),
        )

    def _events_for(
        self,
        session: Session,
        *,
        family_id: str,
        event_ids: list[str],
    ) -> list[EventView]:
        if not event_ids:
            return []
        claims = session.scalars(
            select(ArchiveClaimRow).where(
                ArchiveClaimRow.family_id == family_id,
                ArchiveClaimRow.object_type == ClaimObjectType.EVENT.value,
                ArchiveClaimRow.source_object_id.in_(event_ids),
                ArchiveClaimRow.status == "active",
            )
        )
        views: list[EventView] = []
        for claim in claims:
            payload = claim.payload if isinstance(claim.payload, dict) else {}
            title = _clean(payload.get("title"))
            if not title:
                continue
            views.append(
                EventView(
                    event_id=claim.source_object_id,
                    title=title,
                    event_type=str(payload.get("event_type") or "event"),
                    description=_clean(payload.get("description")),
                    location=_clean(payload.get("location")),
                    date=_date_view(payload.get("date")),
                    participant_person_ids=[],
                )
            )
        return views

    # ------------------------------------------------------------------ review

    def list_review_items(self, *, family_id: str) -> list[ReviewItemView]:
        """Everything the archive says needs a person to look at it.

        Two sources, both persisted: questions extraction could not resolve,
        and conflicts the archive detected between claims. There is no
        frontend rule deciding what counts as needing review -- that would put
        the definition of uncertainty in the client.
        """

        with self._database.session_factory() as session:
            questions = session.scalars(
                select(ArchiveClaimRow)
                .where(
                    ArchiveClaimRow.family_id == family_id,
                    ArchiveClaimRow.object_type == ClaimObjectType.QUESTION.value,
                    ArchiveClaimRow.status == "active",
                )
                .order_by(ArchiveClaimRow.created_at.desc())
            )
            items: list[ReviewItemView] = []
            for claim in questions:
                payload = claim.payload if isinstance(claim.payload, dict) else {}
                question = _clean(payload.get("question"))
                if not question:
                    continue
                items.append(
                    ReviewItemView(
                        review_id=claim.claim_id,
                        kind="open_question",
                        question=question,
                        detail=_clean(payload.get("reason")),
                        recording_id=claim.recording_id,
                        created_at=claim.created_at,
                    )
                )

            conflicts = session.scalars(
                select(ArchiveConflictRow)
                .where(
                    ArchiveConflictRow.family_id == family_id,
                    ArchiveConflictRow.status == "open",
                )
                .order_by(ArchiveConflictRow.created_at.desc())
            )
            for conflict in conflicts:
                items.append(
                    ReviewItemView(
                        review_id=conflict.conflict_id,
                        kind=f"conflict:{conflict.conflict_type}",
                        question=conflict.rationale,
                        detail=None,
                        recording_id=None,
                        created_at=conflict.created_at,
                    )
                )
            return items

    # ---------------------------------------------------------------- overview

    def overview(self, *, family_id: str) -> ArchiveOverviewView:
        stories = self.list_stories(family_id=family_id, limit=5)
        with self._database.session_factory() as session:
            people_count = (
                session.scalar(
                    select(func.count())
                    .select_from(ArchivePersonRow)
                    .where(ArchivePersonRow.family_id == family_id)
                )
                or 0
            )
            relationship_count = (
                session.scalar(
                    select(func.count())
                    .select_from(FamilyGraphEdgeRow)
                    .where(FamilyGraphEdgeRow.family_id == family_id)
                )
                or 0
            )
            recording_count = (
                session.scalar(
                    select(func.count())
                    .select_from(RecordingRow)
                    .where(RecordingRow.family_id == family_id)
                )
                or 0
            )
            open_conflicts = (
                session.scalar(
                    select(func.count())
                    .select_from(ArchiveConflictRow)
                    .where(
                        ArchiveConflictRow.family_id == family_id,
                        ArchiveConflictRow.status == "open",
                    )
                )
                or 0
            )
            open_questions = (
                session.scalar(
                    select(func.count())
                    .select_from(ArchiveClaimRow)
                    .where(
                        ArchiveClaimRow.family_id == family_id,
                        ArchiveClaimRow.object_type == ClaimObjectType.QUESTION.value,
                        ArchiveClaimRow.status == "active",
                    )
                )
                or 0
            )

        return ArchiveOverviewView(
            family_id=family_id,
            people_count=people_count,
            relationship_count=relationship_count,
            story_count=stories.page.total,
            recording_count=recording_count,
            review_count=open_questions + open_conflicts,
            open_conflict_count=open_conflicts,
            recent_stories=stories.items,
        )

    # ---------------------------------------------------- grounding bundle

    def grounding_bundle(
        self,
        *,
        family_id: str,
        recording_ids: list[str] | None = None,
        max_recordings: int = 100,
    ) -> GroundingBundle:
        with self._database.session_factory() as session:
            return self._grounding_bundle_in_session(
                session,
                family_id=family_id,
                recording_ids=recording_ids,
                max_recordings=max_recordings,
            )

    def _grounding_bundle_in_session(
        self,
        session: Session,
        *,
        family_id: str,
        recording_ids: list[str] | None = None,
        max_recordings: int = 100,
    ) -> GroundingBundle:
        from mura.storage.book import get_eligible_recordings

        eligible = get_eligible_recordings(
            session,
            family_id=family_id,
            recording_ids=recording_ids,
        )
        if recording_ids is not None:
            found_ids = {r.recording_id for r in eligible}
            missing = [rid for rid in recording_ids if rid not in found_ids]
            if missing:
                raise ArchiveResourceNotFound(
                    f"recording {missing[0]} not found or not eligible"
                )

        if max_recordings and len(eligible) > max_recordings:
            # An immutable Book may never silently change "selected A..Z" into
            # "the first N recordings". Refuse the source set before snapshot
            # compilation so the manifest remains an exact contract.
            raise GroundingSourceLimitExceeded(
                count=len(eligible),
                limit=max_recordings,
            )

        rec_ids = [r.recording_id for r in eligible]
        if not rec_ids:
            return GroundingBundle(
                family_id=family_id,
                recordings=[],
                pipeline_payloads={},
                people=[],
                relationships=[],
                stories=[],
                events=[],
                claims=[],
                corrections=[],
                conflicts=[],
                unresolved_questions=[],
                resolved_mentions={},
            )

        pipeline_rows = list(
            session.scalars(
                select(PipelineResultRow).where(
                    PipelineResultRow.recording_id.in_(rec_ids)
                )
            )
        )
        pipeline_payloads = {
            row.recording_id: (row.payload if isinstance(row.payload, dict) else {})
            for row in pipeline_rows
        }

        candidate_claims = list(
            session.scalars(
                select(ArchiveClaimRow).where(
                    ArchiveClaimRow.family_id == family_id,
                    ArchiveClaimRow.recording_id.in_(rec_ids),
                    ArchiveClaimRow.status.in_(("active", "accepted", "disputed")),
                ).order_by(ArchiveClaimRow.created_at.asc(), ArchiveClaimRow.claim_id)
            )
        )
        candidate_claim_by_id = {claim.claim_id: claim for claim in candidate_claims}

        # Disputed claims are Book-eligible only when the complete conflict is
        # representable inside the exact selected source universe. Otherwise a
        # family-wide conflict caused by excluded recording C would change the
        # truth semantics of a Book intentionally scoped to A+B.
        conflict_rows = list(
            session.scalars(
                select(ArchiveConflictRow).where(ArchiveConflictRow.family_id == family_id)
            )
        )
        selected_conflict_claim_ids: set[str] = set()
        for conflict in conflict_rows:
            conflict_claim_ids = list(conflict.claim_ids or [])
            if (
                conflict_claim_ids
                and all(claim_id in candidate_claim_by_id for claim_id in conflict_claim_ids)
            ):
                selected_conflict_claim_ids.update(conflict_claim_ids)

        selected_recording_ids = set(rec_ids)
        claims = [
            claim
            for claim in candidate_claims
            if is_book_truth_eligible(
                claim,
                selected_recording_ids=selected_recording_ids,
                allow_disputed=claim.claim_id in selected_conflict_claim_ids,
            )
        ]

        resolved_tuples = resolve_mentions(
            session,
            family_id=family_id,
            recording_ids=set(rec_ids),
        )
        resolved_mentions = {
            f"{r_id}:{m_id}": pid for (r_id, m_id), pid in resolved_tuples.items()
        }

        stories: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        general_claims: list[dict[str, Any]] = []

        for c in claims:
            c_dict = {
                "claim_id": c.claim_id,
                "family_id": c.family_id,
                "recording_id": c.recording_id,
                "object_type": c.object_type,
                "source_object_id": c.source_object_id,
                "predicate": c.predicate,
                "subject_person_id": c.subject_person_id,
                "object_person_id": c.object_person_id,
                "payload": c.payload if isinstance(c.payload, dict) else {},
                "evidence_ids": list(c.evidence_ids or []),
                "evidence_class": c.evidence_class,
                "assertion_mode": c.assertion_mode,
                "verification_status": c.verification_status,
                "archive_status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            if c.object_type == ClaimObjectType.STORY.value:
                stories.append(c_dict)
            elif c.object_type == ClaimObjectType.EVENT.value:
                events.append(c_dict)
            elif c.object_type == ClaimObjectType.QUESTION.value:
                questions.append(c_dict)
            else:
                general_claims.append(c_dict)

        # Book people are projected from selected person-mention claims, not
        # copied from ArchivePersonRow. ArchivePersonRow is intentionally
        # family-wide/materialized: its aliases and relation map can aggregate
        # knowledge from recordings the user did not select.
        #
        # Every field emitted here therefore carries recording-level provenance.
        selected_person_claims: dict[str, list[ArchiveClaimRow]] = defaultdict(list)
        for claim in claims:
            if (
                claim.object_type == ClaimObjectType.PERSON_MENTION.value
                and claim.subject_person_id
            ):
                selected_person_claims[claim.subject_person_id].append(claim)

        speaker_id_by_recording = {
            row.recording_id: row.speaker_id for row in eligible
        }
        people: list[dict[str, Any]] = []
        for person_id in sorted(selected_person_claims):
            person_claims = sorted(
                selected_person_claims[person_id],
                key=lambda row: (
                    row.created_at.isoformat() if row.created_at else "",
                    row.recording_id,
                    row.claim_id,
                ),
            )
            names: list[tuple[str, str]] = []
            aliases_by_recording: dict[str, set[str]] = defaultdict(set)
            verified_aliases_by_recording: dict[str, set[str]] = defaultdict(set)
            category_candidates: list[tuple[str, str]] = []
            relation_candidates: list[tuple[str, str, str]] = []

            for claim in person_claims:
                payload = claim.payload if isinstance(claim.payload, dict) else {}
                name = _clean(payload.get("name"))
                if name:
                    names.append((name, claim.recording_id))

                raw_aliases = payload.get("aliases")
                if isinstance(raw_aliases, list):
                    for alias in raw_aliases:
                        clean_alias = _clean(alias)
                        if clean_alias:
                            aliases_by_recording[claim.recording_id].add(clean_alias)

                variants = payload.get("name_variants")
                if isinstance(variants, list):
                    for variant in variants:
                        if not isinstance(variant, dict):
                            continue
                        surface = _clean(variant.get("surface"))
                        if not surface:
                            continue
                        aliases_by_recording[claim.recording_id].add(surface)
                        if str(variant.get("verification_status") or "") == "confirmed":
                            verified_aliases_by_recording[claim.recording_id].add(surface)

                category = _clean(payload.get("category"))
                if category:
                    category_candidates.append((category, claim.recording_id))

                relation = _clean(payload.get("relation_to_speaker"))
                speaker_id = speaker_id_by_recording.get(claim.recording_id)
                if relation and speaker_id:
                    relation_candidates.append(
                        (speaker_id, relation, claim.recording_id)
                    )

            # A canonical id without a selected-source name is not enough to
            # expose a person to the Book: doing so would require borrowing the
            # family-wide canonical profile. Fail closed instead.
            if not names:
                continue

            canonical_name, name_recording = names[0]
            all_aliases = sorted(
                {
                    alias
                    for values in aliases_by_recording.values()
                    for alias in values
                    if alias != canonical_name
                }
            )
            verified_aliases = sorted(
                {
                    alias
                    for values in verified_aliases_by_recording.values()
                    for alias in values
                    if alias != canonical_name
                }
            )
            relations_to_speakers: dict[str, str] = {}
            for speaker_id, relation, _recording_id in relation_candidates:
                relations_to_speakers.setdefault(speaker_id, relation)

            category = category_candidates[0][0] if category_candidates else "unknown"
            attribute_sources: dict[str, list[str]] = {
                "display_name": [name_recording],
            }
            if category_candidates:
                attribute_sources["category"] = sorted(
                    {recording_id for _value, recording_id in category_candidates}
                )
            if relation_candidates:
                attribute_sources["relation_to_speaker"] = sorted(
                    {recording_id for _speaker, _value, recording_id in relation_candidates}
                )
            for alias in all_aliases:
                attribute_sources[f"alias:{alias}"] = sorted(
                    recording_id
                    for recording_id, values in aliases_by_recording.items()
                    if alias in values
                )

            source_recording_ids = sorted(
                {claim.recording_id for claim in person_claims}
            )
            people.append(
                {
                    "person_id": person_id,
                    "family_id": family_id,
                    "canonical_name": canonical_name,
                    "normalized_name": canonical_name.casefold(),
                    "aliases": all_aliases,
                    "verified_aliases": verified_aliases,
                    "category": category,
                    "relations_to_speakers": relations_to_speakers,
                    "source_recording_ids": source_recording_ids,
                    "attribute_sources": attribute_sources,
                }
            )

        selected_claim_by_id = {claim.claim_id: claim for claim in claims}
        selected_person_ids = {person["person_id"] for person in people}
        edge_rows = list(
            session.scalars(
                select(FamilyGraphEdgeRow).where(FamilyGraphEdgeRow.family_id == family_id)
            )
        )
        relationships: list[dict[str, Any]] = []
        for edge in edge_rows:
            supporting_claim_ids: list[str] = []
            for claim_id in edge.source_claim_ids or []:
                source_claim = selected_claim_by_id.get(claim_id)
                if (
                    source_claim is None
                    or source_claim.object_type != ClaimObjectType.RELATIONSHIP.value
                ):
                    continue
                payload = (
                    source_claim.payload
                    if isinstance(source_claim.payload, dict)
                    else {}
                )
                if not relationship_semantics_match(
                    left_type=str(payload.get("relationship_type") or source_claim.predicate),
                    left_subject_person_id=source_claim.subject_person_id,
                    left_subject_role=str(payload.get("subject_role") or ""),
                    left_object_person_id=source_claim.object_person_id,
                    left_object_role=str(payload.get("object_role") or ""),
                    right_type=edge.relationship_type,
                    right_subject_person_id=edge.subject_person_id,
                    right_subject_role=edge.subject_role,
                    right_object_person_id=edge.object_person_id,
                    right_object_role=edge.object_role,
                ):
                    continue
                supporting_claim_ids.append(claim_id)

            if (
                not supporting_claim_ids
                or edge.subject_person_id not in selected_person_ids
                or edge.object_person_id not in selected_person_ids
            ):
                continue

            relationships.append(
                {
                    "edge_id": edge.edge_id,
                    "family_id": edge.family_id,
                    "relationship_type": edge.relationship_type,
                    "subject_person_id": edge.subject_person_id,
                    "subject_role": edge.subject_role,
                    "object_person_id": edge.object_person_id,
                    "object_role": edge.object_role,
                    "source_claim_ids": sorted(set(supporting_claim_ids)),
                }
            )

        correction_rows = list(
            session.scalars(
                select(ArchiveCorrectionRow).where(
                    ArchiveCorrectionRow.family_id == family_id,
                    ArchiveCorrectionRow.recording_id.in_(rec_ids),
                )
            )
        )
        corrections = [
            {
                "correction_id": cor.correction_id,
                "family_id": cor.family_id,
                "recording_id": cor.recording_id,
                "kind": cor.kind,
                "subject": cor.subject,
                "original_value": cor.original_value,
                "corrected_value": cor.corrected_value,
                "explanation": cor.explanation,
                "confidence": cor.confidence,
            }
            for cor in correction_rows
        ]

        snapshot_claim_ids = {claim["claim_id"] for claim in general_claims}
        conflicts: list[dict[str, Any]] = []
        for conf in conflict_rows:
            claim_ids = list(conf.claim_ids or [])
            # A mixed selected/excluded conflict would reveal the existence and
            # possibly rationale of an excluded claim. Only conflicts whose
            # complete claim set is representable in this Book may cross the
            # selected-source boundary.
            if (
                not claim_ids
                or any(claim_id not in snapshot_claim_ids for claim_id in claim_ids)
                or (
                    conf.preferred_claim_id is not None
                    and conf.preferred_claim_id not in claim_ids
                )
            ):
                continue
            conflicts.append(
                {
                    "conflict_id": conf.conflict_id,
                    "family_id": conf.family_id,
                    "conflict_type": conf.conflict_type,
                    "status": conf.status,
                    "detected_by": conf.detected_by,
                    "claim_ids": sorted(set(claim_ids)),
                    "preferred_claim_id": conf.preferred_claim_id,
                    # Notes/reviewer text are family-wide decisions, not
                    # selected-recording evidence. The Book only needs the
                    # selected competing claim IDs and conflict state.
                    "rationale": "selected source claims disagree",
                    "resolution_note": None,
                }
            )

        recordings_dict = [
            {
                "recording_id": r.recording_id,
                "family_id": r.family_id,
                "speaker_name": r.speaker_name,
                "speaker_id": r.speaker_id,
                "detected_language": getattr(r, "detected_language", None),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in eligible
        ]

        return GroundingBundle(
            family_id=family_id,
            recordings=recordings_dict,
            pipeline_payloads=pipeline_payloads,
            people=people,
            relationships=relationships,
            stories=stories,
            events=events,
            claims=general_claims,
            corrections=corrections,
            conflicts=conflicts,
            unresolved_questions=questions,
            resolved_mentions=resolved_mentions,
        )


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _first_relation(relations: Any) -> str | None:
    if not isinstance(relations, dict):
        return None
    for value in relations.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _mention_ids(claim: ArchiveClaimRow) -> list[str]:
    """The mention ids a story names.

    A mention is a string somebody said, not a person. Resolving it is entity
    resolution's job, and it already did that work: see `resolve_mentions`.
    """

    payload = claim.payload if isinstance(claim.payload, dict) else {}
    mentions = payload.get("person_mention_ids")
    if isinstance(mentions, list):
        return [value for value in mentions if isinstance(value, str) and value]
    return []


def resolve_mentions(
    session: Session,
    *,
    family_id: str,
    recording_ids: set[str],
) -> dict[tuple[str, str], str]:
    """Map (recording_id, mention_id) -> canonical person_id.

    Story claims record which mentions they involve, and the canonical person
    behind each mention is persisted on that mention's own claim, written by
    entity resolution when the archive was built. Following that link is the
    only honest way to answer "who is this story about".

    The alternative -- matching a story's mention text against person names --
    is what PR-05 left behind on the Person page and is being removed here.
    Names are not identity: two relatives share one, one relative has three
    across Russian and Kazakh spelling, and a normalised string comparison
    quietly merges people who are not the same person.

    Resolved in one query for every recording involved, so a page of stories
    costs one lookup rather than one per story.
    """

    if not recording_ids:
        return {}
    rows = session.scalars(
        select(ArchiveClaimRow).where(
            ArchiveClaimRow.family_id == family_id,
            ArchiveClaimRow.object_type == ClaimObjectType.PERSON_MENTION.value,
            ArchiveClaimRow.recording_id.in_(recording_ids),
            # An unresolved mention has no canonical person yet. It belongs in
            # review, not in a story's cast list.
            ArchiveClaimRow.status == "active",
        )
    )
    return {
        (row.recording_id, row.source_object_id): row.subject_person_id
        for row in rows
        if row.subject_person_id
    }


def _story_person_ids(
    claim: ArchiveClaimRow,
    resolved: dict[tuple[str, str], str],
) -> list[str]:
    seen: list[str] = []
    for mention_id in _mention_ids(claim):
        person_id = resolved.get((claim.recording_id, mention_id))
        if person_id and person_id not in seen:
            seen.append(person_id)
    return seen


StoryDetailView.model_rebuild()


def grounding_bundle(
    source: Database | ArchiveReadRepository | Session,
    *,
    family_id: str,
    recording_ids: list[str] | None = None,
    max_recordings: int = 100,
) -> GroundingBundle:
    """Load authorized grounding material from Database, ArchiveReadRepository, or Session."""
    if isinstance(source, ArchiveReadRepository):
        return source.grounding_bundle(
            family_id=family_id,
            recording_ids=recording_ids,
            max_recordings=max_recordings,
        )
    if isinstance(source, Database):
        return ArchiveReadRepository(source).grounding_bundle(
            family_id=family_id,
            recording_ids=recording_ids,
            max_recordings=max_recordings,
        )
    if isinstance(source, Session):
        repo = ArchiveReadRepository.__new__(ArchiveReadRepository)
        return repo._grounding_bundle_in_session(
            source,
            family_id=family_id,
            recording_ids=recording_ids,
            max_recordings=max_recordings,
        )
    raise TypeError(f"unsupported source for grounding_bundle: {type(source)}")
