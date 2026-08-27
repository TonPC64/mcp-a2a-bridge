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

  it("updates each agent's task count when a task snapshot changes", async () => {
    vi.stubGlobal("EventSource", MockEventSource);
    render(<App />);

    await act(async () => {
      MockEventSource.instances[0].emit("agents", JSON.stringify({ agents: [
        { name: "planner", reachable: true, skills: [] },
        { name: "worker", reachable: true, skills: [] },
      ] }));
      MockEventSource.instances[1].emit("tasks", JSON.stringify({ tasks: [
        { id: "t1", agent: "planner", source: "planner", destination: "worker", kind: "send_message", state: "completed", text: "done", created_at: 0, updated_at: 0 },
      ] }));
    });
    expect(screen.getByRole("article", { name: "worker" })).toHaveTextContent("1 task");
    expect(screen.getByRole("article", { name: "planner" })).toHaveTextContent("0 tasks");

    await act(async () => {
      MockEventSource.instances[1].emit("tasks", JSON.stringify({ tasks: [
        { id: "t1", agent: "planner", kind: "send_message", state: "completed", text: "done", created_at: 0, updated_at: 0 },
        { id: "t2", agent: "planner", source: "worker", kind: "send_message", state: "completed", text: "done", created_at: 0, updated_at: 0 },
      ] }));
    });
    expect(screen.getByRole("article", { name: "worker" })).toHaveTextContent("1 task");
    expect(screen.getByRole("article", { name: "planner" })).toHaveTextContent("1 task");
  });

  it("labels live data regions and clears their loading state after a snapshot", async () => {
    vi.stubGlobal("EventSource", MockEventSource);
    render(<App />);

    expect(screen.getByRole("status", { name: "Loading agents" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading task activity" })).toBeInTheDocument();

    await act(async () => {
      MockEventSource.instances[0].emit("agents", JSON.stringify({ agents: [] }));
      MockEventSource.instances[1].emit("tasks", JSON.stringify({ tasks: [] }));
    });

    expect(screen.getByRole("region", { name: "Agents" })).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("region", { name: "Task activity" })).toHaveAttribute("aria-busy", "false");
  });

  it("reports a disconnected stream instead of loading indefinitely", async () => {
    vi.stubGlobal("EventSource", MockEventSource);
    render(<App />);

    await act(async () => {
      MockEventSource.instances[0].onerror?.();
    });

    expect(screen.queryByRole("status", { name: "Loading agents" })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Live update connection lost.");
    expect(screen.getByText("Reconnecting")).toBeInTheDocument();
  });

  it("shows a jump-to-top control after scrolling and returns to the top", async () => {
    vi.stubGlobal("EventSource", MockEventSource);
    const scrollTo = vi.fn();
    vi.stubGlobal("scrollTo", scrollTo);
    Object.defineProperty(window, "scrollY", { configurable: true, value: 0, writable: true });
    render(<App />);

    expect(screen.queryByRole("button", { name: "Jump to top" })).not.toBeInTheDocument();

    await act(async () => {
      window.scrollY = 300;
      window.dispatchEvent(new Event("scroll"));
    });
    expect(screen.queryByRole("button", { name: "Jump to top" })).not.toBeInTheDocument();

    await act(async () => {
      window.scrollY = 400;
      window.dispatchEvent(new Event("scroll"));
    });

    screen.getByRole("button", { name: "Jump to top" }).click();
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "smooth" });
  });

  it("respects reduced-motion preferences when returning to the top", async () => {
    vi.stubGlobal("EventSource", MockEventSource);
    const scrollTo = vi.fn();
    vi.stubGlobal("scrollTo", scrollTo);
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    Object.defineProperty(window, "scrollY", { configurable: true, value: 400, writable: true });
    render(<App />);

    await act(async () => window.dispatchEvent(new Event("scroll")));
    screen.getByRole("button", { name: "Jump to top" }).click();

    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });
  });

  it("registers one jump-to-top scroll listener and removes it on unmount", () => {
    vi.stubGlobal("EventSource", MockEventSource);
    const addEventListener = vi.spyOn(window, "addEventListener");
    const removeEventListener = vi.spyOn(window, "removeEventListener");
    const { unmount } = render(<App />);

    const jumpListeners = () => addEventListener.mock.calls.filter(([event, , options]) => event === "scroll" && (options as AddEventListenerOptions).passive);
    expect(jumpListeners()).toHaveLength(1);
    window.dispatchEvent(new Event("scroll"));
    expect(jumpListeners()).toHaveLength(1);
    unmount();
    expect(removeEventListener).toHaveBeenCalledWith("scroll", jumpListeners()[0][1]);
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
