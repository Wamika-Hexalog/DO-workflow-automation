import { useCallback, useEffect, useRef, useState } from "react";

const hasKeys = (obj) => Object.keys(obj).length > 0;

// Keeps edits locally and sends them in small batches once typing pauses.
// `edits` holds values the server hasn't confirmed yet.
export function useAutosave(save, delay = 450) {
  const [edits, setEdits] = useState({});
  const [status, setStatus] = useState({ state: "idle" });
  const queue = useRef({});
  const timer = useRef(null);
  const running = useRef(null);
  const saveRef = useRef(save);
  saveRef.current = save;

  const flush = useCallback(async () => {
    clearTimeout(timer.current);
    timer.current = null;
    while (running.current) await running.current;
    let sent = false;
    while (hasKeys(queue.current)) {
      const changes = queue.current;
      queue.current = {};
      setStatus({ state: "saving" });
      const attempt = saveRef.current(changes);
      running.current = attempt.catch(() => {});
      try {
        await attempt;
        sent = true;
        setEdits((prev) => {
          const next = { ...prev };
          for (const [key, value] of Object.entries(changes)) {
            if (Object.is(next[key], value)) delete next[key];
          }
          return next;
        });
      } catch (err) {
        queue.current = { ...changes, ...queue.current };
        setStatus({ state: "error", message: err.message });
        return false;
      } finally {
        running.current = null;
      }
    }
    if (sent) setStatus({ state: "saved" });
    return true;
  }, []);

  const setValue = useCallback(
    (key, value) => {
      queue.current = { ...queue.current, [key]: value };
      setEdits((prev) => ({ ...prev, [key]: value }));
      setStatus({ state: "pending" });
      clearTimeout(timer.current);
      timer.current = setTimeout(flush, delay);
    },
    [flush, delay],
  );

  // Anything still queued when the view closes is sent without waiting.
  useEffect(
    () => () => {
      clearTimeout(timer.current);
      if (hasKeys(queue.current)) saveRef.current(queue.current).catch(() => {});
    },
    [],
  );

  return { edits, setValue, flush, status };
}
