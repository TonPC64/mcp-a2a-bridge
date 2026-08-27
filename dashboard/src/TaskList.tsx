import { useEffect, useState } from "react";
import { DetailDialog } from "./DetailDialog";

export interface TaskActivity {
  id: string;
  agent: string;
  kind: string;
  state: string;
  text: string;
  created_at: number;
  updated_at: number;
  source?: string;
  destination?: string;
}

export function formatRelativeTime(epochSeconds: number, nowSeconds = Date.now() / 1000): string {
  const secondsAgo = Math.max(0, nowSeconds - epochSeconds);
  if (secondsAgo < 60) return "just now";
  if (secondsAgo < 3600) return `${Math.floor(secondsAgo / 60)} minutes ago`;
  if (secondsAgo < 86400) return `${Math.floor(secondsAgo / 3600)} hours ago`;

  const date = new Date(epochSeconds * 1000);
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatExactTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString();
}

function taskValue(value: string | undefined, fallback: string): string {
  return value?.trim() || fallback;
}

function TaskDetail({ task }: { task: TaskActivity }) {
  const agent = taskValue(task.agent, "Unavailable");
  const source = taskValue(task.source, agent);
  const destination = taskValue(task.destination, agent);
  const created = new Date(task.created_at * 1000);
  const updated = new Date(task.updated_at * 1000);

  return <div className="task-detail">
    <section className="task-detail-section" aria-label="Task record"><dl className="task-detail-list">
      <div className="task-detail-wide"><dt>Task ID</dt><dd><code>{taskValue(task.id, "Unavailable")}</code></dd></div>
      <div className="task-detail-wide"><dt>Log message</dt><dd className="task-detail-message">{taskValue(task.text, "No log message was recorded.")}</dd></div>
      <div><dt>Status</dt><dd><span className={`badge badge-${taskValue(task.state, "unknown")}`}>{taskValue(task.state, "unknown")}</span></dd></div>
      <div><dt>Activity kind</dt><dd><span className="kind-label">{taskValue(task.kind, "Unavailable")}</span></dd></div>
      <div className="task-detail-wide"><dt>Handling agent</dt><dd>{agent}</dd></div>
    </dl></section>
    <section className="task-detail-section" aria-label="Participant route"><dl className="task-detail-list">
      <div><dt>Source participant</dt><dd>{source}</dd></div>
      <div><dt>Destination participant</dt><dd>{destination}</dd></div>
      <div className="task-detail-wide"><dt>Route</dt><dd className="task-detail-route" aria-label={`Route from ${source} to ${destination}`}><span className="participant-tag task-detail-route-tag participant-source">{source}</span><span className="task-detail-route-arrow" aria-hidden="true">→</span><span className="participant-tag task-detail-route-tag participant-destination">{destination}</span></dd></div>
    </dl></section>
    <section className="task-detail-section" aria-label="Task timing"><dl className="task-detail-list">
      <div><dt>Created</dt><dd><time dateTime={created.toISOString()}>{formatExactTime(task.created_at)}</time></dd></div>
      <div><dt>Updated</dt><dd><time dateTime={updated.toISOString()}>{formatExactTime(task.updated_at)}</time></dd></div>
    </dl></section>
  </div>;
}

export function truncateTaskText(text: string, maxLength = 140): string {
  const compactText = text.trim().replace(/\s+/g, " ");
  return Array.from(compactText).length > maxLength ? `${Array.from(compactText).slice(0, maxLength).join("")}…` : compactText;
}

export function TaskList({ tasks }: { tasks: TaskActivity[] }) {
  const [selectedTask, setSelectedTask] = useState<TaskActivity | null>(null);
  const [visibleCount, setVisibleCount] = useState(10);

  useEffect(() => {
    setVisibleCount((count) => Math.min(Math.max(10, count), tasks.length));
  }, [tasks.length]);

  useEffect(() => {
    const loadMore = () => {
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight) {
        setVisibleCount((count) => Math.min(count + 10, tasks.length));
      }
    };

    window.addEventListener("scroll", loadMore);
    return () => window.removeEventListener("scroll", loadMore);
  }, [tasks.length]);

  if (tasks.length === 0) {
    return <p className="empty-state" role="status">No task activity yet. New work will appear here automatically.</p>;
  }

  return (
    <div className="table-scroll">
    <table className="data-table task-table">
      <caption className="sr-only">Recent A2A task activity, with task logs and metadata</caption>
      <thead>
        <tr>
          <th scope="col">Task log</th>
          <th scope="col">Metadata</th>
        </tr>
      </thead>
      <tbody>
        {tasks.slice(0, visibleCount).map((task) => {
          const source = task.source ?? task.agent;
          const destination = task.destination ?? task.agent;
          const detail = task.text.trim();
          const preview = truncateTaskText(detail);
          const hasMoreDetail = preview !== detail;

          return <tr className="task-row" key={task.id}>
            <th className="task-log" scope="row">
              <code title={task.id} aria-label={`Full task ID: ${task.id}`}>{task.id.slice(0, 8)}</code>
              {detail ? <><span className="task-text">{preview}</span>{hasMoreDetail && <button type="button" className="detail-trigger" aria-label={`View full task details for ${task.id}`} onClick={() => setSelectedTask(task)}>… view more</button>}</> : <span className="muted">No details yet</span>}
            </th>
            <td className="task-meta" data-label="Metadata">
              <div className="task-status"><span className={`badge badge-${task.state}`}>{task.state}</span></div>
              <div className="task-route" aria-label={source === destination ? `Participant: ${source}` : `Route from ${source} to ${destination}`}>
                <span className="participant-route">
                  <span className="participant-tag participant-source">{source}</span>
                  {source !== destination && <span className="participant-route-arrow">
                    <span className="participant-arrow" aria-hidden="true">→</span>
                    <span className="sr-only">to</span>
                  </span>}
                  {source !== destination && <span className="participant-tag participant-destination">{destination}</span>}
                </span>
              </div>
              <div className="task-activity"><span className="kind-label">{task.kind}</span></div>
              <div className="task-updated">
                <time dateTime={new Date(task.updated_at * 1000).toISOString()} title={formatExactTime(task.updated_at)}>
                  {formatRelativeTime(task.updated_at)}
                </time>
              </div>
            </td>
          </tr>;
        })}
      </tbody>
    </table>
    {selectedTask && <DetailDialog title={`Task ${selectedTask.id} details`} closeLabel="Close task details" onClose={() => setSelectedTask(null)}><TaskDetail task={selectedTask} /></DetailDialog>}
    </div>
  );
}
