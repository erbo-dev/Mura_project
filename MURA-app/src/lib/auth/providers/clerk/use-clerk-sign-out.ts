"use client";

import { useClerk } from "@clerk/nextjs";

export function useClerkSignOut(): () => Promise<unknown> {
  const { signOut } = useClerk();
  return () => signOut({ redirectUrl: "/" });
}
