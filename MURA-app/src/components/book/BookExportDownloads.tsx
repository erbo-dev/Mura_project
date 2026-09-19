"use client";

import { Download, FileText, Book as BookIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMuraI18n } from "@/lib/i18n";
import { getBookDownloadUrl, type ExportFormat } from "@/lib/mura/book-api";

interface BookExportDownloadsProps {
  familyId: string;
  bookId: string;
  availableFormats?: ExportFormat[];
}

export function BookExportDownloads({
  familyId,
  bookId,
  availableFormats = ["pdf", "epub"],
}: BookExportDownloadsProps) {
  const { t } = useMuraI18n();

  return (
    <div className="flex flex-wrap items-center gap-2.5">
      {availableFormats.includes("pdf") && (
        <Button asChild variant="soft" size="md" className="gap-2">
          <a
            href={getBookDownloadUrl(familyId, bookId, "pdf")}
            download
            target="_blank"
            rel="noopener noreferrer"
          >
            <FileText className="size-4 text-ink/70" />
            <span>{t("bookDownloadPdf")}</span>
            <Download className="size-3.5 text-muted" />
          </a>
        </Button>
      )}

      {availableFormats.includes("epub") && (
        <Button asChild variant="soft" size="md" className="gap-2">
          <a
            href={getBookDownloadUrl(familyId, bookId, "epub")}
            download
            target="_blank"
            rel="noopener noreferrer"
          >
            <BookIcon className="size-4 text-ink/70" />
            <span>{t("bookDownloadEpub")}</span>
            <Download className="size-3.5 text-muted" />
          </a>
        </Button>
      )}
    </div>
  );
}

