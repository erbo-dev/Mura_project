"use client";

import { Loader2, AlertTriangle } from "lucide-react";
import { useEffect, useRef, useId } from "react";
import { Button } from "@/components/ui/button";

export interface ConfirmDialogProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void | Promise<void>;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  isDestructive?: boolean;
  isLoading?: boolean;
}

/**
 * Accessible modal confirmation dialog conforming to WAI-ARIA alertdialog pattern.
 *
 * Features:
 * - Traps focus while mounted
 * - Returns focus to the trigger element on unmount
 * - Closes on Escape
 * - Backdrop click closure (when not loading)
 * - Semantic aria-modal, aria-labelledby, aria-describedby
 */
export function ConfirmDialog({
  isOpen,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel = "Подтвердить",
  cancelLabel = "Отмена",
  isDestructive = false,
  isLoading = false,
}: ConfirmDialogProps) {
  const titleId = useId();
  const descId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<Element | null>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);

  // Remember trigger element to restore focus on close
  useEffect(() => {
    if (isOpen) {
      triggerRef.current = document.activeElement;
      // Focus cancel button or container
      const timer = setTimeout(() => {
        cancelBtnRef.current?.focus();
      }, 50);
      return () => clearTimeout(timer);
    } else if (triggerRef.current instanceof HTMLElement) {
      triggerRef.current.focus();
    }
  }, [isOpen]);

  // Keyboard navigation: Escape and Tab focus trap
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isLoading) {
        e.preventDefault();
        onClose();
        return;
      }

      if (e.key === "Tab" && dialogRef.current) {
        const focusableElements = dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        if (focusableElements.length === 0) return;

        const firstElement = focusableElements[0];
        const lastElement = focusableElements[focusableElements.length - 1];

        if (e.shiftKey) {
          if (document.activeElement === firstElement) {
            e.preventDefault();
            lastElement.focus();
          }
        } else {
          if (document.activeElement === lastElement) {
            e.preventDefault();
            firstElement.focus();
          }
        }
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isLoading, onClose]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-ink/45 p-4 backdrop-blur-xs transition-opacity duration-200"
      onClick={() => {
        if (!isLoading) onClose();
      }}
      role="presentation"
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="relative my-8 w-full max-w-md rounded-3xl border border-ink/10 bg-raised p-6 shadow-lift sm:p-8"
        onClick={(e) => e.stopPropagation()}
      >
        {isDestructive && (
          <div className="mb-4 flex size-12 items-center justify-center rounded-2xl bg-danger-surface text-danger">
            <AlertTriangle className="size-6" />
          </div>
        )}

        <h3 id={titleId} className="text-section font-bold tracking-tight text-ink">
          {title}
        </h3>

        <p id={descId} className="mt-2.5 text-body leading-relaxed text-muted">
          {description}
        </p>

        <div className="mt-7 flex items-center justify-end gap-3">
          <Button
            ref={cancelBtnRef}
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={isLoading}
          >
            {cancelLabel}
          </Button>

          <Button
            type="button"
            variant={isDestructive ? "danger" : "primary"}
            onClick={() => onConfirm()}
            disabled={isLoading}
            className="gap-2"
          >
            {isLoading && <Loader2 className="size-4 animate-spin" />}
            <span>{confirmLabel}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

