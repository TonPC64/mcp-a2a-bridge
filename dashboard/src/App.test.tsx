import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

class MockEventSource {
  static instances: MockEventSource[] = [];
  private listeners = new Map<string, (event: MessageEvent<string>) => void>();
  close = vi.fn();
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(event: string, listener: (event: MessageEvent<string>) => void) {
    this.listeners.set(event, listener);
  }

  emit(event: string, data: string) {
    this.listeners.get(event)?.(new MessageEvent(event, { data }));
  }
}

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    MockEventSource.instances = [];
  });

  it("renders full snapshots received from both event streams", async () => {
    vi.stubGlobal("EventSource", MockEventSource);

    render(<App />);
    await act(async () => {
      MockEventSource.instances[0].emit(
        "agents",
        JSON.stringify({ agents: [{ name: "planner", reachable: true, skills: [] }] }),
      );
      MockEventSource.instances[1].emit(
        "tasks",
        JSON.stringify({ tasks: [{ id: "t1", agent: "planner", kind: "send_message", state: "completed", text: "done", created_at: 0, updated_at: 0 }] }),
      );
    });

    expect(screen.getByText("A2A Bridge Dashboard")).toBeInTheDocument();
    expect(screen.getAllByText("planner")).toHaveLength(2);
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("ignores malformed events, reports errors, and closes streams on unmount", async () => {
    vi.stubGlobal("EventSource", MockEventSource);
    const { unmount } = render(<App />);

    await act(async () => {
      MockEventSource.instances[0].emit("agents", "not json");
      MockEventSource.instances[1].onerror?.();
    });

    expect(screen.getByText("Invalid live update payload.")).toBeInTheDocument();
    expect(screen.getByText("Live update connection lost.")).toBeInTheDocument();
    unmount();
    expect(MockEventSource.instances.map((source) => source.close)).toEqual([
      expect.any(Function),
      expect.any(Function),
    ]);
    expect(MockEventSource.instances[0].close).toHaveBeenCalledOnce();
    expect(MockEventSource.instances[1].close).toHaveBeenCalledOnce();
  });
});
