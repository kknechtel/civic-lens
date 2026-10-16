"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { useAuth } from "@/app/contexts/AuthContext";
import GoogleSignInButton from "@/components/GoogleSignInButton";
import { useMunicipality } from "@/app/contexts/MunicipalityContext";

const HAS_GOOGLE = !!process.env.NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID;

export default function LoginForm() {
  const { login, magicLinkLogin, inviteToken } = useAuth();
  const municipality = useMunicipality();
  const brandColor = municipality.branding.theme_color;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Invite state
  const [inviteValid, setInviteValid] = useState<boolean | null>(null);
  const [inviteEmail, setInviteEmail] = useState<string | null>(null);
  const [inviteChecking, setInviteChecking] = useState(false);

  // Check invite token on load
  useEffect(() => {
    if (!inviteToken) return;
    setInviteChecking(true);
    fetch(`/api/auth/invite/${inviteToken}`)
      .then((r) => r.json())
      .then((data) => {
        setInviteValid(data.valid);
        if (data.email) {
          setInviteEmail(data.email);
          setEmail(data.email);
        }
      })
      .catch(() => setInviteValid(false))
      .finally(() => setInviteChecking(false));
  }, [inviteToken]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (inviteToken && inviteValid) {
        // Magic link flow: accept invite + set password
        await magicLinkLogin(inviteToken, email, password, fullName || undefined);
      } else {
        // Standard login
        await login(email, password);
      }
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const isInviteFlow = inviteToken && inviteValid;
  const headerBg = brandColor;
  const headerTitle = municipality.branding.app_name;
  const headerSub = "Document Intelligence Platform";

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full">
        <div className="bg-white rounded-xl shadow-lg p-8">
          {/* Header — shared badge logo across both apps. */}
          <div className="flex items-center justify-center mb-1">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/icon-192.png"
              alt=""
              className="w-16 h-16 object-contain"
            />
          </div>
          <h1 className="text-2xl font-bold text-gray-900 text-center mt-3">
            {headerTitle}
          </h1>
          <p className="text-sm text-gray-500 text-center mt-1 mb-6">
            {headerSub}
          </p>

          {/* Google Sign-In — primary on events-app, secondary on civic.
              Hide entirely (button + divider) when CLIENT_ID is unset so
              we don't render a dangling "or email" separator. */}
          {HAS_GOOGLE && (
            <>
              <div className="mb-4 flex justify-center">
                <GoogleSignInButton onError={setError} />
              </div>
              {!inviteToken && (
                <div className="flex items-center gap-2 mb-4 text-[10px] uppercase tracking-wider text-gray-400">
                  <div className="flex-1 h-px bg-gray-200" />
                  <span>or email</span>
                  <div className="flex-1 h-px bg-gray-200" />
                </div>
              )}
            </>
          )}

          {/* Invite checking */}
          {inviteToken && inviteChecking && (
            <div className="mb-4 p-3 bg-gray-50 rounded-lg text-center">
              <div className="flex items-center justify-center gap-2">
                <div
                  className="w-4 h-4 border-2 rounded-full animate-spin"
                  style={{ borderColor: `${brandColor}40`, borderTopColor: brandColor }}
                />
                <span className="text-sm text-gray-600">Checking invite...</span>
              </div>
            </div>
          )}

          {/* Invalid invite */}
          {inviteToken && inviteValid === false && (
            <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-sm text-red-700 text-center">
                This invite link is invalid or has expired.
              </p>
            </div>
          )}

          {/* Valid invite */}
          {isInviteFlow && (
            <div className="mb-4 p-3 rounded-lg border" style={{ borderColor: brandColor, backgroundColor: `${brandColor}08` }}>
              <p className="text-sm text-center font-medium" style={{ color: brandColor }}>
                You&apos;ve been invited! Set your password below.
              </p>
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                disabled={!!inviteEmail}
                autoComplete="email"
                className="block w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:ring-2 focus:border-transparent disabled:bg-gray-100 disabled:text-gray-600"
                style={{ "--tw-ring-color": brandColor } as React.CSSProperties}
              />
            </div>

            {isInviteFlow && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Full Name</label>
                <input
                  type="text"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  autoComplete="name"
                  placeholder="Optional"
                  className="block w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:ring-2 focus:border-transparent"
                  style={{ "--tw-ring-color": brandColor } as React.CSSProperties}
                />
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {isInviteFlow ? "Set Password" : "Password"}
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete={isInviteFlow ? "new-password" : "current-password"}
                className="block w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:ring-2 focus:border-transparent"
                style={{ "--tw-ring-color": brandColor } as React.CSSProperties}
              />
            </div>

            {error && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-red-600 text-sm">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || (inviteToken !== null && !inviteValid)}
              className="w-full text-white py-2.5 rounded-lg hover:opacity-90 disabled:opacity-50 transition-colors font-medium"
              style={{ backgroundColor: headerBg }}
            >
              {loading ? "..." : isInviteFlow ? "Create Account & Sign In" : "Sign In"}
            </button>
          </form>

        </div>

        {municipality.name && (
          <p className="text-center text-xs text-gray-400 mt-4">
            {municipality.name}{municipality.state ? `, ${municipality.state}` : ""}
          </p>
        )}
        <a
          href="/privacy"
          className="block text-center text-[11px] text-gray-400 hover:text-gray-600 mt-2"
        >
          Privacy Policy
        </a>
      </div>
    </div>
  );
}
