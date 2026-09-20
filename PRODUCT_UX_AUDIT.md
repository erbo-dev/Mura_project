# MURA (Мұра) — Product Completion & Premium UX Audit
**Date**: September 2026  
**Auditor**: Senior Product Designer & Frontend Architecture Team  
**Scope**: Complete Frontend (`MURA-app`) UX, Visual Polish, Accessibility, and Design System Audit

---

## 1. Executive Summary & Design Principles

MURA is a generational digital family archive designed to capture oral memories, preserve relationships across generations, and synthesize grounded family books in Kazakh, Russian, and English.

### Aesthetic Foundation
- **Paper & Ink**: The visual foundation is built upon warm ivory/paper tones (`--color-paper: #F2EFE7`, `--color-raised: #FBF9F5`) paired with deep, cool-toned ink (`--color-ink: #14141C`, `--color-ink-deep: #0A0A12`).
- **Editorial Typography**: Long-form family narratives require genuine reading measures (65–70ch), comfortable line heights (1.75–1.85), and refined serif/sans toggle capabilities.
- **Calm, Private Trust**: The product rejects high-saturation SaaS patterns, gradient cards, and neon AI tropes. The interface reflects the dignity of family memories and intergenerational heritage.
- **Zero Engineering Jargon**: Technical leakages such as LLM, tokens, job lease, queue workers, vector search, or raw database error codes (`ERR_PLANNER_FAIL`) must never be exposed to users. All messaging is human, supportive, and truthful.

---

## 2. Route-by-Route Product & UX Audit

### 2.1. Landing Page (`/`)
- **Strengths**: Clear value proposition ("Голос вашей семьи. Навсегда."), warm brand illustrations, step-by-step onboarding cards.
- **Findings**:
  - *Accessibility / Motion*: Missing root `<MotionConfig reducedMotion="user">` for system-level vestibular accessibility.
  - *Theme Color*: Viewport theme color specified `#eee8df` instead of design token `#f2efe7`.
- **Action**: Align viewport meta theme color and configure Framer Motion reduced-motion wrapper in `layout.tsx`.

### 2.2. Archive Home (`/home`)
- **Strengths**: Masthead with real family name, editorial list of recent stories, prominent record CTA, and structured doorways.
- **Findings**:
  - *Doorways*: Cleanly organized with real counts.
  - *Empty State*: When zero memories exist, first-run flow provides actionable prompts without overwhelming the user.
- **Action**: Ensure all interactive doorway links maintain focus rings and comfortable touch targets (min 44px) on mobile viewports (390px).

### 2.3. Family Tree (`/tree`)
- **Strengths**: Interactive SVG tree with pan/zoom gestures, proper generation grouping, and honest handling of disconnected family clusters ("islands").
- **Findings**:
  - *Canvas Controls*: Reset and zoom controls are positioned well, but need high-contrast outlines against canvas backgrounds.
  - *Mobile Gestures*: Single-touch drag and pinch-to-zoom need clear tactile feedback.
- **Action**: Preserve existing gesture handling; verify touch-action and mobile canvas usability.

### 2.4. Stories & Story Detail (`/stories`, `/story/[id]`)
- **Strengths**: True editorial reading layout, quotes anchored to audio provenance, tagged family members.
- **Findings**:
  - *Reading Measure*: Optimal container width (`max-w-reading` ~70ch) ensures comfortable reading for elderly family members.
  - *Provenance*: Honest attribution to recording source and speaker.
- **Action**: Maintain strong contrast ratios for metadata badges and speaker avatars.

### 2.5. Voice Recording Flow (`/record`)
- **Strengths**: Clear recording companion with contextual prompt cards, live waveform animation, audio language selector.
- **Findings**:
  - *Language Separation*: Audio spoken language is correctly decoupled from interface locale.
  - *Permission States*: Clear guidance when microphone access is denied or unavailable.
  - *Double-Submit Guard*: Submission ref prevents double-clicks during upload.
- **Action**: Verify that upload errors provide graceful recovery without losing the local recording blob.

### 2.6. Processing Screen (`/processing`)
- **Strengths**: Mascot step progression, real-time preview of transcribed segments, countdown for degraded ASR states.
- **Findings**:
  - *User Reassurance*: Users may feel anxious leaving the page while processing is ongoing.
  - *Copy Clarity*: Needs explicit statement that closing the browser or returning to home is safe because processing runs durably on the server.
- **Action**: Reinforce background processing reassurance copy across all locales.

### 2.7. Books Library (`/books`)
- **Strengths**: Book cover previews, chapter counts, status badges, and download actions.
- **Findings**:
  - *Duplicate Heading*: Page renders `AppHeader title="Книги"` and immediately repeats `<h2>Книги</h2>` in the content body.
  - *Empty State*: Clean editorial empty state with direct "Создать книгу" CTA.
- **Action**: Remove the redundant `<h2>Книги</h2>` heading, preserving clean hierarchy and semantic structure.

