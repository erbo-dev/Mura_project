/**
 * Typed client for MURA Grounded Family Book endpoints.
 *
 * All requests route through the Next.js `/api/mura` server proxy, which carries
 * the user's bearer session token and validates against the server allowlist.
 */

import { coreRequest } from "@/lib/mura/core-api";

export type BookStage =
  | "preparing_sources"
  | "planning"
  | "writing_chapter"
  | "reviewing_chapter"
  | "repairing_chapter"
  | "updating_continuity"
  | "exporting_pdf"
  | "exporting_epub"
  | "completed"
  | "failed"
  | "cancelled";

export type BookStatus =
  | "draft"
  | "queued"
  | "planning"
  | "writing"
  | "reviewing"
  | "exporting"
  | "completed"
  | "failed"
  | "cancelled";

export type ChapterStatus =
  | "planned"
  | "writing"
  | "reviewing"
  | "repairing"
  | "approved"
  | "failed";

export type ExportFormat = "pdf" | "epub";

export interface BookSourceOption {
  recording_id: string;
  speaker_name: string;
  recorded_at: string;
  story_count: number;
  person_count: number;
  title: string | null;
}

export interface BookProgress {
  stage: BookStage;
  chapters_total: number;
  chapters_approved: number;
  current_chapter_number: number | null;
  current_chapter_title: string | null;
}

export interface BookAccepted {
  book_id: string;
  job_id: string;
  status: BookStatus;
}

export interface BookSummary {
  book_id: string;
  family_id: string;
  title: string;
  subtitle: string | null;
  status: BookStatus;
  output_language: "ru" | "kk" | "en";
  target_word_count: number;
  chapters_total: number;
  chapters_approved: number;
  word_count: number;
  source_snapshot_version: number;
  supersedes_book_id: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  error_code: string | null;
  progress: BookProgress;
}

export interface BookDetail extends BookSummary {
  central_theme: string | null;
  narrative_voice: string | null;
  material_anchor: string | null;
  source_recording_count: number;
  available_formats: ExportFormat[];
}

export interface BookChapterSummary {
  chapter_number: number;
  title: string | null;
  status: ChapterStatus;
  word_count: number;
  approved: boolean;
}

export interface BookChapter {
  chapter_number: number;
  title: string;
  text: string;
  word_count: number;
  approved_at: string | null;
}

export interface BookChapterPage {
  page: { total: number; limit: number; offset: number };
  items: BookChapterSummary[];
}

export interface BookListPage {
  page: { total: number; limit: number; offset: number };
  items: BookSummary[];
}

export interface BookCreatePayload {
  title: string;
  subtitle?: string | null;
  output_language?: "ru" | "kk" | "en";
  target_word_count?: number;
  requested_recording_ids?: string[];
}

export interface BookRegeneratePayload {
  title?: string;
  subtitle?: string | null;
  output_language?: "ru" | "kk" | "en";
  target_word_count?: number;
  requested_recording_ids?: string[];
}

// ------------------------------------------------------------- api calls

export function fetchBookSources(
  familyId: string,
  signal?: AbortSignal,
): Promise<BookSourceOption[]> {
  return coreRequest<BookSourceOption[]>(
    `/v1/families/${encodeURIComponent(familyId)}/books/sources`,
    { signal },
  );
}

export function createBook(
  familyId: string,
  payload: BookCreatePayload,
): Promise<BookAccepted> {
  return coreRequest<BookAccepted>(
    `/v1/families/${encodeURIComponent(familyId)}/books`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function fetchBooks(
  familyId: string,
  limit = 20,
  offset = 0,
  signal?: AbortSignal,
): Promise<BookListPage> {
  return coreRequest<BookListPage>(
    `/v1/families/${encodeURIComponent(familyId)}/books?limit=${limit}&offset=${offset}`,
    { signal },
  );
}

export function fetchBook(
  familyId: string,
  bookId: string,
  signal?: AbortSignal,
): Promise<BookDetail> {
  return coreRequest<BookDetail>(
    `/v1/families/${encodeURIComponent(familyId)}/books/${encodeURIComponent(bookId)}`,
    { signal },
  );
}

export function fetchBookStatus(
  familyId: string,
  bookId: string,
  signal?: AbortSignal,
): Promise<BookProgress> {
  return coreRequest<BookProgress>(
    `/v1/families/${encodeURIComponent(familyId)}/books/${encodeURIComponent(bookId)}/status`,
    { signal },
  );
}

export function fetchBookChapters(
  familyId: string,
  bookId: string,
  signal?: AbortSignal,
): Promise<BookChapterPage> {
  return coreRequest<BookChapterPage>(
    `/v1/families/${encodeURIComponent(familyId)}/books/${encodeURIComponent(bookId)}/chapters`,
    { signal },
  );
}

export function fetchBookChapter(
  familyId: string,
  bookId: string,
  chapterNumber: number,
  signal?: AbortSignal,
): Promise<BookChapter> {
  return coreRequest<BookChapter>(
    `/v1/families/${encodeURIComponent(familyId)}/books/${encodeURIComponent(bookId)}/chapters/${chapterNumber}`,
    { signal },
  );
}

export function cancelBook(familyId: string, bookId: string): Promise<BookDetail> {
  return coreRequest<BookDetail>(
    `/v1/families/${encodeURIComponent(familyId)}/books/${encodeURIComponent(bookId)}/cancel`,
    { method: "POST" },
  );
}

export function regenerateBook(
  familyId: string,
  bookId: string,
  payload?: BookRegeneratePayload,
): Promise<BookAccepted> {
  return coreRequest<BookAccepted>(
    `/v1/families/${encodeURIComponent(familyId)}/books/${encodeURIComponent(bookId)}/regenerate`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload ?? {}),
    },
  );
}

export function getBookDownloadUrl(
  familyId: string,
  bookId: string,
  format: ExportFormat,
): string {
  return `/api/mura/v1/families/${encodeURIComponent(familyId)}/books/${encodeURIComponent(bookId)}/download?format=${format}`;
}

