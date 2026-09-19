import type { Metadata } from "next";
import { BooksView } from "@/components/book/BooksView";

export const metadata: Metadata = {
  title: "Семейные книги",
};

export default function BooksPage() {
  return <BooksView />;
}

