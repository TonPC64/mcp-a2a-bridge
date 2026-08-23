import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the dashboard heading and both sections", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ agents: [], tasks: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await act(async () => {});

    expect(screen.getByText("A2A Bridge Dashboard")).toBeInTheDocument();
    expect(screen.getByText("Agents")).toBeInTheDocument();
    expect(screen.getByText("Task activity")).toBeInTheDocument();
    expect(screen.getByText("No agents configured.")).toBeInTheDocument();
    expect(screen.getByText("No task activity yet.")).toBeInTheDocument();
  });
});
