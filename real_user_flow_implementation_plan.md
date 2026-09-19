# MURA Production Phase 1.6 — Real User Flow Implementation Plan

## 1. Overview & Core Philosophy

The primary objective of Phase 1.6 is to enable a brand-new user to complete the core user journey independently without developer assistance or manual database intervention:
1. Land on MURA (`/`)
2. Sign in or register (`/sign-in` or `/sign-up`)
3. Create their family archive (`/home` -> `CreateFamily`)
4. Choose narrator context or prompt
5. Record via microphone OR upload an existing audio file (`/record`)
6. Observe truthful, transparent processing stages (`/processing`)
7. Inspect the resulting memory: narrative summary, identified people, family tree links, events, evidence quotes, and working original audio player (`/story/[id]`)
8. Navigate back to `/home`, view updated stats and recent stories, explore the family tree (`/tree`), and review ambiguities (`/review`)

### Strict Constraints
- **Mental Model**: «Я просто разговариваю с бабушкой.» Keep all technical internals transparent to the user.
- **No Redesign**: Maintain existing visual language, tokens, typography, and Lastochka mascot.
- **No Redis / Celery**: Preserve existing PostgreSQL queue (`FOR UPDATE SKIP LOCKED`).
- **No External Analytics Trackers**: No Google Analytics, Mixpanel, PostHog, or Session Replay.
- **Truthful States**: No simulated progress percentages or fake completion bars.
- **Operator Isolation**: Do not expose `/v1/operations/*` through the Next.js proxy allowlist.

---

## 2. Component-by-Component Implementation Plan

### Part A: Audio File Upload & Client Validation (`/record`)
- **Components**:
  - `MURA-app/src/components/record/record-view.tsx`
  - `MURA-app/src/components/record/audio-uploader.tsx` (new component)
- **Features**:
  1. Mode switch between **«Записать сейчас»** (Microphone) and **«Загрузить файл»** (Audio file).
  2. Drag-and-drop zone and file picker supporting:
     - MIME types: `audio/mp4`, `audio/m4a`, `audio/mpeg`, `audio/wav`, `audio/webm`, `audio/ogg`, `audio/aac`
     - File extensions: `.m4a`, `.mp3`, `.wav`, `.webm`, `.ogg`, `.aac`
  3. Client-side validation:
     - Format validation: Immediate rejection with human-readable error in user's UI language.
     - Size validation: Maximum 25MB ceiling (matching backend `core_max_upload_mb = 25`).
  4. Speaker selection (`RecordCompanion`) is required identically for uploaded files as for microphone recording.
  5. Submission via `submitRecording`:
     - Sets uploading state with truthful indeterminate indicator («Загрузка записи…»).
     - Creates local memory entry and navigates to `/processing?job=...&recording=...&memory=...`.

### Part B: First-Run UX & Landing Page Navigation
- **Components**:
  - `MURA-app/src/components/home/first-run.tsx`
  - `MURA-app/src/components/onboarding/onboarding-view.tsx`
- **Features**:
  1. In `FirstRun`: Add a primary high-contrast CTA button («Записать первое воспоминание» / «Добавить запись») linking directly to `/record`, alongside the 4 prompt question pills.
  2. In `OnboardingView`: Check `useMuraSession()`. If user is already authenticated:
     - If user has a family: Show primary CTA «Перейти в архив» linking to `/home` (and offering quick navigation).
     - If user has no family: Show primary CTA «Создать архив» linking to `/home`.

### Part C: Truthful Processing & Audio Playback Resilience
- **Components**:
  - `MURA-app/src/components/processing/processing-view.tsx`
  - `MURA-app/src/components/story/local-story-view.tsx`
  - `MURA-app/src/components/story/recording-player.tsx`
- **Features**:
  1. In `ProcessingView`: Ensure all job statuses (`queued`, `transcribing`, `cleaning`, `extracting`, `resolving`, `completed`, `failed`) and stages map truthfully to UI steps. Live preview appears immediately upon ASR completion.
  2. In `LocalStoryView`: If local IndexedDB audio blob is absent (e.g. opened in another tab or device), fall back to streaming the audio via `recordingAudioUrl(memory.familyId, memory.recordingId)` using `RecordingPlayer`.
  3. Add clear fallback if audio cannot be played.

### Part D: Evidence Grounding & Narrative Anchoring
- **Components & Backend**:
  - Backend: `Mura_project/src/mura/storage/archive_read.py`
  - Frontend: `MURA-app/src/lib/mura/pipeline-result.ts`
  - Frontend: `MURA-app/src/lib/mura/archive-api.ts`
  - Frontend: `MURA-app/src/lib/mura/reconcile-memory.ts`
  - Frontend: `MURA-app/src/components/story/local-story-view.tsx`
  - Frontend: `MURA-app/src/components/story/story-view.tsx`
- **Features**:
  1. Backend `ArchiveReadRepository.get_story`: Query `PipelineResultRow` for the recording to attach:
     - `evidence_quotes: list[str] = Field(default_factory=list)` (text of evidence spans supporting the story's claims).
     - `transcript: str | None = None` (clean readable transcript).
  2. Frontend `pipeline-result.ts` / `reconcile-memory.ts`:
     - Extract `evidenceQuotes: string[]` from `extraction.evidence_spans` and associate with `SavedMemory`.
  3. Frontend `LocalStoryView` & `StoryView`:
     - Render an expandable / secondary **«Свидетельства из рассказа»** (Evidence grounding) block.
     - Displays supporting quotes from the narrator with clean typographic treatment so as not to clutter the primary narrative.

### Part E: Viewport & Accessibility Hardening
- **Features**:
  1. Ensure all interactive controls have a minimum touch target of 44x44px.
  2. Test and harden layouts on mobile viewports (`375x667` and `390x844`), ensuring recording/upload controls and speaker selectors never slip below the fold or get clipped.
  3. Proper ARIA live regions for upload, processing status, and error states.

### Part F: Automated E2E Test Suite & Browser Verification
- **Test Scripts**:
  - Create automated Playwright E2E script (`tests/e2e/test_real_user_flow.py` or Node test):
    1. Sign in via Dev Auth (`test@example.com`).
    2. Create Family Archive («Семья Ахметовых»).
    3. Select narrator and prompt.
    4. Upload a test audio file (`test_memory.wav`).
    5. Wait for truthful processing to complete.
    6. Verify memory view: title, summary, people avatars, kinship links, evidence quotes, and working audio player.
    7. Navigate to `/home` and verify updated counts (`1 история`, `N человек`).
    8. Navigate to `/tree` and verify person card is present.
    9. Capture visual screenshots at each critical milestone.

### Part G: Runbook Documentation (`REAL_USER_FLOW.md`)
- Document the end-to-end verified user flow with step-by-step instructions, supported formats, edge cases, and troubleshooting steps for operators and developers.

---

## 3. Verification & Acceptance Criteria

1. **Unit & Integration Tests**:
   - Backend pytest suite continues to pass 100% (all 1,122+ tests).
   - Frontend vitest suite continues to pass 100% (all 471+ tests) with added tests for `audio-uploader` and evidence projection.
2. **End-to-End Automated Verification**:
   - Automated browser test executes the full user journey from clean session to saved memory and tree visualization without errors.
3. **Visual Proof**:
   - Screenshots captured and verified for:
     - First run / Landing
     - Audio file upload and format validation
     - Processing progress
     - Memory detail with audio player and evidence grounding
     - Family tree showing the extracted family member

