import { useEffect, useState } from "react";

interface UseApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function useApi<T>(path: string, intervalMs: number): UseApiState<T> {
  const [state, setState] = useState<UseApiState<T>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;

    async function fetchOnce() {
      try {
        const response = await fetch(path);
        if (!response.ok) {
          throw new Error(`${path} returned ${response.status}`);
        }
        const data = (await response.json()) as T;
        if (!cancelled) {
          setState({ data, error: null, loading: false });
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          setState((prev) => ({ ...prev, error: message, loading: false }));
        }
      }
    }

    fetchOnce();
    const id = setInterval(fetchOnce, intervalMs);

    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [path, intervalMs]);

  return state;
}
