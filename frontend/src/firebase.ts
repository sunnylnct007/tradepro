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

/**
 * Request an on-demand screen run.
 *
 * Writes a `jobs` doc with status="pending", which `tradepro-worker` on the
 * Mac already watches. The screen MUST run there — it reads the local bar
 * store, which the API box does not have — and this collection is how the UI
 * has always reached that host, so the trigger reuses it rather than
 * introducing a second mechanism.
 *
 * Firestore is imported dynamically: this app previously loaded Firebase for
 * AUTH only, and pulling the Firestore SDK into the main bundle for one button
 * would cost every page load. Returns the job id so the caller can poll it.
 */
export async function requestScreenRun(screen: string): Promise<string | null> {
  const app = getFirebaseApp();
  if (!app) return null;
  const { getFirestore, collection, addDoc, serverTimestamp } =
    await import("firebase/firestore");
  const ref = await addDoc(collection(getFirestore(app), "jobs"), {
    kind: "screen",
    status: "pending",
    request: { screen },
    requested_at: serverTimestamp(),
    requested_by: "desk-ui",
  });
  return ref.id;
}

/** Watch one job doc until it leaves "pending"/"running". Returns unsubscribe. */
export async function watchJob(
  jobId: string,
  cb: (status: string, data: Record<string, unknown>) => void,
): Promise<() => void> {
  const app = getFirebaseApp();
  if (!app) return () => {};
  const { getFirestore, doc, onSnapshot } = await import("firebase/firestore");
  return onSnapshot(doc(getFirestore(app), "jobs", jobId), (snap) => {
    const d = (snap.data() || {}) as Record<string, unknown>;
    cb(String(d.status ?? "unknown"), d);
  });
}
