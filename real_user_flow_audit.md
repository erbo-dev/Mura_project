# MURA Production Phase 1.6 — Real User Flow & UX Audit

## 1. Executive Summary & Philosophy

MURA (Мұра) is a voice-first family memory archive.
The emotional and product core of MURA is:
> **«Я просто разговариваю с бабушкой.»** *(“I'm just talking to grandmother.”)*

All backend complexity — asynchronous workers, PostgreSQL `FOR UPDATE SKIP LOCKED` queues, GigaAM/Whisper transcription, DeepSeek extraction, entity resolution, and evidence claim ladders — must stay completely transparent. The real user must experience a seamless, trustworthy, and dignified journey from their first visit to a permanent, playable, evidence-grounded family memory without developer intervention.

This document presents the complete audit of every route, component, API contract, and UX state across the application as of Phase 1.6 Step 1.

---

## 2. Route-by-Route UX & Architecture Audit

| Route | Primary Action | Frontend Component | API / Data Source | Backend Endpoint | Status | Notes & Identified Deficiencies |
|---|---|---|---|---|---|---|
| `/` | Landing page, value proposition, entry | `OnboardingView` (`app/page.tsx`) | Session status | N/A | **PARTIAL** | Shows "Создать семейный архив" (`/sign-up`) and "Войти" (`/sign-in`). If user is already authenticated with an existing family, visiting `/` still prompts to create/sign in instead of transitioning smoothly to `/home` or providing a prominent "Перейти в архив" button. |
| `/sign-in` | Authenticate existing user | `SignIn` / `DevSignInPanel` / `SupabaseSignIn` | `/api/dev-auth/session` or Supabase/Clerk | Auth Provider / Dev Auth | **WORKING** | Supports Dev Auth (`MURA_DEV_AUTH=true`), Supabase Auth, and Clerk. Correctly forwards `redirectTo` (`destination`) to `/home`. Clean error messaging. |
| `/sign-up` | Register new user | `SignUp` / `DevSignInPanel` / `SupabaseSignIn` | Same as sign-in | Auth Provider / Dev Auth | **WORKING** | Same auth handlers; redirects to `destination` (`/home`). |
| `/home` (First Run: no family) | Prompt user to name & create family archive | `FirstRunFlow` -> `CreateFamily` (`family-gate.tsx`) | `createFamily(name)` | `POST /v1/families` | **WORKING** | Clean, emotional UI with Lastochka mascot. Validates non-empty name. Immediately updates session without page reload. |
| `/home` (First Run: role selection) | Optional role questionnaire to personalize prompts | `RoleStep` (`role-step.tsx`) | Local role state (`useNarratorRole`) | Client state | **WORKING** | Allows selecting narrator role (parent, child, elder, etc.) or skipping. Sets personalized conversation starters. |
| `/home` (First Run: archive empty) | Prompt to record first story | `FirstRun` (`first-run.tsx`) | `promptsForRole` | N/A | **PARTIAL** | Displays 4 clickable prompt cards linking to `/record`. **Gap**: Lacks a prominent primary "Записать первое воспоминание" / "Добавить запись" CTA button for users who want to record immediately without selecting a prompt card. |
| `/home` (With Stories) | Archive dashboard | `HomeView`, `RecordHero`, `StoryLink`, `PeopleStrip` | `fetchArchiveOverview`, `fetchArchivePeople`, `fetchArchiveRelationships` | `GET /v1/families/{id}/archive`, `people`, `relationships` | **WORKING** | High-contrast `RecordHero` with primary "Записать воспоминание" button, statistics (people, stories, generations), recent stories feed, and search shortcut (Ctrl+K). |
| `/record` | Capture or upload family memory audio | `RecordView`, `RecordControls`, `RecordCompanion`, `AudioLanguagePicker` | `submitRecording` (FormData) | `POST /v1/families/{id}/recordings` | **PARTIAL / BROKEN GAP** | **Critical Gap**: Only supports microphone recording (`useRecorder`). Users who already have voice recordings (phone voice memos, WhatsApp audio in `.m4a`, `.mp3`, `.wav`, `.webm`, `.ogg`) have no way to upload files! Client-side file format & size validation (25MB limit) is missing. |
| `/processing` | Visual progress while worker processes audio | `ProcessingView`, `TranscriptPreview`, `MascotStage` | `fetchJob` polling | `GET /v1/families/{id}/jobs/{id}` | **WORKING** | Polls job status. Truthful stage progression (`queued`, `transcribing`, `cleaning`, `extracting`, `resolving`, `completed`, `failed`). Shows live transcript preview as soon as ASR finishes. Automatically redirects upon completion. |
| `/story/[id]` (`local-...`) | Display newly finished local memory | `LocalStoryView`, `TranscriptReader` | IndexedDB `getSavedMemory` + `reconcileMemory` (`fetchRecordingResult`) | `GET /v1/families/{id}/recordings/{id}` | **PARTIAL** | Shows title, summary, people (with links to tree profiles), events/places, transcript, and audio player. **Gaps**: 1) If audio blob is absent in IndexedDB (different tab/device), it fails to fall back to streaming audio from `/v1/families/{id}/recordings/{id}/audio`. 2) Evidence quotes/spans supporting extracted facts are not rendered. |
| `/story/[id]` (canonical `story_id`) | Display persisted archive story | `StoryView`, `RecordingPlayer` | `fetchArchiveStory` | `GET /v1/families/{id}/stories/{id}` | **PARTIAL** | Renders canonical story from Core, people, events, source, and audio player via `GET /v1/families/{id}/recordings/{id}/audio`. **Gap**: Does not display supporting evidence quotes/spans or transcript reader. |
| `/stories` | Catalog of all family stories | `StoriesContent`, `ArchiveState`, `StoryLink` | `fetchArchiveStories` (paged) | `GET /v1/families/{id}/stories` | **WORKING** | Paginated list (30 per page) with excerpt, speaker name, recorded date, participant avatars. If empty, provides clear "Записать воспоминание" CTA. |
| `/tree` | Visual family tree & relationship graph | `TreeView`, `TreeCanvas`, `PersonSheet` | `fetchArchivePeople`, `fetchArchiveRelationships` | `GET /v1/families/{id}/people`, `GET /v1/families/{id}/relationships` | **WORKING** | Interactive pan-zoom canvas of family members and kinship connections. Clicking a person opens detail sheet with links to their stories and profile. |
| `/person/[id]` | Profile of a family member | `PersonView`, `PersonAvatar` | `fetchArchiveProfile` | `GET /v1/families/{id}/profiles/{id}` | **WORKING** | Displays display name, aliases, relation to speaker, kinships, locations, and linked stories. |
| `/review` | Ambiguities & conflicting claims queue | `ReviewView` | `fetchArchiveReviewItems`, `resolveConflict`, `dismissConflict` | `GET /v1/families/{id}/review-items`, `POST conflicts/{id}/resolve` | **WORKING** | Truthfully lists unresolved questions and conflicts flagged by extraction pipeline. Operators/editors can resolve or dismiss. |
| `/settings` | Archive preferences & family management | `SettingsView`, `MembersSection` | `fetchFamilyMembers`, `updateMemberRole`, `removeMember` | `GET/PATCH/DELETE /v1/families/{id}/members` | **WORKING** | Audio language selector (auto, ru, kk, mixed), family members table with roles, family rename, and sign-out button. |
| `/ask` | Ask MURA question about family archive | `AskView`, `MemoryAnswerCard` | `searchArchive` | Client search / search API | **WORKING** | Search and memory retrieval interface. |

