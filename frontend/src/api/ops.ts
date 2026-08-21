import client from "./client";

export interface OpsQueue {
  name: string;
  depth: number | null;
  backlog: { task: string; count: number }[];
  sampled: number;
  consumers: string[] | null;
  no_consumer: boolean;
}

export interface OpsTask {
  schedule_name: string;
  task: string;
  queue: string;
  schedule_seconds: number | null;
  schedule: string;
  last_state: string | null;
  last_at: string | null;
  last_runtime_seconds: number | null;
  last_error: string | null;
  seconds_since: number | null;
  stale: boolean | null;
}

export interface OpsStatus {
  generated_at: string;
  redis_reachable: boolean;
  workers_reachable: boolean;
  queues: OpsQueue[];
  unacked: number | null;
  tasks: OpsTask[];
}

export const fetchOpsStatus = () =>
  client.get<OpsStatus>("/ops/queues").then((r) => r.data);