### 2.8. Book Creation Modal (`BookCreateModal.tsx`)
- **Strengths**: Source memory selector with story and person counts, multi-step configuration.
- **Findings**:
  - *Stale Presets (P0)*: Array `PRESET_WORD_COUNTS` contained duplicate and obsolete presets (10,000, 15,000, 25,000, 20,000, 25,000, 30,000). Valid backend contract requires exactly 20,000, 25,000, 30,000 words.
  - *Default Selection*: Target word count must default to 25,000 (Standard).
- **Action**: Clean `PRESET_WORD_COUNTS` to the canonical 20k / 25k / 30k contract with matching i18n keys.

### 2.9. Book Progress & Detail View (`BookProgressTracker.tsx`, `BookDetailView.tsx`)
- **Strengths**: Discrete chapter progress indicators without misleading percentage animations, real-time polling.
- **Findings**:
  - *Unstyled Native Dialog (P0)*: `BookProgressTracker.tsx` line 100 invokes native `window.confirm(t("bookCancelConfirm"))`.
  - *Technical Error Codes (P0)*: Line 139 of `BookDetailView.tsx` exposes `Код ошибки: ${book.error_code}` directly to users.
- **Action**:
  - Replace `window.confirm` with a fully accessible `ConfirmDialog` modal component.
  - Map backend error codes (`pipeline_failed`, `continuity_failed`, `export_failed`) to supportive, human-friendly localized descriptions.

### 2.10. Book Reader (`BookReader.tsx`)
- **Strengths**: Distraction-free reading view, chapter table of contents drawer, font size adjustments (md/lg/xl), serif vs sans toggle.
- **Findings**:
  - *Typography*: Ensure Kazakh and Russian glyphs render with consistent Cyrillic kerning and optical weights.
  - *Navigation*: Sticky reader toolbar and smooth scrolling between chapters.
- **Action**: Ensure maximum reading comfort with generous paragraph margins and subtle indent styling.

### 2.11. Settings & Privacy Lifecycle (`/settings`)
- **Strengths**: Member role permissions, UI language switcher, audio language preference, privacy policy disclosures.
- **Findings**:
  - *Missing Privacy Actions (P0)*: Missing UI for "Экспорт данных семьи" (GDPR / Privacy Export) and "Удаление архива семьи" (Destructive deletion with confirmation).
  - *Proxy Allowlist Gaps (P0)*: Next.js proxy `/api/mura/[...path]/proxy.ts` lacks entries for `GET /privacy/export`, `DELETE /recordings/{id}`, `DELETE /books/{id}`, and `DELETE /families/{id}`.
- **Action**:
  - Update proxy allowlist in `proxy.ts` and test suite `proxy-allowlist.test.ts`.
  - Add client API functions in `core-api.ts` and `book-api.ts`.
  - Integrate Privacy Export download and Family Archive Deletion modal in `settings-view.tsx`.

### 2.12. Archive Q&A Demo (`/ask`)
- **Strengths**: Clearly gated with `DemoNotice` banner, grounded answers citing archive evidence.
- **Findings**: Kept out of main navigation to preserve honest product boundaries.

---

## 3. Prioritized Issue Log

| Priority | Component / Route | Description | Remediation Plan |
|---|---|---|---|
| **P0** | `BookCreateModal.tsx` | Obsolete/duplicate word count presets (10k, 15k, 25k, 20k, 25k, 30k) | Pinned to 20,000, 25,000, 30,000 words; default 25,000 |
| **P0** | `proxy.ts` & `core-api.ts` | Missing privacy export and deletion endpoints in Next.js proxy allowlist | Add routes to allowlist with regex validation; add client methods |
| **P0** | `BookProgressTracker.tsx` | Unstyled, non-accessible browser `window.confirm` | Implement accessible `ConfirmDialog` primitive (WCAG 2.1 AA) |
| **P0** | `BookDetailView.tsx` | Raw technical error code leakage (`Код ошибки: ${book.error_code}`) | Humanize errors into compassionate, actionable guidance |
| **P1** | `settings-view.tsx` | Lack of UI for data export and family archive deletion | Add Export Archive button and Danger Zone Delete Modal |
| **P1** | `BooksView.tsx` | Redundant duplicate `<h2>Книги</h2>` heading | Remove redundant heading, refine layout spacing |
| **P1** | `layout.tsx` | Missing `prefers-reduced-motion` config and viewport theme color mismatch | Add `MotionConfig reducedMotion="user"` and update themeColor to `#f2efe7` |
| **P2** | `processing-view.tsx` | User reassurance during asynchronous background processing | Add clear notification that closing the browser is safe |
| **P2** | `BookReader.tsx` | Refinement of reading column and typography controls | Ensure 70ch optimal measure and elegant chapter transitions |
| **P3** | Global Design System | Consistent focus rings, touch targets, and contrast across mobile viewports | Verify WCAG 2.1 AA compliance (min 4.5:1 text contrast) |