---

## 3. Identified Critical Gaps

### Gap 1: Missing Audio File Upload in Record View (Step 5)
- **Problem**: `RecordView` is strictly wired to microphone capture via `useRecorder`. Users frequently hold pre-recorded interviews, voice notes from WhatsApp/Telegram, or legacy dictaphone files (`.m4a`, `.mp3`, `.wav`, `.ogg`, `.webm`).
- **Required Solution**:
  - Add an intuitive mode toggle on `/record`: **«Записать сейчас»** (Record microphone) / **«Загрузить файл»** (Upload audio file).
  - Provide a clean drag-and-drop / file selector zone with clear accepted format hints.
  - Implement client-side validation before network request:
    - Accepted MIME types / extensions: `audio/mp4`, `audio/m4a`, `audio/mpeg`, `audio/mp3`, `audio/wav`, `audio/wave`, `audio/x-wav`, `audio/webm`, `audio/ogg`.
    - Maximum upload size ceiling: 25MB (matching backend `core_max_upload_mb = 25`).
    - Clear, friendly inline error messages if format is unsupported or file exceeds 25MB.
  - Require speaker attribution (`RecordCompanion`) identically for both recorded and uploaded audio.
  - Truthful upload feedback state (`"Загрузка записи..."` with spinner, no simulated percentages).

