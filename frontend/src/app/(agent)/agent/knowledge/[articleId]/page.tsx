import { ArticleScreen } from "@/components/knowledge";
import { notFound } from "next/navigation";

export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ articleId: string }>;
  searchParams: Promise<{ revision?: string | string[] }>;
}) {
  const { articleId } = await params;
  const { revision } = await searchParams;
  if (
    revision !== undefined &&
    (typeof revision !== "string" || !/^[1-9][0-9]{0,8}$/.test(revision))
  )
    notFound();
  return (
    <ArticleScreen
      key={`${articleId}:${revision ?? "current"}`}
      id={articleId}
      revision={revision ? Number(revision) : undefined}
    />
  );
}
