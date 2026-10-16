'use client';

import React, { useEffect, useState, use } from 'react';
import { Lock, Loader2 } from 'lucide-react';
import {
  type DeckSection, type DeckAttachment,
  fetchPublicMeta, fetchPublicDeck,
} from '@/lib/presentationsApi';
import PresentationViewer from '@/components/presentations/PresentationViewer';

// Next 15: dynamic route params arrive as a Promise. Client components
// unwrap with React.use(); server components await directly.
interface PageProps { params: Promise<{ slug: string }> }

const brandColor = '#385854';

/**
 * Public presentation viewer. No auth required. If the deck has a password,
 * we prompt for it and persist on the session for the slug.
 */
export default function PublicPresentationPage({ params }: PageProps) {
  const { slug } = use(params);
  const [meta, setMeta] = useState<{ title: string; has_password: boolean } | null>(null);
  const [deck, setDeck] = useState<{ title: string; sections: DeckSection[]; attachments: DeckAttachment[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pwInput, setPwInput] = useState('');
  const [unlocking, setUnlocking] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const m = await fetchPublicMeta(slug);
        if (cancelled) return;
        setMeta(m);
        const cached = typeof window !== 'undefined' ? sessionStorage.getItem(`deck-pw-${slug}`) : null;
        if (!m.has_password) {
          const d = await fetchPublicDeck(slug);
          if (!cancelled) setDeck(d);
        } else if (cached) {
          try {
            const d = await fetchPublicDeck(slug, cached);
            if (!cancelled) setDeck(d);
          } catch { /* fall through to password prompt */ }
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Not found');
      }
    })();
    return () => { cancelled = true; };
  }, [slug]);

  const unlock = async () => {
    setUnlocking(true);
    try {
      const d = await fetchPublicDeck(slug, pwInput);
      sessionStorage.setItem(`deck-pw-${slug}`, pwInput);
      // Mirror the slug pw into the key CitationPreview reads when fetching
      // citations on a password-protected public deck.
      sessionStorage.setItem(`ah-deck-pw:${slug}`, pwInput);
      setDeck(d);
    } catch {
      setError('Incorrect password');
    } finally {
      setUnlocking(false);
    }
  };

  if (error && !deck) {
    return <div className="min-h-screen flex items-center justify-center p-6 text-gray-700">{error}</div>;
  }
  if (!meta) {
    return <div className="min-h-screen flex items-center justify-center"><Loader2 className="w-5 h-5 animate-spin text-gray-400" /></div>;
  }

  if (meta.has_password && !deck) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6 bg-gray-50">
        <div className="bg-white rounded-lg shadow border border-gray-200 p-6 max-w-sm w-full">
          <div className="flex items-center gap-2 mb-4">
            <Lock className="w-5 h-5" style={{ color: brandColor }} />
            <h2 className="font-semibold text-gray-900">Password required</h2>
          </div>
          <p className="text-sm text-gray-600 mb-3">{meta.title}</p>
          <input
            type="password" value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && unlock()}
            placeholder="Enter password"
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm mb-3"
            autoFocus
          />
          <button
            onClick={unlock} disabled={unlocking || !pwInput}
            className="w-full py-2 rounded text-white text-sm disabled:opacity-50"
            style={{ backgroundColor: brandColor }}
          >
            {unlocking ? 'Unlocking…' : 'Unlock'}
          </button>
          {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
        </div>
      </div>
    );
  }

  if (!deck) {
    return <div className="min-h-screen flex items-center justify-center"><Loader2 className="w-5 h-5 animate-spin text-gray-400" /></div>;
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <PresentationViewer
        title={deck.title}
        sections={deck.sections}
        attachments={deck.attachments}
        mode="public"
        publicSlug={slug}
      />
    </div>
  );
}
