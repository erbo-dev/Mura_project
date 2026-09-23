"""Family Book domain contracts.

A book is a durable, versioned artifact derived from an already-grounded family
archive. Nothing in this module extracts anything: the archive decided what is
true before a book existed, and the book may only say what the archive can
support.

Two separations are load-bearing and are encoded in the types rather than left to
convention:

*Facts and prose are different objects.* A blueprint and a chapter plan carry
stable ids for the people, places, recordings and evidence they are allowed to
use. A chapter's prose is checked against those ids after the fact, so "the model
was asked nicely" is never the only defence.

*Uncertainty and disagreement survive.* Corrections carry the rejected value so
it can be forbidden, uncertainties carry why they are uncertain, and conflicts
carry both versions. A book that resolves them silently would be inventing
history rather than recording it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from mura.domain.models import StrictModel

#: Bumped whenever the compiled snapshot's shape changes, so a stored snapshot is
#: always interpretable by the code that produced it.
SNAPSHOT_SCHEMA_VERSION = "book-source-snapshot-v2"
BLUEPRINT_SCHEMA_VERSION = "book-blueprint-v1"
CONTINUITY_SCHEMA_VERSION = "book-continuity-v1"
REVIEW_SCHEMA_VERSION = "book-review-v1"
GATE_SCHEMA_VERSION = "book-gate-report-v1"

#: A book is never more than this many chapters. Enforced in code, not requested
#: politely in a prompt.
MIN_CHAPTERS = 10
MAX_CHAPTERS = 15

MIN_BOOK_WORDS = 20_000
DEFAULT_BOOK_WORDS = 25_000
MAX_BOOK_WORDS = 30_000
MAX_BOOK_SOURCE_RECORDINGS = 100
MAX_BOOK_EVIDENCE_QUOTES = 400


class BookStatus(StrEnum):
    """Lifecycle of one book row.

    ``cancelled`` is distinct from ``failed``: a user asking to stop is not a
    defect and must not appear in failure monitoring as one.
    """

    DRAFT = "draft"
    QUEUED = "queued"
    PLANNING = "planning"
    WRITING = "writing"
    REVIEWING = "reviewing"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: A book in one of these states will never change again on its own.
TERMINAL_BOOK_STATUSES = (
    BookStatus.COMPLETED.value,
    BookStatus.FAILED.value,
    BookStatus.CANCELLED.value,
)


class BookStage(StrEnum):
    """What the pipeline is actually doing, reported verbatim to the user.

    There is deliberately no percentage. "Writing chapter 3 of 12" is a true
    statement; "63%" is a number nobody can justify.
    """

    PREPARING_SOURCES = "preparing_sources"
    PLANNING = "planning"
    WRITING_CHAPTER = "writing_chapter"
    REVIEWING_CHAPTER = "reviewing_chapter"
    REPAIRING_CHAPTER = "repairing_chapter"
    UPDATING_CONTINUITY = "updating_continuity"
    EXPORTING_PDF = "exporting_pdf"
    EXPORTING_EPUB = "exporting_epub"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ChapterStatus(StrEnum):
    """Per-chapter progress. Persisted after every transition.

    The point of storing this is restart cost: a worker that dies at chapter 8
    must not re-write chapters 1 through 7, and an ``approved`` chapter is
    therefore never regenerated.
    """

    PLANNED = "planned"
    WRITING = "writing"
    REVIEWING = "reviewing"
    REPAIRING = "repairing"
    APPROVED = "approved"
    FAILED = "failed"


class ExportFormat(StrEnum):
    PDF = "pdf"
    EPUB = "epub"


class ExportStatus(StrEnum):
    PENDING = "pending"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class BookJobStatus(StrEnum):
    """Mirrors the recording queue's vocabulary exactly, minus ASR stages."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_BOOK_JOB_STATUSES = (
    BookJobStatus.COMPLETED.value,
    BookJobStatus.FAILED.value,
    BookJobStatus.CANCELLED.value,
)


