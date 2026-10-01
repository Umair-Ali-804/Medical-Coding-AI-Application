import { Shell } from "@/components/Shell";
import { ReviewWorkspace } from "@/components/ReviewWorkspace";
import { requireUser } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { user } = await requireUser();
  const { id } = await params;
  return (
    <Shell user={user}>
      <ReviewWorkspace documentId={id} role={user.role} />
    </Shell>
  );
}
