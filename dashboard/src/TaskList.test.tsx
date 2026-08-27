import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { formatRelativeTime, TaskList, truncateTaskText, type TaskActivity } from "./TaskList";

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

  it("truncates task text on a character boundary for the compact preview", () => {
    expect(truncateTaskText("123456", 5)).toBe("12345…");
    expect(truncateTaskText("short", 5)).toBe("short");
  });

  it("shows a message when there is no activity", () => {
    render(<TaskList tasks={[]} />);
    expect(screen.getByText("No task activity yet. New work will appear here automatically.")).toBeInTheDocument();
  });

  it("shows ten tasks initially and reveals the next page when scrolled to the bottom", () => {
    const tasks = Array.from({ length: 11 }, (_, index) => ({
      id: `task-${index}`,
      agent: "planner",
      kind: "send_message",
      state: "completed",
      text: `task ${index}`,
      created_at: index,
      updated_at: index,
    }));
    const innerHeight = Object.getOwnPropertyDescriptor(window, "innerHeight");
    const scrollY = Object.getOwnPropertyDescriptor(window, "scrollY");
    const scrollHeight = Object.getOwnPropertyDescriptor(document.documentElement, "scrollHeight");
    Object.defineProperties(window, { innerHeight: { configurable: true, value: 100 }, scrollY: { configurable: true, value: 900 } });
    Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, value: 1000 });

    render(<TaskList tasks={tasks} />);

    expect(screen.getByText("task 9")).toBeInTheDocument();
    expect(screen.queryByText("task 10")).not.toBeInTheDocument();
    fireEvent.scroll(window);
    expect(screen.getByText("task 10")).toBeInTheDocument();

    if (innerHeight) Object.defineProperty(window, "innerHeight", innerHeight);
    if (scrollY) Object.defineProperty(window, "scrollY", scrollY);
    if (scrollHeight) Object.defineProperty(document.documentElement, "scrollHeight", scrollHeight);
  });

  it("renders each task as a log cell and ordered metadata cell", () => {
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
    expect(screen.getByText("send_message")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Recent A2A task activity, with task logs and metadata" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Task log" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Metadata" })).toBeInTheDocument();
    expect(screen.getByLabelText("Full task ID: 12345678-abcd")).toHaveAttribute("title", "12345678-abcd");
    const row = screen.getByText("12345678").closest("tr")!;
    expect(row).toHaveClass("task-row");
    expect(Array.from(row.children)).toHaveLength(2);
    expect(row.children[0]).toHaveClass("task-log");
    expect(row.children[1]).toHaveClass("task-meta");
    expect(Array.from(row.children[1].children).map((item) => item.className)).toEqual([
      "task-status",
      "task-route",
      "task-activity",
      "task-updated",
    ]);
    expect(row.querySelector("time")).toHaveAttribute("dateTime", "2023-11-14T22:13:25.000Z");
  });

  it("renders source and destination as labelled participant tags", () => {
    render(
      <TaskList
        tasks={[{
          id: "task-1", agent: "codex-co-developer", source: "copilot",
          destination: "codex-co-developer", kind: "a2a_receive", state: "working",
          text: "received", created_at: 1, updated_at: 1,
        }]}
      />,
    );

    const route = screen.getByLabelText("Route from copilot to codex-co-developer");
    expect(route).toHaveTextContent("copilot");
    expect(route).toHaveTextContent("codex-co-developer");
    expect(screen.getByText("copilot")).toHaveClass("participant-source");
    expect(screen.getByText("codex-co-developer")).toHaveClass("participant-destination");
    expect(screen.getByText("to")).toHaveClass("sr-only");
  });

  it("groups a route's source and destination in a compact inline route", () => {
    render(
      <TaskList
        tasks={[{
          id: "task-stack", agent: "codex", source: "copilot", destination: "codex",
          kind: "a2a_receive", state: "working", text: "received", created_at: 1, updated_at: 1,
        }]}
      />,
    );

    const route = screen.getByLabelText("Route from copilot to codex");
    expect(route.querySelector(".participant-route")).toContainElement(screen.getByText("copilot"));
    expect(route.querySelector(".participant-route")).toContainElement(screen.getByText("→"));
    expect(route.querySelector(".participant-route")).toContainElement(screen.getByText("codex"));
  });

  it("renders one participant tag when the route starts and ends at the same agent", () => {
    render(
      <TaskList
        tasks={[{
          id: "task-2", agent: "planner", source: "planner", destination: "planner",
          kind: "send_message", state: "completed", text: "complete", created_at: 1, updated_at: 1,
        }]}
      />,
    );

    const route = screen.getByLabelText("Participant: planner");
    expect(route).toHaveTextContent("planner");
    expect(route.querySelectorAll(".participant-tag")).toHaveLength(1);
    expect(screen.queryByText("to")).not.toBeInTheDocument();
  });

  it("shows every task field in the detail dialog and closes on an outside click", () => {
    const text = "A long task detail ".repeat(20);
    render(<TaskList tasks={[{
      id: "task-long-1234567890", agent: "planner", source: "copilot", destination: "worker",
      kind: "send_message", state: "completed", text, created_at: 1, updated_at: 2,
    }]} />);

    expect(screen.getByText(`${text.trim().slice(0, 140)}…`)).toBeInTheDocument();
    const button = screen.getByLabelText("View full task details for task-long-1234567890");
    expect(screen.queryByRole("dialog", { name: "Task task-long-1234567890 details" })).not.toBeInTheDocument();

    fireEvent.click(button);
    const dialog = screen.getByRole("dialog", { name: "Task task-long-1234567890 details" });
    expect(dialog).toHaveAttribute("open");
    expect(dialog).toHaveTextContent("Task ID");
    expect(dialog).toHaveTextContent("task-long-1234567890");
    expect(dialog).toHaveTextContent("Log message");
    expect(dialog).toHaveTextContent(text.trim());
    expect(dialog).toHaveTextContent("Status");
    expect(dialog).toHaveTextContent("completed");
    expect(dialog).toHaveTextContent("Activity kind");
    expect(dialog).toHaveTextContent("send_message");
    expect(dialog).toHaveTextContent("Source participant");
    expect(dialog).toHaveTextContent("copilot");
    expect(dialog).toHaveTextContent("Destination participant");
    expect(dialog).toHaveTextContent("worker");
    expect(dialog).toHaveTextContent("Route");
    expect(dialog).toHaveTextContent("Created");
    expect(dialog).toHaveTextContent("Updated");
    expect(dialog.querySelectorAll("dt")).toHaveLength(10);
    expect(dialog.querySelectorAll("dd")).toHaveLength(10);
    expect(dialog.querySelectorAll("time")).toHaveLength(2);
    fireEvent.click(dialog);
    expect(screen.queryByRole("dialog", { name: "Task task-long-1234567890 details" })).not.toBeInTheDocument();
  });

  it("renders the detail route as labelled, compact participants", () => {
    const text = "A long task detail ".repeat(20);
    render(<TaskList tasks={[{
      id: "task-route", agent: "planner", source: "a-very-long-source-participant-name",
      destination: "a-very-long-destination-participant-name", kind: "send_message", state: "completed",
      text, created_at: 1, updated_at: 1,
    }]} />);

    fireEvent.click(screen.getByLabelText("View full task details for task-route"));
    const dialog = screen.getByRole("dialog", { name: "Task task-route details" });
    const route = within(dialog).getByLabelText("Route from a-very-long-source-participant-name to a-very-long-destination-participant-name");
    expect(route).toHaveClass("task-detail-route");
    expect(route.querySelector(".task-detail-route-tag.participant-source")).toHaveTextContent("a-very-long-source-participant-name");
    expect(route.querySelector(".task-detail-route-arrow")).toHaveTextContent("→");
    expect(route.querySelector(".task-detail-route-tag.participant-destination")).toHaveTextContent("a-very-long-destination-participant-name");
  });

  it("uses the handling agent when optional route participants are absent", () => {
    const text = "A long task detail ".repeat(20);
    render(<TaskList tasks={[{
      id: "task-fallback", agent: "planner", kind: "send_message", state: "completed",
      text, created_at: 1, updated_at: 1,
    }]} />);

    fireEvent.click(screen.getByLabelText("View full task details for task-fallback"));
    const dialog = screen.getByRole("dialog", { name: "Task task-fallback details" });
    expect(dialog).toHaveTextContent("Handling agent");
    expect(dialog).toHaveTextContent("planner");
    expect(dialog).toHaveTextContent("Source participant");
    expect(dialog).toHaveTextContent("Destination participant");
    expect(screen.getByText("Source participant").nextElementSibling).toHaveTextContent("planner");
    expect(screen.getByText("Destination participant").nextElementSibling).toHaveTextContent("planner");
  });
});
