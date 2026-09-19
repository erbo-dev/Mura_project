import { describe, expect, it } from "vitest";
import { isAcceptedFile, MAX_SIZE_BYTES } from "./audio-uploader";

describe("AudioUploader validation", () => {
  it("enforces a 25 MB ceiling matching backend limit", () => {
    expect(MAX_SIZE_BYTES).toBe(25 * 1024 * 1024);
  });

  it("accepts supported MIME types", () => {
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/mp4" })).toBe(true);
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/x-m4a" })).toBe(true);
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/mpeg" })).toBe(true);
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/mp3" })).toBe(true);
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/wav" })).toBe(true);
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/webm" })).toBe(true);
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/ogg" })).toBe(true);
    expect(isAcceptedFile({ name: "recording.unknown", type: "audio/aac" })).toBe(true);
  });

  it("accepts supported extensions when MIME type is generic or empty", () => {
    expect(isAcceptedFile({ name: "grandma_story.m4a", type: "" })).toBe(true);
    expect(isAcceptedFile({ name: "grandma_story.mp3", type: "application/octet-stream" })).toBe(true);
    expect(isAcceptedFile({ name: "interview.wav", type: "" })).toBe(true);
    expect(isAcceptedFile({ name: "voice_note.webm", type: "" })).toBe(true);
    expect(isAcceptedFile({ name: "old_tape.ogg", type: "" })).toBe(true);
    expect(isAcceptedFile({ name: "memo.aac", type: "" })).toBe(true);
  });

  it("rejects unsupported file formats", () => {
    expect(isAcceptedFile({ name: "notes.txt", type: "text/plain" })).toBe(false);
    expect(isAcceptedFile({ name: "photo.jpg", type: "image/jpeg" })).toBe(false);
    expect(isAcceptedFile({ name: "document.pdf", type: "application/pdf" })).toBe(false);
    expect(isAcceptedFile({ name: "archive.zip", type: "application/zip" })).toBe(false);
    expect(isAcceptedFile({ name: "video.mkv", type: "video/x-matroska" })).toBe(false);
  });
});

