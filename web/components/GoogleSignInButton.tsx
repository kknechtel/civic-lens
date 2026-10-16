"use client";

// Google Sign-In via Google Identity Services (GIS). Renders the official
// Google-branded button that issues an ID token, which we POST to
// /api/auth/google for server-side verification.
//
// Why GIS instead of the older `gapi.auth2` flow: GIS is what Google
// supports going forward, takes ~3KB of JS, and the button is one-line.
// The downside is the click handler can't be customised — that's a small
// price for the conformant Google branding which most users expect.
//
// To activate, set NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID at build time
// (amplify.yml injects it into web/.env.local). When it's unset, this
// component renders null so the form falls back to password-only.

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/app/contexts/AuthContext";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (cfg: {
            client_id: string;
            callback: (resp: { credential: string }) => void;
            auto_select?: boolean;
            cancel_on_tap_outside?: boolean;
          }) => void;
          renderButton: (parent: HTMLElement, opts: {
            type?: "standard" | "icon";
            theme?: "outline" | "filled_blue" | "filled_black";
            size?: "small" | "medium" | "large";
            text?: "signin_with" | "signup_with" | "continue_with" | "signin";
            shape?: "rectangular" | "pill" | "circle" | "square";
            width?: number;
          }) => void;
        };
      };
    };
  }
}

const GSI_SRC = "https://accounts.google.com/gsi/client";
const CLIENT_ID = process.env.NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID || "";

export default function GoogleSignInButton({
  onError,
}: {
  onError?: (msg: string) => void;
}) {
  const { loginWithGoogle } = useAuth();
  const containerRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  // Load the GSI script exactly once across the app lifetime.
  useEffect(() => {
    if (!CLIENT_ID) return;
    if (window.google?.accounts?.id) {
      setReady(true);
      return;
    }
    const existing = document.querySelector(`script[src="${GSI_SRC}"]`);
    if (existing) {
      existing.addEventListener("load", () => setReady(true));
      return;
    }
    const script = document.createElement("script");
    script.src = GSI_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => setReady(true);
    document.head.appendChild(script);
  }, []);

  // Initialize + render the button once GIS is loaded.
  useEffect(() => {
    if (!ready || !CLIENT_ID || !containerRef.current || !window.google) return;
    window.google.accounts.id.initialize({
      client_id: CLIENT_ID,
      callback: async (resp) => {
        try {
          await loginWithGoogle(resp.credential);
        } catch (err: unknown) {
          const msg = err instanceof Error ? err.message : "Google sign-in failed";
          onError?.(msg);
        }
      },
      auto_select: false,
      cancel_on_tap_outside: true,
    });
    window.google.accounts.id.renderButton(containerRef.current, {
      type: "standard",
      theme: "outline",
      size: "large",
      text: "continue_with",
      shape: "rectangular",
      width: 280,
    });
  }, [ready, loginWithGoogle, onError]);

  if (!CLIENT_ID) return null;
  return <div ref={containerRef} className="flex justify-center" />;
}
