// Canonical entity types, hand-mirrored from server/adapters/base.py.

export type ServerStatus = 'running' | 'off' | 'rebuilding' | 'unknown';
export type TrafficCounting = 'outgoing_only' | 'ingress_and_egress';

export interface Money {
  amount: string; // decimal serialized as string
  currency: string;
  vat_inclusive: boolean | null;
}

export interface Allowance {
  included_bytes: number | null;
  used_bytes: number | null;
  counting: TrafficCounting | null;
  window: string | null;
  reset_at: string | null;
  overage_price: Money | null;
  projected_overage_cost: Money | null;
}

export interface Facet {
  label: string;
  value: string;
}

export interface Server {
  provider_id: string;
  name: string;
  adapter: string;
  account_id: number;
  status: ServerStatus;
  ipv4: string | null;
  region: string | null;
  server_type: string | null;
  created: string | null;
  labels: Record<string, string> | null;
  monthly_price: Money | null;
  allowance: Allowance | null;
  facets: Facet[];
  not_exposed: string[];
  // cache metadata (added by the API)
  last_seen_at?: string;
  traffic_history?: { day: string; bytes_used: number }[];
}

export interface FleetAccount {
  id: number;
  adapter: string;
  name: string;
  enabled: boolean;
  servers: Server[];
}

export interface SyncInfo {
  last_success_at: string | null;
  last_error: string | null;
  interval_minutes: number;
}

export interface FleetResponse {
  accounts: FleetAccount[];
  sync: Record<string, SyncInfo>;
  in_progress_actions: {
    id: number; account_id: number; provider_id: string; kind: string; created_at: string;
  }[];
}

export interface AccountRow {
  id: number;
  adapter: string;
  name: string;
  enabled: number;
  created_at: string;
  last4: string | null;
  scope: string | null;
  cred_created: string | null;
  last_used_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
}

export interface AdapterInfo {
  key: string;
  display_name: string;
  capabilities: string[];
  /** catalog providers only: 'live' | 'seeded' */
  source?: 'live' | 'seeded';
}

export interface UserRow {
  id: number;
  username: string;
  role: 'admin' | 'viewer';
  disabled: number;
  created_at: string;
}

export interface ActionRow {
  id: number;
  account_id: number;
  provider_id: string;
  kind: string;
  status: 'in_progress' | 'done' | 'failed';
  detail: string | null;
  created_at: string;
  completed_at: string | null;
  username: string | null;
}

export interface AuditRow {
  id: number;
  action: string;
  target: string;
  before_state: string | null;
  after_state: string | null;
  created_at: string;
  username: string | null;
}

export const CAPABILITY_LABELS: Record<string, string> = {
  power_on: 'Power on',
  power_off: 'Power off',
  reboot: 'Reboot',
  shutdown: 'Shutdown',
  rename: 'Rename',
  relabel: 'Edit labels',
  firewall: 'Firewall',
  rebuild: 'Rebuild',
  delete: 'Delete',
};

// ---- v2: catalog + orders ----

export interface IpOffer {
  kind: string;
  included: number;
  price: Money | null;
  limit: number | null;
  note: string | null;
}

export interface Plan {
  adapter: string;
  name: string;
  location: string;
  cpu_cores: number | null;
  cpu_arch: string | null;
  ram_gb: number | null;
  disk_gb: number | null;
  disk_type: string | null;
  price_monthly: Money | null;
  price_hourly: Money | null;
  included_traffic_bytes: number | null;
  counting: TrafficCounting | null;
  traffic_note?: string | null;
  overage_price: Money | null;
  extra_ip: IpOffer | null;
  billing_model: string;
  deprecated: boolean;
}

export interface OrderRow {
  id: number;
  mode: 'prototype' | 'real';
  status: 'draft' | 'confirmed' | 'executing' | 'provisioned' | 'failed' | 'cancelled';
  adapter: string;
  account_id: number | null;
  plan_name: string;
  location: string;
  options: string;      // JSON
  plan_snapshot: string; // JSON
  estimated_monthly: string; // JSON {amount, currency, partial}
  resulting_provider_id: string | null;
  requested_by: number;
  username: string | null;
  created_at: string;
  updated_at: string;
}