### Gap 2: First-Run Prominent CTA (Step 4)
- **Problem**: When a user creates their family archive and has 0 stories, `FirstRun` shows 4 suggestion prompt pills. While clicking a prompt links to `/record`, there is no unmistakable primary action button.
- **Required Solution**:
  - Add a primary CTA button (`"Записать первую историю"` / `"Добавить воспоминание"`) linking directly to `/record`, styled with prominent ink contrast.

### Gap 3: Seamless Landing Page Transition for Authenticated Users (Step 3)
- **Problem**: When a user who is already signed in visits `/`, they are greeted with "Создать семейный архив" and "Войти".
- **Required Solution**:
  - If authenticated with an active session:
    - If user has an existing family, provide a primary `"Перейти в архив"` CTA button leading to `/home` (and optional smooth client redirect).
    - If user has no family, provide `"Создать архив"` leading to `/home`.

### Gap 4: Audio Playback Resilience in Local Story View (Step 9)
- **Problem**: `LocalStoryView` only renders an audio player if `getMemoryAudio(memoryId)` returns an audio blob from IndexedDB. If opened in a new tab, or after local cache clear, the player completely disappears even though the audio was uploaded to Supabase Storage / local backend storage.
- **Required Solution**:
  - In `LocalStoryView`, if `!audioUrl` but `memory.familyId` and `memory.recordingId` are present, fall back to streaming audio from `/api/mura/v1/families/{family_id}/recordings/{recording_id}/audio` using `RecordingPlayer`.

### Gap 5: Evidence Grounding in Memory Review (Step 10)
- **Problem**: One of MURA's core promises is Evidence-first grounding — memories are not hallucinated fabrications, but anchored in exact quotes from the recording. Neither `LocalStoryView` nor `StoryView` currently displays the extracted evidence quotes supporting the narrative and people/events.
- **Required Solution**:
  - In backend `ArchiveReadRepository.get_story`, populate `evidence_quotes: list[str]` from the associated `pipeline_results` payload.
  - In frontend `pipeline-result.ts` / `reconcile-memory.ts`, project `evidence_spans` / quotes into `SavedMemory`.
  - In `LocalStoryView` and `StoryView`, render an accessible, expandable secondary section: **«Свидетельства из рассказа»** (Evidence grounding), displaying verbatim supporting quotes from the narrator without cluttering the primary narrative.

### Gap 6: Viewport & Accessibility Hardening
- **Problem**: Mobile viewports (iPhone SE 375px, iPhone 14 390px) can push the speaker selector or record buttons below the fold if spacing is rigid.
- **Required Solution**:
  - Verify layout sizing, touch targets (minimum 44px), ARIA status announcements, and keyboard navigability across 375px, 390px, 768px, and 1280px+.

