import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { formatRelativeTime, TaskList, type TaskActivity } from "./TaskList";

describe("TaskList", () => {
  it("formats recent updates as relative time", () => {
    expect(formatRelativeTime(1000, 1000)).toBe("just now");
    expect(formatRelativeTime(1000 - 5 * 60, 1000)).toBe("5 minutes ago");
    expect(formatRelativeTime(1000 - 2 * 3600, 1000)).toBe("2 hours ago");
  });

  it("formats older updates as a calendar date", () => {
    expect(formatRelativeTime(Date.UTC(2026, 7, 20) / 1000, Date.UTC(2026, 7, 25) / 1000)).toBe(
      "Aug 20, 2026",
    );
  });

  it("shows a message when there is no activity", () => {
    render(<TaskList tasks={[]} />);
    expect(screen.getByText("No task activity yet.")).toBeInTheDocument();
  });

  it("renders a task row with its state and text", () => {
    const tasks: TaskActivity[] = [
      {
        id: "12345678-abcd",
        agent: "planner",
        kind: "send_message",
        state: "completed",
        text: "done",
        created_at: 1700000000,
        updated_at: 1700000005,
      },
    ];
    render(<TaskList tasks={tasks} />);

    expect(screen.getByText("12345678")).toBeInTheDocument();
    expect(screen.getAllByText("planner")).toHaveLength(2);
    expect(screen.getByText("send_message")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
  });

  it("renders the source and handling agents when supplied", () => {
    render(
      <TaskList
        tasks={[{
          id: "task-1", agent: "codex-co-developer", source: "copilot",
          destination: "codex-co-developer", kind: "a2a_receive", state: "working",
          text: "received", created_at: 1, updated_at: 1,
        }]}
      />,
    );

    expect(screen.getByText("copilot")).toBeInTheDocument();
    expect(screen.getByText("codex-co-developer")).toBeInTheDocument();
  });
});
