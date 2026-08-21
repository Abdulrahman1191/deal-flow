import { useQuery } from "@tanstack/react-query";
import { fetchOpsStatus } from "../api/ops";
import type { OpsQueue, OpsTask } from "../api/ops";

/**
 * Pipeline health — the background half of the product, which until now was
 * only observable by SSHing to the prod host and running `redis-cli LLEN`.
 *
 * Deliberately reports "unknown" rather than guessing: a depth of null (Redis
 * unreachable) must never render as 0, because 0 reads as healthy and that is
 * how an outage stays invisible for a day.
 */

const REFRESH_MS = 15_000;

function relative(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function shortTask(task: string): string {
  // "app.tasks.sync_pitch_decks.sync_pitch_decks_task" -> "sync_pitch_decks_task"
  const parts = task.split(".");
  return parts[parts.length - 1] || task;
}

/**
 * Tones follow the house palette, which is navy-monochrome on purpose: the
 * `success`/`warning`/`error` tokens encode YES/MAYBE/REJECT prominence for a
 * LEAD, and `error` is the FAINTEST colour in the set (slate-500, deliberately
 * de-emphasised). Reusing them here would render an outage as the quietest
 * thing on the page. So prominence is inverted to match meaning instead:
 * trouble is solid navy, healthy is quiet grey. Differentiation by shade and
 * label, never hue -- same rule the rest of the app follows.
 */
function Pill({ tone, children }: { tone: "ok" | "warn" | "bad" | "muted"; children: React.ReactNode }) {
  const tones = {
    bad: "bg-foreground text-background",
    warn: "bg-secondary/15 text-secondary",
    ok: "bg-muted text-muted-foreground",
    muted: "bg-muted text-muted-foreground",
  } as const;
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

function QueueCard({ queue }: { queue: OpsQueue }) {
  const unknown = queue.depth === null;
  return (
    <div className="bg-card border border-border rounded-xl p-4 space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="font-medium text-foreground">{queue.name}</span>
        {queue.stalled ? (
          <Pill tone="bad">stalled</Pill>
        ) : queue.seconds_since_completion !== null ? (
          <Pill tone="ok">drained {relative(queue.seconds_since_completion)}</Pill>
        ) : (
          <Pill tone="muted">no completions yet</Pill>
        )}
      </div>

      <div>
        <div className={`text-3xl font-semibold ${unknown ? "text-muted-foreground" : "text-foreground"}`}>
          {unknown ? "?" : queue.depth}
        </div>
        <div className="text-xs text-muted-foreground">
          {unknown ? "broker unreachable" : "waiting"}
        </div>
      </div>

      {queue.consumers && queue.consumers.length > 0 && (
        <div
          className="text-xs text-muted-foreground truncate"
          title={
            "Workers that answered a control ping. A --pool=solo worker cannot " +
            "answer while it is running a task, so a worker missing from this " +
            "line is not evidence that it is gone."
          }
        >
          answered ping: {queue.consumers.join(", ")}
        </div>
      )}

      {queue.backlog.length > 0 && (
        <div className="space-y-1 pt-1 border-t border-border">
          {queue.backlog.map((b) => (
            <div key={b.task} className="flex justify-between gap-3 text-xs">
              <span className="text-muted-foreground truncate" title={b.task}>{shortTask(b.task)}</span>
              <span className="text-foreground tabular-nums">{b.count}</span>
            </div>
          ))}
          {queue.depth !== null && queue.sampled < queue.depth && (
            <p className="text-[11px] text-muted-foreground pt-1">
              breakdown from the first {queue.sampled} of {queue.depth}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function TaskRow({ task }: { task: OpsTask }) {
  const state = task.last_state;
  const tone = task.stale ? "bad" : state === "FAILURE" ? "bad" : state === "SKIPPED" ? "warn" : "ok";

  return (
    <tr className="border-t border-border align-top">
      <td className="px-4 py-3">
        <div className="text-foreground">{task.schedule_name}</div>
        <div className="text-xs text-muted-foreground">{shortTask(task.task)}</div>
      </td>
      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{task.queue}</td>
      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{task.schedule}</td>
      <td className="px-4 py-3 whitespace-nowrap">
        <Pill tone={tone}>
          {task.stale ? "late" : state ? state.toLowerCase() : "no runs yet"}
        </Pill>
      </td>
      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap tabular-nums">
        {relative(task.seconds_since)}
      </td>
      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap tabular-nums">
        {task.last_runtime_seconds === null ? "—" : `${task.last_runtime_seconds.toFixed(1)}s`}
      </td>
      <td className="px-4 py-3 text-xs text-foreground max-w-[280px] break-words">
        {task.last_error ?? ""}
      </td>
    </tr>
  );
}

export default function SystemPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["ops-status"],
    queryFn: fetchOpsStatus,
    refetchInterval: REFRESH_MS,
    staleTime: REFRESH_MS / 2,
  });

  if (isError) {
    return <p className="p-4 sm:p-6 text-sm text-foreground">Failed to load pipeline status.</p>;
  }

  const stale = (data?.tasks ?? []).filter((t) => t.stale);
  const starved = (data?.queues ?? []).filter((q) => q.stalled);

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-foreground">System</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Background pipeline health — queue depth, who is consuming each queue, and
          when each scheduled task last ran. Refreshes every {REFRESH_MS / 1000}s.
        </p>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          {(starved.length > 0 || stale.length > 0) && (
            <div className="rounded-xl border border-foreground/25 bg-accent p-4 space-y-1">
              {starved.map((q) => (
                <p key={q.name} className="text-sm text-foreground">
                  <span className="font-medium">{q.name}</span> has {q.depth} task
                  {q.depth === 1 ? "" : "s"} waiting and nothing has completed on it{" "}
                  {q.seconds_since_completion === null
                    ? "at all"
                    : `since ${relative(q.seconds_since_completion)}`}
                  .
                </p>
              ))}
              {stale.map((t) => (
                <p key={t.schedule_name} className="text-sm text-foreground">
                  <span className="font-medium">{t.schedule_name}</span> last ran{" "}
                  {relative(t.seconds_since)} — it is scheduled {t.schedule}.
                </p>
              ))}
            </div>
          )}

          {!data?.redis_reachable && (
            <div className="rounded-xl border border-border bg-card p-4">
              <p className="text-sm text-muted-foreground">
                The broker is unreachable, so queue depths are unknown rather than zero.
                Task history below may also be missing.
              </p>
            </div>
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            {(data?.queues ?? []).map((q) => (
              <QueueCard key={q.name} queue={q} />
            ))}
          </div>

          <p className="text-xs text-muted-foreground">
            {data?.unacked === null
              ? "In flight: unknown"
              : `In flight (delivered, not yet acknowledged): ${data?.unacked}`}
            {!data?.workers_reachable && " · workers did not answer a ping"}
          </p>

          <div className="bg-card border border-border rounded-xl overflow-x-auto">
            <table className="w-full min-w-[860px] text-sm">
              <thead className="bg-background text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="text-left px-4 py-3 font-medium">Scheduled task</th>
                  <th className="text-left px-4 py-3 font-medium">Queue</th>
                  <th className="text-left px-4 py-3 font-medium">Schedule</th>
                  <th className="text-left px-4 py-3 font-medium">Last result</th>
                  <th className="text-left px-4 py-3 font-medium">Last run</th>
                  <th className="text-left px-4 py-3 font-medium">Took</th>
                  <th className="text-left px-4 py-3 font-medium">Error</th>
                </tr>
              </thead>
              <tbody>
                {(data?.tasks ?? []).map((t) => (
                  <TaskRow key={t.schedule_name} task={t} />
                ))}
              </tbody>
            </table>
          </div>

          <p className="text-xs text-muted-foreground">
            Task history lives in the broker, which has no persistence — after a redeploy
            or a Redis restart every task reads as "no runs yet" until it next runs.
            {data?.observing_seconds != null && (
              <> Collecting for {relative(data.observing_seconds).replace(" ago", "")}; a
              task is only called late once that exceeds its own interval.</>
            )}
          </p>
        </>
      )}
    </div>
  );
}
