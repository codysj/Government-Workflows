import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ToastKind = "info" | "success" | "error";

interface ToastItem {
  id: number;
  message: string;
  kind: ToastKind;
}

interface ToastContextValue {
  /** Show a toast (auto-dismisses after >= 6s) and mirror it in the aria-live region. */
  showToast: (message: string, kind?: ToastKind) => void;
  /** Announce a message in the polite aria-live region without a visible toast. */
  announce: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside ToastProvider");
  return ctx;
}

const TOAST_DURATION_MS = 7000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [liveMessage, setLiveMessage] = useState("");
  const nextId = useRef(1);
  const timers = useRef<Set<number>>(new Set());

  const schedule = useCallback((fn: () => void, ms: number) => {
    const id = window.setTimeout(() => {
      timers.current.delete(id);
      fn();
    }, ms);
    timers.current.add(id);
  }, []);

  // Clear pending timers on unmount so nothing fires after teardown.
  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const id of pending) window.clearTimeout(id);
      pending.clear();
    };
  }, []);

  const announce = useCallback(
    (message: string) => {
      // Clear first so repeating the same message is re-announced.
      setLiveMessage("");
      schedule(() => setLiveMessage(message), 50);
    },
    [schedule],
  );

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const showToast = useCallback(
    (message: string, kind: ToastKind = "info") => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, message, kind }]);
      announce(message);
      schedule(() => dismiss(id), TOAST_DURATION_MS);
    },
    [announce, dismiss, schedule],
  );

  return (
    <ToastContext.Provider value={{ showToast, announce }}>
      {children}
      <div className="toast-stack">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast-${toast.kind}`}>
            <span>{toast.message}</span>
            <button
              type="button"
              className="toast-dismiss"
              onClick={() => dismiss(toast.id)}
              aria-label="Dismiss notification"
            >
              x
            </button>
          </div>
        ))}
      </div>
      <div aria-live="polite" className="visually-hidden">
        {liveMessage}
      </div>
    </ToastContext.Provider>
  );
}
