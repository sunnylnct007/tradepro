import { initializeApp, type FirebaseApp } from "firebase/app";
import { getAnalytics, isSupported, type Analytics } from "firebase/analytics";
import {
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  type Auth,
  type User,
} from "firebase/auth";

// Firebase web config is NOT a secret — it ships in the client bundle. Values
// are loaded from VITE_FIREBASE_* env vars so the same build can target
// multiple Firebase projects (staging, prod). Lock down abuse via:
//   - Google Cloud Console -> API key restrictions (HTTP referrers)
//   - Firebase Security Rules for Firestore / Storage / RTDB
const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY ?? "",
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN ?? "",
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID ?? "",
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET ?? "",
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID ?? "",
  appId: import.meta.env.VITE_FIREBASE_APP_ID ?? "",
  measurementId: import.meta.env.VITE_FIREBASE_MEASUREMENT_ID ?? "",
};

let appInstance: FirebaseApp | null = null;
let authInstance: Auth | null = null;
let analyticsInstance: Analytics | null = null;

export function isFirebaseConfigured(): boolean {
  return Boolean(firebaseConfig.apiKey);
}

export function initFirebase(): FirebaseApp | null {
  if (!isFirebaseConfigured()) return null; // local dev without env set
  if (appInstance) return appInstance;
  appInstance = initializeApp(firebaseConfig);
  authInstance = getAuth(appInstance);
  void isSupported().then((ok) => {
    if (ok && appInstance) analyticsInstance = getAnalytics(appInstance);
  });
  return appInstance;
}

export function getFirebaseApp(): FirebaseApp | null {
  return appInstance;
}

export function getFirebaseAnalytics(): Analytics | null {
  return analyticsInstance;
}

export function getFirebaseAuth(): Auth | null {
  return authInstance;
}

export async function signInWithGoogle(): Promise<User | null> {
  if (!authInstance) return null;
  const provider = new GoogleAuthProvider();
  const result = await signInWithPopup(authInstance, provider);
  return result.user;
}

export async function signOutFromFirebase(): Promise<void> {
  if (authInstance) await signOut(authInstance);
}

export function subscribeToAuth(cb: (user: User | null) => void): () => void {
  if (!authInstance) {
    cb(null);
    return () => {};
  }
  return onAuthStateChanged(authInstance, cb);
}

export async function getIdToken(): Promise<string | null> {
  const user = authInstance?.currentUser;
  return user ? await user.getIdToken() : null;
}
