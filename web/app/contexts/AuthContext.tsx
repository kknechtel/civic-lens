"use client";

import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from "react";
import { User, getMe, login as apiLogin, loginWithGoogle as apiLoginGoogle, logout as apiLogout, signup as apiSignup } from "@/lib/api";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  pendingApproval: boolean;
  inviteToken: string | null;
  login: (email: string, password: string) => Promise<void>;
  loginWithGoogle: (idToken: string) => Promise<void>;
  magicLinkLogin: (inviteToken: string, email: string, password: string, fullName?: string) => Promise<void>;
  signup: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => void;
  setUser: (user: User) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [pendingApproval, setPendingApproval] = useState(false);
  const [inviteToken, setInviteToken] = useState<string | null>(null);

  // Extract invite token from URL on mount
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const invite = params.get("invite");
    if (invite) {
      setInviteToken(invite);
      const url = new URL(window.location.href);
      url.searchParams.delete("invite");
      window.history.replaceState({}, "", url.pathname);
    }
  }, []);

  // Check for existing token on mount
  useEffect(() => {
    const token = localStorage.getItem("ah_token");
    if (token) {
      getMe()
        .then((me) => {
          setUser(me);
          setPendingApproval(!me.is_active);
        })
        .catch(() => {
          localStorage.removeItem("ah_token");
          setUser(null);
          setPendingApproval(false);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const data = await apiLogin(email, password);
    localStorage.setItem("ah_token", data.access_token);
    if (data.pending_approval) {
      const me = await getMe();
      setUser(me);
      setPendingApproval(true);
    } else {
      const me = await getMe();
      setUser(me);
      setPendingApproval(false);
    }
  }, []);

  const loginWithGoogle = useCallback(async (idToken: string) => {
    const data = await apiLoginGoogle(idToken);
    localStorage.setItem("ah_token", data.access_token);
    const me = await getMe();
    setUser(me);
    setPendingApproval(!!data.pending_approval);
  }, []);

  const magicLinkLogin = useCallback(async (token: string, email: string, password: string, fullName?: string) => {
    const res = await fetch("/api/auth/magic-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invite_token: token, email, password, full_name: fullName }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Failed to accept invite" }));
      throw new Error(err.detail || "Failed to accept invite");
    }
    const data = await res.json();
    localStorage.setItem("ah_token", data.access_token);
    const me = await getMe();
    setUser(me);
    setPendingApproval(false);
    setInviteToken(null);
  }, []);

  const signup = useCallback(async (email: string, password: string, fullName?: string) => {
    const data = await apiSignup(email, password, fullName);
    localStorage.setItem("ah_token", data.access_token);
    const me = await getMe();
    setUser(me);
    setPendingApproval(!!data.pending_approval);
  }, []);

  const logout = useCallback(() => {
    apiLogout();
    setUser(null);
    setPendingApproval(false);
  }, []);

  return (
    <AuthContext.Provider value={{
      user, loading, pendingApproval, inviteToken,
      login, loginWithGoogle, magicLinkLogin, signup, logout, setUser,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
