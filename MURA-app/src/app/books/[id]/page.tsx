import type { Metadata } from "next";
import { BookDetailView } from "@/components/book/BookDetailView";

export const metadata: Metadata = {
  title: "Семейная книга",
};

export default async function BookPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <BookDetailView bookId={id} />;
}