class BookLanguage(StrEnum):
    """The language the book is written in.

    Explicit and independent of both the interface language and the language the
    family actually spoke. Someone reading MURA in Kazakh may want the book in
    Russian, and a mixed-language recording does not choose a book language.
    """

    RU = "ru"
    KK = "kk"
    EN = "en"


class NarrativeVoice(StrEnum):
    THIRD_PERSON = "third_person"
    FIRST_PERSON_PLURAL = "first_person_plural"
    DOCUMENTARY = "documentary"


class ReviewStatus(StrEnum):
    """Outcome of the fact and cultural review for one chapter."""

    APPROVED = "approved"
    REPAIR_REQUIRED = "repair_required"
    BLOCKED = "blocked"


class IssueSeverity(StrEnum):
    """``BLOCKER`` stops a chapter becoming canonical; ``WARNING`` never does.

    The split exists because a deterministic gate over literary prose would
    otherwise reject good writing: an institution, a month, a city or a kinship
    word used figuratively is not a hallucination. Only a genuine invented fact
    may block.
    """

    BLOCKER = "blocker"
    WARNING = "warning"


class IssueType(StrEnum):
    UNGROUNDED_PERSON = "ungrounded_person"
    UNGROUNDED_YEAR = "ungrounded_year"
    UNSUPPORTED_RELATIONSHIP = "unsupported_relationship"
    REJECTED_CORRECTION = "rejected_correction"
    UNGROUNDED_QUOTE = "ungrounded_quote"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    WRONG_LENGTH = "wrong_length"
    WRONG_LANGUAGE = "wrong_language"
    UNSUPPORTED_ANCHOR = "unsupported_anchor"
    UNKNOWN_REFERENCE = "unknown_reference"
    INVENTED_SCENE_DETAIL = "invented_scene_detail"
    UNCERTAINTY_COLLAPSED = "uncertainty_collapsed"
    CONFLICT_RESOLVED_SILENTLY = "conflict_resolved_silently"
    CULTURAL_UNSUPPORTED = "cultural_unsupported"


class GateCode(StrEnum):
    """Deterministic, code-level chapter gates. Separate from any model verdict."""

    NAMED_PERSON = "named_person"
    YEAR = "year"
    RELATIONSHIP = "relationship"
    CORRECTION = "correction"
    QUOTE = "quote"
    EVIDENCE_COVERAGE = "evidence_coverage"
    WORD_COUNT = "word_count"
    LANGUAGE = "language"

    @property
    def issue_type(self) -> IssueType:
        return _GATE_ISSUE_TYPES[self]


_GATE_ISSUE_TYPES: dict[GateCode, IssueType] = {
    GateCode.NAMED_PERSON: IssueType.UNGROUNDED_PERSON,
    GateCode.YEAR: IssueType.UNGROUNDED_YEAR,
    GateCode.RELATIONSHIP: IssueType.UNSUPPORTED_RELATIONSHIP,
    GateCode.CORRECTION: IssueType.REJECTED_CORRECTION,
    GateCode.QUOTE: IssueType.UNGROUNDED_QUOTE,
    GateCode.EVIDENCE_COVERAGE: IssueType.INSUFFICIENT_EVIDENCE,
    GateCode.WORD_COUNT: IssueType.WRONG_LENGTH,
    GateCode.LANGUAGE: IssueType.WRONG_LANGUAGE,
}


class BlueprintIssueCode(StrEnum):
    """Why a structured plan was refused. Every one is decided in code."""

    CHAPTER_COUNT_OUT_OF_RANGE = "chapter_count_out_of_range"
    WORD_BUDGET_OUT_OF_RANGE = "word_budget_out_of_range"
    CHAPTER_TOTAL_MISMATCH = "chapter_total_mismatch"
    UNKNOWN_PERSON = "unknown_person"
    UNKNOWN_PLACE = "unknown_place"
    UNKNOWN_RECORDING = "unknown_recording"
    UNKNOWN_STORY = "unknown_story"
    UNKNOWN_CLAIM = "unknown_claim"
    UNKNOWN_EVIDENCE = "unknown_evidence"
    UNSUPPORTED_YEAR = "unsupported_year"
    CHAPTER_WITHOUT_GROUNDING = "chapter_without_grounding"
    EVENT_OVER_REPEATED = "event_over_repeated"
    LANGUAGE_MISMATCH = "language_mismatch"
    UNSUPPORTED_MATERIAL_ANCHOR = "unsupported_material_anchor"
    DUPLICATE_CHAPTER_NUMBER = "duplicate_chapter_number"
    CHAPTER_NUMBER_GAP = "chapter_number_gap"


