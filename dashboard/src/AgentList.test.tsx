import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentList, type Agent } from "./AgentList";

describe("AgentList", () => {
  it("shows a message when there are no agents", () => {
    render(<AgentList agents={[]} />);
    expect(screen.getByText("No agents configured.")).toBeInTheDocument();
  });

  it("renders reachable agents with their skills", () => {
    const agents: Agent[] = [
      {
        name: "planner",
        configured_url: "http://localhost:9001",
        reachable: true,
        skills: [{ id: "plan", name: "Planning", description: "d", tags: [], examples: [] }],
      },
    ];
    render(<AgentList agents={agents} />);

    expect(screen.getByText("planner")).toBeInTheDocument();
    expect(screen.getByText("reachable")).toBeInTheDocument();
    expect(screen.getByText("Planning")).toBeInTheDocument();
  });

  it("renders unreachable agents with their error as a tooltip", () => {
    const agents: Agent[] = [
      {
        name: "broken",
        configured_url: "http://localhost:9002",
        reachable: false,
        error: "connection refused",
      },
    ];
    render(<AgentList agents={agents} />);

    const badge = screen.getByText("unreachable");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute("title", "connection refused");
  });
});
