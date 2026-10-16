"use client";

// In Next 15, `dynamic({ ssr: false })` is no longer allowed inside a
// Server Component. This thin client wrapper exists so layout.tsx (a
// Server Component) can still skip SSR for Providers — preserving the
// Next 14 behavior. The reason we skip SSR: the provider subtree pulls
// in react-pdf (PDFViewer → pdfjs-dist), which references DOMMatrix at
// module load — that crashes Node-side static rendering.

import dynamic from "next/dynamic";

const Providers = dynamic(() => import("./Providers"), {
  ssr: false,
  loading: () => null,
});

export default function ProvidersClient({ children }: { children: React.ReactNode }) {
  return <Providers>{children}</Providers>;
}
