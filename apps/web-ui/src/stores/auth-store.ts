import { create } from "zustand";
import { persist } from "zustand/middleware";

const AUTH_STORAGE_KEY = "auth-storage";
const NETWORK_AUTH_TOKEN_KEY = "auth_token";

interface User {
  username?: string;
  subject?: string;
  tokenType?: string;
  claims?: Record<string, unknown>;
  email?: string;
  roles?: string[];
}

interface AuthState {
  token: string | null;
  user: User | null;
  isAuthenticated: boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
  setToken: (token: string) => void;
}

function syncNetworkAuthToken(token: string | null) {
  if (typeof window === "undefined") {
    return;
  }
  if (token) {
    localStorage.setItem(NETWORK_AUTH_TOKEN_KEY, token);
  } else {
    localStorage.removeItem(NETWORK_AUTH_TOKEN_KEY);
  }
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,
      login: (token, user) => {
        syncNetworkAuthToken(token);
        set({ token, user, isAuthenticated: true });
      },
      logout: () => {
        syncNetworkAuthToken(null);
        set({ token: null, user: null, isAuthenticated: false });
      },
      setToken: (token) => {
        syncNetworkAuthToken(token);
        set({ token });
      },
    }),
    {
      name: AUTH_STORAGE_KEY,
      onRehydrateStorage: () => (state) => {
        syncNetworkAuthToken(
          state?.isAuthenticated && state.token ? state.token : null,
        );
      },
    },
  ),
);
