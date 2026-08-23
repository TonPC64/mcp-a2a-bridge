import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TaskList, type TaskActivity } from "./TaskList";

describe("TaskList", () => {
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
    expect(screen.getByText("planner")).toBeInTheDocument();
    expect(screen.getByText("send_message")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
  });
});
