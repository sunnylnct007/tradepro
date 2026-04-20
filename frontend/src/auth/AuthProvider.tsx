import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { User } from "firebase/auth";
import {
  isFirebaseConfigured,
  signInWithGoogle,
  signOutFromFirebase,
  subscribeToAuth,
} from "../firebase";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  firebaseAvailable: boolean;
  error: string | null;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const firebaseAvailable = isFirebaseConfigured();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(firebaseAvailable);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!firebaseAvailable) {
      setLoading(false);
      return;
    }
    return subscribeToAuth((u) => {
      setUser(u);
      setLoading(false);
    });
  }, [firebaseAvailable]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      firebaseAvailable,
      error,
      signIn: async () => {
        setError(null);
        try {
          await signInWithGoogle();
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          // Firebase error codes come through as `auth/...` — keep them visible
          // so unauthorised-domain or popup-blocked failures don't fail silently.
          setError(msg);
          // eslint-disable-next-line no-console
          console.error("sign-in failed:", e);
        }
      },
      signOut: async () => {
        setError(null);
        await signOutFromFirebase();
      },
    }),
    [user, loading, firebaseAvailable, error],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
