"use client";

import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="kk">
      <body className="flex min-h-screen items-center justify-center bg-[#eee8df] p-6 text-stone-900">
        <div className="max-w-md space-y-4 text-center">
          <h1 className="text-2xl font-bold">MURA</h1>
          <h2 className="text-lg font-semibold">
            Жүйелік қате / Системная ошибка
          </h2>
          <p className="text-sm text-stone-600">
            Қате Sentry жүйесінде тіркелді.
            <br />
            Ошибка зарегистрирована в системе мониторинга.
          </p>
          <button
            onClick={() => reset()}
            className="rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white shadow hover:bg-stone-800"
          >
            Қайта жүктеу / Перезагрузить
          </button>
        </div>
      </body>
    </html>
  );
}