def chapter_word_budget_bounds(min_words: int, max_words: int) -> tuple[int, int]:
    """Absolute per-chapter bounds, clamped so a small book is still possible.

    A 10-chapter book targeting 20 000 words needs 2 000 words per chapter, which
    exceeds a naive 1 500-word ceiling. Refusing that plan would make the
    configured target unreachable, so the ceiling follows the book target rather
    than constraining it.
    """

    return max(200, min_words), max(min_words, max_words)


# --------------------------------------------------------------------- snapshot


class SnapshotDate(StrictModel):
    """A date as the archive actually knows it, precision included.

    ``precision`` is carried all the way into the writer's prompt on purpose.
    Rendering "1 января 1978" when the family only ever said "в семьдесят
    восьмом" invents a day and a month nobody mentioned.
    """

    value: str | None = None
    precision: str = "unknown"
    original_expression: str | None = None
    approximate: bool = False


class SnapshotPerson(StrictModel):
    """A canonical archive person. Never a mention, never an account."""

    person_id: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    category: str = "unknown"
    relation_to_speaker: str | None = None
    birth_date: SnapshotDate | None = None
    death_date: SnapshotDate | None = None
    professions: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    descriptions: list[str] = Field(default_factory=list)
    source_recording_ids: list[str] = Field(default_factory=list)
    # Per-attribute recording provenance. Optional attributes are omitted from
    # the snapshot unless their specific source can be proven selected.
    attribute_sources: dict[str, list[str]] = Field(default_factory=dict)


class SnapshotRelationship(StrictModel):
    """A materialized family-graph edge.

    Read, never re-derived: which claims are strong enough to become an edge was
    decided when the graph was built, using the evidence-class ladder.
    """

    edge_id: str
    relationship_type: str
    subject_person_id: str
    subject_role: str
    object_person_id: str
    object_role: str
    source_claim_ids: list[str] = Field(default_factory=list)


class SnapshotEvent(StrictModel):
    event_id: str
    title: str
    event_type: str = "event"
    description: str | None = None
    location: str | None = None
    date: SnapshotDate | None = None
    participant_person_ids: list[str] = Field(default_factory=list)
    recording_id: str | None = None
    evidence_quote_ids: list[str] = Field(default_factory=list)


class SnapshotStory(StrictModel):
    story_id: str
    title: str | None = None
    summary: str | None = None
    recording_id: str
    speaker_name: str
    person_ids: list[str] = Field(default_factory=list)
    evidence_quote_ids: list[str] = Field(default_factory=list)


class SnapshotClaim(StrictModel):
    """A graded archive claim, with the ladder it was graded on.

    ``evidence_class`` and ``assertion_mode`` travel with it so the planner can be
    told which claims are usable and which must stay hedged, instead of being
    handed a flat list that looks uniformly certain.
    """

    claim_id: str
    recording_id: str
    object_type: str
    predicate: str
    subject_person_id: str | None = None
    object_person_id: str | None = None
    evidence_class: str
    assertion_mode: str | None = None
    verification_status: str = "unreviewed"
    evidence_ids: list[str] = Field(default_factory=list)
    summary: str | None = None


class SnapshotCorrection(StrictModel):
    """A resolved self-correction from oral testimony.

    ``original_value`` is a **forbidden** string for the whole book. It exists
    here so the correction gate can prove it never reappears as a fact.
    """

    correction_id: str
    recording_id: str
    kind: str
    subject: str | None = None
    original_value: str
    corrected_value: str
    explanation: str = ""
    confidence: str = "unknown"


class SnapshotUncertainty(StrictModel):
    """Something the family could not settle, kept unsettled."""

    uncertainty_id: str
    recording_id: str | None = None
    kind: str
    text: str
    person_ids: list[str] = Field(default_factory=list)


