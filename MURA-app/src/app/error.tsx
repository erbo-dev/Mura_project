"use client";

import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";

export default function Error({
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
    <div className="flex min-h-[60vh] flex-col items-center justify-center p-6 text-center">
      <div className="max-w-md space-y-4">
        <h2 className="text-xl font-semibold text-stone-900 dark:text-stone-100">
          Бірдеңе дұрыс болмады / Что-то пошло не так
        </h2>
        <p className="text-sm text-stone-600 dark:text-stone-400">
          Қате тіркелді. Қайталап көріңіз немесе кейінірек оралыңыз.
          <br />
          Произошла непредвиденная ошибка. Попробуйте обновить страницу.
        </p>
        <button
          onClick={() => reset()}
          className="inline-flex items-center justify-center rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white shadow hover:bg-stone-800 transition-colors dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-200"
        >
          Қайталау / Повторить
        </button>
      </div>
    </div>
  );
}

