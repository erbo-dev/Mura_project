"use client";

import { useCallback, useRef, useState } from "react";
import { Upload, X, FileAudio } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMuraI18n, type TranslationKey } from "@/lib/i18n";

export const MAX_SIZE_BYTES = 25 * 1024 * 1024; // 25 MB

/** MIME types the backend can process. */
const ACCEPTED_TYPES = new Set([
  "audio/mp4",
  "audio/x-m4a",
  "audio/mpeg",
  "audio/mp3",
  "audio/wav",
  "audio/wave",
  "audio/x-wav",
  "audio/webm",
  "audio/ogg",
  "audio/aac",
]);

/** Extensions when the browser reports an empty MIME. */
const ACCEPTED_EXTENSIONS = /\.(m4a|mp3|mp4|wav|webm|ogg|aac)$/i;

export function isAcceptedFile(file: { name: string; type: string }): boolean {
  if (ACCEPTED_TYPES.has(file.type)) return true;
  return ACCEPTED_EXTENSIONS.test(file.name);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface AudioUploaderProps {
  onFileSelected: (file: File) => void;
  onFileRemoved: () => void;
  selectedFile: File | null;
  disabled?: boolean;
  className?: string;
}

/**
 * Drag-and-drop or click-to-select audio file picker.
 *
 * Validates format and size client-side before anything leaves the browser.
 * The validation is a convenience, not a trust boundary — Core repeats both
 * checks server-side.
 */
export function AudioUploader({
  onFileSelected,
  onFileRemoved,
  selectedFile,
  disabled = false,
  className,
}: AudioUploaderProps) {
  const { t } = useMuraI18n();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<TranslationKey | null>(null);

  const accept = useCallback(
    (file: File) => {
      setError(null);
      if (!isAcceptedFile(file)) {
        setError("uploadFormatError");
        return;
      }
      if (file.size > MAX_SIZE_BYTES) {
        setError("uploadSizeError");
        return;
      }
      onFileSelected(file);
    },
    [onFileSelected],
  );

  const handleDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragOver(false);
      if (disabled) return;
      const file = event.dataTransfer.files[0];
      if (file) accept(file);
    },
    [accept, disabled],
  );

  const handleChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0];
      if (file) accept(file);
      // Reset so the same file can be re-selected after removal.
      if (inputRef.current) inputRef.current.value = "";
    },
    [accept],
  );

  if (selectedFile) {
    return (
      <div className={cn("rounded-panel bg-raised p-5 shadow-soft", className)}>
        <div className="flex items-center gap-3">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-peach">
            <FileAudio className="size-5" strokeWidth={1.7} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-body font-semibold">{selectedFile.name}</p>
            <p className="text-meta text-muted">
              {formatBytes(selectedFile.size)} · {t("uploadReady")}
            </p>
          </div>
          {!disabled && (
            <button
              type="button"
              onClick={() => {
                onFileRemoved();
                setError(null);
              }}
              aria-label={t("uploadRemove")}
              className="flex size-9 shrink-0 items-center justify-center rounded-full text-ink/50 transition-colors hover:bg-sand hover:text-ink focus-ring"
            >
              <X className="size-4" strokeWidth={2} />
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={className}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!disabled) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={cn(
          "flex w-full flex-col items-center gap-3 rounded-panel border-2 border-dashed px-6 py-10 text-center transition-colors",
          dragOver
            ? "border-ink/40 bg-peach/20"
            : "border-ink/15 bg-raised/50 hover:border-ink/25 hover:bg-raised",
          disabled && "pointer-events-none opacity-50",
        )}
      >
        <Upload className="size-8 text-ink/40" strokeWidth={1.5} />
        <p className="text-body font-medium text-ink/70">{t("uploadDropHint")}</p>
        <p className="text-meta text-muted">{t("uploadFormatHint")}</p>
      </button>

      <input
        ref={inputRef}
        type="file"
        accept=".m4a,.mp3,.mp4,.wav,.webm,.ogg,.aac,audio/*"
        className="hidden"
        onChange={handleChange}
      />

      {error && (
        <p
          role="alert"
          className="mt-3 rounded-surface bg-danger-surface px-4 py-2.5 text-meta leading-relaxed text-danger"
        >
          {t(error)}
        </p>
      )}
    </div>
  );
}
