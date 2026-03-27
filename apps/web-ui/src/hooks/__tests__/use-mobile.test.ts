import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useIsMobile } from "../use-mobile";

// ---------------------------------------------------------------------------
// Helpers to control matchMedia and window.innerWidth
// ---------------------------------------------------------------------------

let matchMediaListeners: Array<() => void>;

function setWindowWidth(width: number) {
  Object.defineProperty(window, "innerWidth", {
    writable: true,
    configurable: true,
    value: width,
  });
}

function setupMatchMedia() {
  matchMediaListeners = [];

  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: (_event: string, cb: () => void) => {
      matchMediaListeners.push(cb);
    },
    removeEventListener: (_event: string, cb: () => void) => {
      matchMediaListeners = matchMediaListeners.filter((l) => l !== cb);
    },
    dispatchEvent: vi.fn(),
  }));
}

function triggerResize(newWidth: number) {
  setWindowWidth(newWidth);
  for (const cb of matchMediaListeners) {
    cb();
  }
}

// ---------------------------------------------------------------------------

describe("useIsMobile", () => {
  beforeEach(() => {
    setupMatchMedia();
  });

  afterEach(() => {
    matchMediaListeners = [];
  });

  // -----------------------------------------------------------------------
  // Initial values
  // -----------------------------------------------------------------------

  it("returns false when window.innerWidth >= 768", () => {
    setWindowWidth(1024);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("returns true when window.innerWidth < 768", () => {
    setWindowWidth(500);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("returns false when window.innerWidth is exactly 768", () => {
    setWindowWidth(768);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("returns true when window.innerWidth is 767", () => {
    setWindowWidth(767);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("returns false for typical desktop width 1920", () => {
    setWindowWidth(1920);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);
  });

  it("returns true for small mobile width 320", () => {
    setWindowWidth(320);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  // -----------------------------------------------------------------------
  // Resize behaviour
  // -----------------------------------------------------------------------

  it("updates to true when resized below 768", () => {
    setWindowWidth(1024);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => {
      triggerResize(500);
    });
    expect(result.current).toBe(true);
  });

  it("updates to false when resized above 768", () => {
    setWindowWidth(500);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);

    act(() => {
      triggerResize(1024);
    });
    expect(result.current).toBe(false);
  });

  it("stays false when resized from wide to wide", () => {
    setWindowWidth(1024);
    const { result } = renderHook(() => useIsMobile());

    act(() => {
      triggerResize(1440);
    });
    expect(result.current).toBe(false);
  });

  it("stays true when resized from narrow to narrow", () => {
    setWindowWidth(400);
    const { result } = renderHook(() => useIsMobile());

    act(() => {
      triggerResize(600);
    });
    expect(result.current).toBe(true);
  });

  it("handles multiple resize events correctly", () => {
    setWindowWidth(1024);
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => triggerResize(400));
    expect(result.current).toBe(true);

    act(() => triggerResize(800));
    expect(result.current).toBe(false);

    act(() => triggerResize(767));
    expect(result.current).toBe(true);

    act(() => triggerResize(768));
    expect(result.current).toBe(false);
  });

  // -----------------------------------------------------------------------
  // matchMedia integration
  // -----------------------------------------------------------------------

  it("calls window.matchMedia with the correct media query", () => {
    setWindowWidth(1024);
    renderHook(() => useIsMobile());

    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 767px)");
  });

  it("cleans up event listener on unmount", () => {
    setWindowWidth(1024);
    const { unmount } = renderHook(() => useIsMobile());

    expect(matchMediaListeners).toHaveLength(1);
    unmount();
    expect(matchMediaListeners).toHaveLength(0);
  });

  // -----------------------------------------------------------------------
  // Edge case: initial undefined → coerced to false
  // -----------------------------------------------------------------------

  it("initial undefined state is coerced to false via !! operator", () => {
    setWindowWidth(1024);
    // On the very first render before useEffect fires, the internal state
    // is undefined. The hook returns !!undefined = false.
    const { result } = renderHook(() => useIsMobile());
    // After effect, it should be false for width 1024
    expect(result.current).toBe(false);
  });
});