class SnapshotConflict(StrictModel):
    """Two versions of one memory, preserved as two versions.

    A conflict is not a bug for the book to fix. It is a finding the book may
    report, and the writer is given both sides plus the resolution state rather
    than a single chosen truth.
    """

    conflict_id: str
    conflict_type: str
    status: str
    claim_ids: list[str] = Field(default_factory=list)
    preferred_claim_id: str | None = None
    rationale: str = ""
    resolution_note: str | None = None
    recording_ids: list[str] = Field(default_factory=list)


class SnapshotEvidence(StrictModel):
    """One verbatim evidence span from one recording.

    This is the only text a quotation in the book may be drawn from, and the
    quote gate checks that mechanically.
    """

    evidence_id: str
    recording_id: str
    speaker_name: str
    text: str
    source_layer: str = "raw_transcript"
    person_ids: list[str] = Field(default_factory=list)


class SnapshotManifest(StrictModel):
    """What a book was written from, in stable ids.

    This is why an already-generated book stays explainable: the archive may
    change tomorrow, but the manifest still names exactly the recordings, stories,
    claims, events and people this book consumed.
    """

    source_recording_ids: list[str] = Field(default_factory=list)
    source_story_ids: list[str] = Field(default_factory=list)
    source_claim_ids: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    source_person_ids: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    correction_count: int = 0
    uncertainty_count: int = 0
    conflict_count: int = 0
    created_at: datetime


class BookSourceSnapshot(StrictModel):
    """The immutable grounding bundle one book is written from.

    Compiled once, before any creative call, from material already authorized for
    this family. It is never updated: a later regeneration produces a new book
    against a new snapshot rather than quietly rewriting this one's basis.
    """

    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    compiler_version: str
    family_id: str
    manifest: SnapshotManifest
    people: list[SnapshotPerson] = Field(default_factory=list)
    relationships: list[SnapshotRelationship] = Field(default_factory=list)
    events: list[SnapshotEvent] = Field(default_factory=list)
    stories: list[SnapshotStory] = Field(default_factory=list)
    claims: list[SnapshotClaim] = Field(default_factory=list)
    corrections: list[SnapshotCorrection] = Field(default_factory=list)
    uncertainties: list[SnapshotUncertainty] = Field(default_factory=list)
    conflicts: list[SnapshotConflict] = Field(default_factory=list)
    evidence: list[SnapshotEvidence] = Field(default_factory=list)
    #: Every four-digit year that appears in grounded family material. The year
    #: gate admits nothing outside this set, so an ungrounded year cannot be
    #: written even by accident.
    allowed_years: list[int] = Field(default_factory=list)
    #: Object phrases that actually occur in evidence and could carry continuity
    #: across chapters. Candidates only: the planner may choose one, and an
    #: unsupported choice is stripped rather than accepted.
    material_anchor_candidates: list[str] = Field(default_factory=list)
    #: Languages actually present in the source recordings, for the writer's
    #: guidance. Never used to choose the book language.
    observed_languages: list[str] = Field(default_factory=list)
    #: Place names the archive knows, so the named-person gate can tell a place
    #: from a person instead of guessing from capitalisation alone.
    known_places: list[str] = Field(default_factory=list)


class CompiledSnapshot(StrictModel):
    """A snapshot plus the hash that pins it."""

    snapshot: BookSourceSnapshot
    content_hash: str


# -------------------------------------------------------------------- blueprint


class ChapterPlan(StrictModel):
    """One chapter's blueprint entry.

    Every reference is a stable id rather than a name. A name would have to be
    matched back to a person afterwards, and that is exactly the step where two
    relatives sharing one name get merged.
    """

    chapter_number: int = Field(ge=1, le=MAX_CHAPTERS)
    title: str = Field(min_length=1, max_length=400)
    purpose: str = Field(default="", max_length=2000)
    synopsis: str = Field(default="", max_length=6000)
    target_word_count: int = Field(ge=100, le=20000)
    time_range: str | None = None
    person_ids: list[str] = Field(default_factory=list)
    place_names: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_recording_ids: list[str] = Field(default_factory=list)
    source_story_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    material_anchor_refs: list[str] = Field(default_factory=list)
    continuity_in: str | None = None
    continuity_out: str | None = None
    uncertainties: list[str] = Field(default_factory=list)
    #: Stated by the planner so the writer's constraints are explicit and
    #: reviewable, not merely implied by what the chapter omits.
    forbidden_inventions: list[str] = Field(default_factory=list)


