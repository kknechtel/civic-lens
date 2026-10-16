"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "@/app/contexts/AuthContext";
import { DeckChatProvider } from "@/app/contexts/DeckChatContext";
import Sidebar from "@/components/Sidebar";
import MobileNav from "@/components/MobileNav";
import GlobalChat from "@/components/GlobalChat";
import LoginForm from "@/components/LoginForm";
import { MunicipalityProvider } from "@/app/contexts/MunicipalityContext";
import { changePassword, getMe } from "@/lib/api";

const brandColor = "#385854";

// Shared badge logo for the change-password / pending-approval screens.
// Same logo across both civic and events apps.
function BrandLogo({ size = 12 }: { size?: 12 | 14 }) {
  const sizeCls = size === 14 ? "w-16 h-16" : "w-14 h-14";
  return (
    /* eslint-disable-next-line @next/next/no-img-element */
    <img src="/icon-192.png" alt="" className={`${sizeCls} object-contain`} />
  );
}

function ChangePasswordScreen() {
  const { user, setUser } = useAuth();
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (newPassword.length < 6) {
      setError("Password must be at least 6 characters");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    setLoading(true);
    try {
      await changePassword(newPassword);
      const me = await getMe();
      setUser(me);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full bg-white rounded-xl shadow-lg p-8">
        <div className="flex items-center justify-center mb-1">
          <BrandLogo size={12} />
        </div>
        <h1 className="text-xl font-bold text-gray-900 text-center mt-3 mb-1">
          Change Your Password
        </h1>
        <p className="text-sm text-gray-500 text-center mb-6">
          Welcome, {user?.full_name || user?.email}. Please set a new password to continue.
        </p>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">New Password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              autoComplete="new-password"
              autoFocus
              className="block w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm focus:ring-2 focus:border-transparent"
              style={{ "--tw-ring-color": brandColor } as React.CSSProperties}
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Confirm Password</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              autoComplete="new-password"
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
            disabled={loading}
            className="w-full text-white py-2.5 rounded-lg hover:opacity-90 disabled:opacity-50 transition-colors font-medium"
            style={{ backgroundColor: brandColor }}
          >
            {loading ? "..." : "Set New Password"}
          </button>
        </form>
      </div>
    </div>
  );
}

function PendingApproval() {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="max-w-md w-full bg-white rounded-xl shadow-lg p-8 text-center">
        <div className="mx-auto mb-4 inline-flex">
          <BrandLogo size={14} />
        </div>
        <h1 className="text-xl font-bold text-gray-900 mb-2">Account Pending Approval</h1>
        <p className="text-sm text-gray-600 mb-1">
          Welcome, <span className="font-medium">{user?.full_name || user?.email}</span>
        </p>
        <p className="text-sm text-gray-500 mb-6">
          Your account has been created and is waiting for admin approval.
          You&apos;ll be able to access the platform once an administrator activates your account.
        </p>
        <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg mb-6">
          <p className="text-xs text-amber-800">
            An administrator will review and approve your request shortly.
          </p>
        </div>
        <button
          onClick={logout}
          className="text-sm hover:underline"
          style={{ color: brandColor }}
        >
          Sign out
        </button>
      </div>
    </div>
  );
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, pendingApproval } = useAuth();
  const pathname = usePathname();

  // Public routes — published presentations under /p/{slug} and any future
  // public-only pages. They render bare without the sidebar/mobile-nav chrome.
  const isPublicRoute =
    (pathname?.startsWith("/p/") ?? false) || pathname === "/privacy";
  if (isPublicRoute) {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="flex items-center gap-3">
          <div
            className="w-5 h-5 border-2 rounded-full animate-spin"
            style={{ borderColor: "#38585440", borderTopColor: brandColor }}
          />
          <span className="text-sm text-gray-600">Loading...</span>
        </div>
      </div>
    );
  }

  if (!user) {
    return <LoginForm />;
  }

  if (pendingApproval) {
    return <PendingApproval />;
  }

  if (user.must_change_password) {
    return <ChangePasswordScreen />;
  }

  return (
    <>
      {/* Desktop layout */}
      <div className="hidden md:flex h-screen">
        <Sidebar />
        <main className="flex-1 overflow-auto">{children}</main>
      </div>
      {/* Mobile layout. pb-36 on the scrollable main reserves room for both
          MobileNav (h-16 = 64px) AND the floating chat FAB. The FAB is
          ~46px tall, anchored 80px above the viewport bottom — so its top
          edge sits at viewport-126px. Padding the scroll area by 144px
          (pb-36) gives the last row of content 18px of breathing room
          above the FAB, instead of having it clipped underneath. */}
      <div className="md:hidden flex flex-col h-screen">
        <main className="flex-1 overflow-auto pb-36">{children}</main>
        <MobileNav />
      </div>
      <GlobalChat />
    </>
  );
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <MunicipalityProvider>
      <AuthProvider>
        <DeckChatProvider>
          <AuthGate>{children}</AuthGate>
        </DeckChatProvider>
      </AuthProvider>
      </MunicipalityProvider>
    </QueryClientProvider>
  );
}
