import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

interface ToastAction {
  label: string;
  onClick: () => void;
}
interface ToastOpts {
  action?: ToastAction;
  duration?: number;
}
interface ToastItem {
  id: number;
  message: string;
  action?: ToastAction;
}

const ToastContext = createContext<(message: string, opts?: ToastOpts) => void>(() => {});

/** Fire a toast: `toast("Saved")` or `toast("Archived", { action: { label: "Undo", onClick } })`. */
export function useToast() {
  return useContext(ToastContext);
}

// Module-level escape hatch for non-component code (e.g. the axios response
// interceptor in api/client.ts) that needs to surface a toast but can't call
// useToast(). Set by the single ToastProvider mounted in main.tsx.
let globalToast: ((message: string, opts?: ToastOpts) => void) | null = null;

export function showToast(message: string, opts?: ToastOpts) {
  globalToast?.(message, opts);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const remove = useCallback((id: number) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback(
    (message: string, opts?: ToastOpts) => {
      const id = nextId.current++;
      setToasts((t) => [...t, { id, message, action: opts?.action }]);
      const duration = opts?.duration ?? (opts?.action ? 6000 : 3500);
      setTimeout(() => remove(id), duration);
    },
    [remove],
  );

  useEffect(() => {
    globalToast = toast;
    return () => {
      globalToast = null;
    };
  }, [toast]);

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="fixed bottom-6 left-6 z-[60] flex flex-col gap-2 max-w-[calc(100vw-3rem)]">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="animate-fade-in-up flex items-center gap-4 rounded-xl bg-foreground text-white shadow-lg px-4 py-3 text-sm"
          >
            <span className="leading-snug">{t.message}</span>
            {t.action && (
              <button
                onClick={() => {
                  t.action!.onClick();
                  remove(t.id);
                }}
                className="shrink-0 font-semibold underline underline-offset-2 hover:opacity-80"
              >
                {t.action.label}
              </button>
            )}
            <button
              onClick={() => remove(t.id)}
              className="shrink-0 text-white/60 hover:text-white text-base leading-none"
              aria-label="Dismiss"
            >
              &times;
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
