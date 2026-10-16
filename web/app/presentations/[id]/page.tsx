'use client';

import React, { useEffect, use } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/app/contexts/AuthContext';
import PresentationEditor from '@/components/presentations/PresentationEditor';
import { Loader2 } from 'lucide-react';

// Next 15: route params are now a Promise; unwrap with React.use() in
// client components.
interface PageProps { params: Promise<{ id: string }> }

export default function PresentationEditorPage({ params }: PageProps) {
  const { id } = use(params);
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && !user) router.push('/');
  }, [user, loading, router]);

  if (loading || !user) {
    return <div className="p-6 flex items-center gap-2 text-gray-500"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;
  }

  return (
    <div className="h-[calc(100vh-0px)]">
      <PresentationEditor presentationId={id} />
    </div>
  );
}
