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
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const firebaseAvailable = isFirebaseConfigured();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(firebaseAvailable);

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
      signIn: async () => { await signInWithGoogle(); },
      signOut: async () => { await signOutFromFirebase(); },
    }),
    [user, loading, firebaseAvailable],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