class BookBlueprint(StrictModel):
    """A structured 10-15 chapter plan. Never free-form text."""

    schema_version: str = BLUEPRINT_SCHEMA_VERSION
    book_title: str = Field(min_length=1, max_length=400)
    subtitle: str | None = Field(default=None, max_length=400)
    central_theme: str = Field(default="", max_length=2000)
    narrative_voice: NarrativeVoice = NarrativeVoice.THIRD_PERSON
    output_language: BookLanguage
    target_total_words: int = Field(ge=1000, le=60000)
    #: Null when no grounded object exists to carry continuity. The system must
    #: never fabricate an heirloom because the schema has a slot for one.
    material_anchor: str | None = None
    epigraph: str | None = None
    chapters: list[ChapterPlan] = Field(default_factory=list)


# ----------------------------------------------------------- continuity & review


class ContinuityState(StrictModel):
    """A compact, durable summary of where the book has got to.

    Deliberately not "all previous chapters". Carrying every full chapter forward
    would spend the context window on text the writer already wrote and degrade
    the prompt as the book grows. This is regenerated after each approved chapter
    and persisted, so a reclaiming worker inherits the same state.
    """

    schema_version: str = CONTINUITY_SCHEMA_VERSION
    after_chapter_number: int = Field(ge=0)
    current_time_position: str | None = None
    active_people: list[str] = Field(default_factory=list)
    resolved_story_threads: list[str] = Field(default_factory=list)
    open_story_threads: list[str] = Field(default_factory=list)
    last_scene_summary: str = Field(default="", max_length=4000)
    material_anchor_state: str | None = None
    tone: str | None = None
    important_terminology: list[str] = Field(default_factory=list)
    facts_already_revealed: list[str] = Field(default_factory=list)


class ReviewIssue(StrictModel):
    """One problem the reviewer found, tied back to something checkable."""

    issue_type: IssueType
    severity: IssueSeverity = IssueSeverity.BLOCKER
    location: str | None = None
    detail: str = Field(default="", max_length=2000)
    source_reference: str | None = None
    recommended_correction: str | None = Field(default=None, max_length=2000)


class ReviewResult(StrictModel):
    """The reviewer's verdict.

    ``corrected_text`` is accepted by the schema and deliberately **ignored** by
    the pipeline. Letting a reviewer silently rewrite prose would mean two authors
    per chapter with no way to attribute an invented detail to either of them;
    every repair goes back through the writer with a structured issue list
    instead.
    """

    schema_version: str = REVIEW_SCHEMA_VERSION
    status: ReviewStatus
    issues: list[ReviewIssue] = Field(default_factory=list)
    grounding_score: float = Field(default=1.0, ge=0.0, le=1.0)
    coverage_note: str | None = None
    corrected_text: str | None = None


class GateIssue(StrictModel):
    """One deterministic gate finding, with the detail needed to fix it."""

    code: GateCode
    severity: IssueSeverity
    issue_type: IssueType
    detail: str
    location: str | None = None
    offending: list[str] = Field(default_factory=list)


class GateReport(StrictModel):
    """The deterministic verdict. It always outranks the model's opinion."""

    schema_version: str = GATE_SCHEMA_VERSION
    chapter_number: int
    passed: bool
    blockers: list[GateIssue] = Field(default_factory=list)
    warnings: list[GateIssue] = Field(default_factory=list)
    word_count: int = 0
    grounding_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    #: True when the only failures are mechanical (length, budget) and can be
    #: fixed in code without spending another provider call.
    deterministically_repairable: bool = False


class BlueprintIssue(StrictModel):
    code: BlueprintIssueCode
    severity: IssueSeverity
    detail: str
    chapter_number: int | None = None
    offending: list[str] = Field(default_factory=list)


