import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AgentList, type Agent } from "./AgentList";
import type { TaskActivity } from "./TaskList";

describe("AgentList", () => {
  it("shows a message when there are no agents", () => {
    render(<AgentList agents={[]} />);
    expect(screen.getByText("No agents configured yet.")).toBeInTheDocument();
  });

  it("counts each task once for its destination, otherwise source, and shows zero", () => {
    const agents: Agent[] = [
      { name: "planner", configured_url: "", reachable: true },
      { name: "worker", configured_url: "", reachable: true },
      { name: "idle", configured_url: "", reachable: true },
    ];
    const tasks: TaskActivity[] = [
      { id: "both", agent: "planner", source: "planner", destination: "worker", kind: "send", state: "completed", text: "", created_at: 0, updated_at: 0 },
      { id: "agent", agent: "planner", kind: "send", state: "completed", text: "", created_at: 0, updated_at: 0 },
      { id: "source", agent: "planner", source: "worker", kind: "send", state: "completed", text: "", created_at: 0, updated_at: 0 },
    ];

    render(<AgentList agents={agents} tasks={tasks} />);

    expect(screen.getByRole("article", { name: "planner" })).toHaveTextContent("1 task");
    expect(screen.getByRole("article", { name: "worker" })).toHaveTextContent("2 tasks");
    expect(screen.getByRole("article", { name: "idle" })).toHaveTextContent("0 tasks");
  });

  it("keeps agent details out of the compact card body without a view-more control", () => {
    const agents: Agent[] = [
      {
        name: "planner",
        configured_url: "http://localhost:9001",
        reachable: true,
        description: "Plans multi-step work",
        skills: [{ id: "plan", name: "Planning", description: "d", tags: [], examples: [] }],
      },
    ];
    render(<AgentList agents={agents} />);

    const card = screen.getByRole("article", { name: "planner" });
    expect(card).toHaveClass("agent-card");
    expect(card.querySelector("h3")).toHaveTextContent("planner");
    const status = screen.getByRole("status", { name: "planner is reachable" });
    expect(status).toHaveClass("agent-status-dot", "agent-status-reachable");
    expect(status).toBeEmptyDOMElement();
    expect(status).toHaveAttribute("title", "planner is reachable");
    expect(card.parentElement).toHaveClass("agent-grid", "agent-grid-compact");
    expect(card.querySelector("dialog")).toBeNull();
    expect(card).not.toHaveTextContent("view more");
    expect(screen.queryByRole("button", { name: "View details for planner" })).not.toBeInTheDocument();
    expect(card).not.toHaveTextContent("Plans multi-step work");
    expect(card).not.toHaveTextContent("http://localhost:9001");
    expect(card).not.toHaveTextContent("Planning");
  });

  it("keeps unreachable error details in the dialog", () => {
    const agents: Agent[] = [
      {
        name: "broken",
        configured_url: "http://localhost:9002",
        reachable: false,
        error: "connection refused",
      },
    ];
    render(<AgentList agents={agents} />);

    const status = screen.getByRole("status", { name: "broken is unreachable" });
    expect(status).toHaveClass("agent-status-dot", "agent-status-unreachable");
    expect(status).toBeEmptyDOMElement();
    expect(status).toHaveAttribute("title", "broken is unreachable");
    const card = screen.getByRole("article", { name: "broken" });
    expect(card).toHaveClass("agent-card-unreachable");
    expect(card).not.toHaveTextContent("connection refused");

    fireEvent.click(screen.getByRole("article", { name: "broken" }));
    expect(screen.getByRole("dialog", { name: "broken details" })).toHaveTextContent("Status unreachable");
    expect(screen.getByRole("dialog", { name: "broken details" })).toHaveTextContent("Connection issue: connection refused");
  });

  it("opens metadata and skills when its card is clicked", () => {
    const agents: Agent[] = [{
      name: "planner",
      configured_url: "http://localhost:9001",
      reachable: true,
      skills: Array.from({ length: 7 }, (_, index) => ({
        id: `skill-${index}`,
        name: `Skill ${index + 1}`,
        description: "d",
        tags: [],
        examples: [],
      })),
    }];
    render(<AgentList agents={agents} />);

    expect(screen.queryByRole("dialog", { name: "planner details" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("article", { name: "planner" }));

    const dialog = screen.getByRole("dialog", { name: "planner details" });
    expect(dialog).toHaveAttribute("open");
    expect(dialog).toHaveTextContent("http://localhost:9001");
    expect(dialog).toHaveTextContent("Skill 7");
    fireEvent.click(screen.getByRole("button", { name: "Close planner details" }));
    expect(screen.queryByRole("dialog", { name: "planner details" })).not.toBeInTheDocument();
  });
});
