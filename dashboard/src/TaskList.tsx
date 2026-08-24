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

export function TaskList({ tasks }: { tasks: TaskActivity[] }) {
  if (tasks.length === 0) {
    return <p>No task activity yet.</p>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Task</th>
          <th>Source</th>
          <th>Handling agent</th>
          <th>Kind</th>
          <th>State</th>
          <th>Last update</th>
          <th>Text</th>
        </tr>
      </thead>
      <tbody>
        {tasks.map((task) => (
          <tr key={task.id}>
            <td>{task.id.slice(0, 8)}</td>
            <td>{task.source ?? task.agent}</td>
            <td>{task.destination ?? task.agent}</td>
            <td>{task.kind}</td>
            <td>
              <span className={`badge badge-${task.state}`}>{task.state}</span>
            </td>
            <td title={formatExactTime(task.updated_at)}>
              {formatRelativeTime(task.updated_at)}
            </td>
            <td>{task.text}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
