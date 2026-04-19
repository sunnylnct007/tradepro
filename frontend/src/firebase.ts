import { initializeApp, type FirebaseApp } from "firebase/app";
import { getAnalytics, isSupported, type Analytics } from "firebase/analytics";

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
let analyticsInstance: Analytics | null = null;

export function initFirebase(): FirebaseApp | null {
  if (!firebaseConfig.apiKey) return null; // dev without env set
  if (appInstance) return appInstance;
  appInstance = initializeApp(firebaseConfig);
  // Analytics only initialises in browsers that support it (e.g. not in SSR).
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
