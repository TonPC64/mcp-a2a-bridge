import { useEffect, useState } from "react";

interface EventSourceState<T> {
  data: T | null;
  error: string | null;
}

export function useEventSource<T>(path: string, event: string, initialData?: T): EventSourceState<T> {
  const [state, setState] = useState<EventSourceState<T>>({ data: initialData ?? null, error: null });

  useEffect(() => {
    if (initialData) return;
    const source = new EventSource(path);

    const onMessage = (message: MessageEvent<string>) => {
      try {
        setState({ data: JSON.parse(message.data) as T, error: null });
      } catch {
        setState((previous) => ({ ...previous, error: "Invalid live update payload." }));
      }
    };
    const onError = () => {
      setState((previous) => ({ ...previous, error: "Live update connection lost." }));
    };

    source.addEventListener(event, onMessage);
    source.onerror = onError;
    return () => source.close();
  }, [event, initialData, path]);

  return state;
}
