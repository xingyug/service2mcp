import { describe, it, expect, beforeEach } from "vitest";
import { useAuthStore } from "../auth-store";

const STORAGE_KEY = "auth-storage";

function resetStore() {
  useAuthStore.setState({
    token: null,
    user: null,
    isAuthenticated: false,
  });
}

describe("auth-store", () => {
  beforeEach(() => {
    resetStore();
  });

  // -----------------------------------------------------------------------
  // Initial state
  // -----------------------------------------------------------------------

  it("has null token by default", () => {
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("has null user by default", () => {
    expect(useAuthStore.getState().user).toBeNull();
  });

  it("is not authenticated by default", () => {
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  // -----------------------------------------------------------------------
  // login
  // -----------------------------------------------------------------------

  it("login() sets token, user and isAuthenticated", () => {
    const user = { username: "alice", email: "alice@example.com", roles: ["admin"] };
    useAuthStore.getState().login("tok-123", user);

    const state = useAuthStore.getState();
    expect(state.token).toBe("tok-123");
    expect(state.user).toEqual(user);
    expect(state.isAuthenticated).toBe(true);
  });

  it("login() with minimal user (username only)", () => {
    useAuthStore.getState().login("tok-min", { username: "bob" });

    const state = useAuthStore.getState();
    expect(state.token).toBe("tok-min");
    expect(state.user).toEqual({ username: "bob" });
    expect(state.isAuthenticated).toBe(true);
  });

  // -----------------------------------------------------------------------
  // logout
  // -----------------------------------------------------------------------

  it("logout() clears token, user and isAuthenticated", () => {
    useAuthStore.getState().login("tok-123", { username: "alice" });
    useAuthStore.getState().logout();

    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
    expect(state.isAuthenticated).toBe(false);
  });

  it("logout() is safe to call when already logged out", () => {
    useAuthStore.getState().logout();

    const state = useAuthStore.getState();
    expect(state.token).toBeNull();
    expect(state.isAuthenticated).toBe(false);
  });

  // -----------------------------------------------------------------------
  // setToken
  // -----------------------------------------------------------------------

  it("setToken() updates only the token", () => {
    const user = { username: "carol" };
    useAuthStore.getState().login("old-token", user);
    useAuthStore.getState().setToken("new-token");

    const state = useAuthStore.getState();
    expect(state.token).toBe("new-token");
    expect(state.user).toEqual(user);
    expect(state.isAuthenticated).toBe(true);
  });

  it("setToken() does not change isAuthenticated when called before login", () => {
    useAuthStore.getState().setToken("orphan-token");

    const state = useAuthStore.getState();
    expect(state.token).toBe("orphan-token");
    expect(state.isAuthenticated).toBe(false);
  });

  // -----------------------------------------------------------------------
  // Multiple login/logout cycles
  // -----------------------------------------------------------------------

  it("handles multiple login/logout cycles correctly", () => {
    const user1 = { username: "user1" };
    const user2 = { username: "user2" };

    useAuthStore.getState().login("tok-1", user1);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().user).toEqual(user1);

    useAuthStore.getState().logout();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);

    useAuthStore.getState().login("tok-2", user2);
    expect(useAuthStore.getState().token).toBe("tok-2");
    expect(useAuthStore.getState().user).toEqual(user2);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);

    useAuthStore.getState().logout();
    expect(useAuthStore.getState().token).toBeNull();
  });

  it("login() overwrites previous user without logout", () => {
    useAuthStore.getState().login("tok-a", { username: "a" });
    useAuthStore.getState().login("tok-b", { username: "b" });

    expect(useAuthStore.getState().token).toBe("tok-b");
    expect(useAuthStore.getState().user?.username).toBe("b");
  });

  // -----------------------------------------------------------------------
  // Persistence
  // -----------------------------------------------------------------------

  it("persists state to localStorage under the correct key", () => {
    useAuthStore.getState().login("persist-tok", { username: "persist" });

    const raw = localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();

    const stored = JSON.parse(raw!);
    expect(stored.state.token).toBe("persist-tok");
    expect(stored.state.isAuthenticated).toBe(true);
    expect(stored.state.user.username).toBe("persist");
  });

  it("clears persisted state on logout", () => {
    useAuthStore.getState().login("tok", { username: "u" });
    useAuthStore.getState().logout();

    const raw = localStorage.getItem(STORAGE_KEY);
    expect(raw).not.toBeNull();

    const stored = JSON.parse(raw!);
    expect(stored.state.token).toBeNull();
    expect(stored.state.isAuthenticated).toBe(false);
    expect(stored.state.user).toBeNull();
  });

  it("restores state from localStorage on rehydration", () => {
    const payload = {
      state: {
        token: "rehydrate-tok",
        user: { username: "rehydrate" },
        isAuthenticated: true,
      },
      version: 0,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));

    useAuthStore.persist.rehydrate();

    const state = useAuthStore.getState();
    expect(state.token).toBe("rehydrate-tok");
    expect(state.isAuthenticated).toBe(true);
    expect(state.user?.username).toBe("rehydrate");
  });
});