class BlueprintValidationReport(StrictModel):
    valid: bool
    issues: list[BlueprintIssue] = Field(default_factory=list)
    #: Set when the validator repaired the plan itself instead of rejecting it.
    repaired: bool = False

    @property
    def blockers(self) -> list[BlueprintIssue]:
        return [issue for issue in self.issues if issue.severity is IssueSeverity.BLOCKER]


class ChapterRelationshipAssertion(StrictModel):
    """An explicit kinship or relationship assertion made in a chapter draft."""

    subject_person_id: str = Field(min_length=1)
    relation: str = Field(min_length=1)
    object_person_id: str = Field(min_length=1)
    text_span: str = Field(default="", max_length=500)


class ChapterDraft(StrictModel):
    """The writer's structured answer for one chapter."""

    chapter_number: int = Field(ge=1, le=MAX_CHAPTERS)
    title: str = Field(min_length=1, max_length=400)
    text: str = Field(min_length=1)
    #: Which planned evidence the chapter actually used. Checked against the plan
    #: by the evidence-coverage gate, so "I grounded it" is not taken on trust.
    evidence_usage: list[str] = Field(default_factory=list)
    person_ids_used: list[str] = Field(default_factory=list)
    relationship_assertions: list[ChapterRelationshipAssertion] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    conflict_notes: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------- api views


class BookSourceOptionView(StrictModel):
    """One memory the user may include in a book."""

    recording_id: str
    speaker_name: str
    recorded_at: datetime
    story_count: int = 0
    person_count: int = 0
    title: str | None = None


class BookProgressView(StrictModel):
    """Truthful progress. Stages and counters, never a percentage."""

    stage: BookStage
    chapters_total: int = Field(ge=0)
    chapters_approved: int = Field(ge=0)
    current_chapter_number: int | None = None
    current_chapter_title: str | None = None


class BookCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=400)
    subtitle: str | None = Field(default=None, max_length=400)
    output_language: BookLanguage = BookLanguage.RU
    target_word_count: int = Field(
        default=DEFAULT_BOOK_WORDS,
        ge=MIN_BOOK_WORDS,
        le=MAX_BOOK_WORDS,
    )
    requested_recording_ids: list[str] | None = None


class BookRegenerateRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=400)
    subtitle: str | None = Field(default=None, max_length=400)
    output_language: BookLanguage | None = None
    target_word_count: int | None = Field(
        default=None,
        ge=MIN_BOOK_WORDS,
        le=MAX_BOOK_WORDS,
    )
    requested_recording_ids: list[str] | None = None


class BookAccepted(StrictModel):
    book_id: str
    job_id: str
    status: BookStatus = BookStatus.QUEUED


class BookSummaryView(StrictModel):
    book_id: str
    family_id: str
    title: str
    subtitle: str | None = None
    status: BookStatus
    output_language: BookLanguage
    target_word_count: int
    chapters_total: int = 0
    chapters_approved: int = 0
    word_count: int = 0
    source_snapshot_version: int = 1
    supersedes_book_id: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    error_code: str | None = None
    progress: BookProgressView


class BookDetailView(BookSummaryView):
    """Detail. Exposes no raw planner JSON and no draft text."""

    central_theme: str | None = None
    narrative_voice: NarrativeVoice | None = None
    material_anchor: str | None = None
    source_recording_count: int = 0
    available_formats: list[ExportFormat] = Field(default_factory=list)


class BookChapterSummaryView(StrictModel):
    """A chapter as a list entry: enough to render a table of contents."""

    chapter_number: int
    title: str | None = None
    status: ChapterStatus
    word_count: int = 0
    approved: bool = False


class BookChapterView(StrictModel):
    """One approved chapter's text, and nothing from a rejected draft."""

    chapter_number: int
    title: str
    text: str
    word_count: int
    approved_at: datetime | None = None


class BookChapterPageView(StrictModel):
    page: dict[str, int]
    items: list[BookChapterSummaryView]


class BookListPageView(StrictModel):
    page: dict[str, int]
    items: list[BookSummaryView]
