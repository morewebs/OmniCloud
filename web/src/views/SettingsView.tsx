import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import FormControlLabel from '@mui/material/FormControlLabel';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutlined';
import EditOutlinedIcon from '@mui/icons-material/EditOutlined';
import SendIcon from '@mui/icons-material/Send';
import { api, del, fmtRelative, patch, post, put, safeHref } from '../api';
import type { AccountRow, AdapterInfo, HookDeliveryRow, HookRow } from '../types';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Toast } from '../components/Toast';
import type { ToastMsg } from '../components/Toast';
import { usePageTitle } from '../usePageTitle';

/** Sync intervals: visible, editable, live (no restart). */
export function SettingsView() {
  usePageTitle('Settings');
  const qc = useQueryClient();
  const settings = useQuery<Record<string, string>>({ queryKey: ['settings'],
    queryFn: () => api<Record<string, string>>('/api/settings') });
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  const [form, setForm] = useState<Record<string, string>>({});
  // what the form looked like at the last (re)load - the dirty check's baseline
  const lastSynced = useRef<Record<string, string> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastMsg>(null);

  useEffect(() => {
    // sync only when the form is not dirty: a background refetch (window-focus
    // is on) must never clobber in-progress edits. Missing interval keys are
    // populated with the real server-side default so the shown value is the
    // effective one and validation + save cover it.
    setForm(f => {
      if (lastSynced.current !== null
          && JSON.stringify(f) !== JSON.stringify(lastSynced.current)) {
        return f; // dirty - keep the operator's edits
      }
      const withDefaults = { ...settings.data! };
      for (const a of accounts.data ?? []) {
        const key = `sync_interval:${a.id}`;
        if (withDefaults[key] === undefined) {
          const def = settings.data!['sync_default_interval'];
          if (def !== undefined) withDefaults[key] = def;
        }
        // the IP acquisition cap's effective value (server default: 10)
        const cap = `ip_change_daily_cap:${a.id}`;
        if (withDefaults[cap] === undefined)
          withDefaults[cap] = settings.data!['ip_change_daily_cap'] ?? '10';
      }
      lastSynced.current = withDefaults;
      return withDefaults;
    });
  }, [settings.data, accounts.data]);

  if (settings.isPending || accounts.isPending) {
    return <Stack spacing={2}>
      <Skeleton variant="rounded" height={32} sx={{ maxWidth: 160 }} />
      {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} variant="rounded" height={56} />)}
    </Stack>;
  }
  if (settings.isError) return (
    <Stack spacing={2}>
      <PageHeader title="Settings" />
      <Alert severity="error"
             action={<Button onClick={() => settings.refetch()}>Retry</Button>}>
        {(settings.error as Error).message}
      </Alert>
    </Stack>
  );
  if (accounts.isError) return (
    <Stack spacing={2}>
      <PageHeader title="Settings" />
      <Alert severity="error"
             action={<Button onClick={() => accounts.refetch()}>Retry</Button>}>
        Could not load accounts: {(accounts.error as Error).message}
      </Alert>
    </Stack>
  );

  const invalid = Object.entries(form).some(([k, v]) =>
    (k.startsWith('sync_interval:') && (!Number.isInteger(Number(v)) || Number(v) < 1))
    || (k.startsWith('ip_change_daily_cap') && (v === '' || !Number.isInteger(Number(v)) || Number(v) < 0))
    || (k.startsWith('billing_low_balance:') && v !== '' && (Number.isNaN(Number(v)) || Number(v) < 0)));

  const save = async () => {
    setBusy(true); setError(null);
    try {
      await put('/api/settings', form);
      setToast({ message: 'Settings saved — apply on the next sync cycle', severity: 'success' });
      qc.invalidateQueries({ queryKey: ['fleet'] });
      qc.invalidateQueries({ queryKey: ['accounts'] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const dirty = JSON.stringify(form) !== JSON.stringify(lastSynced.current);

  return (
    <Stack spacing={3}>
      <PageHeader title="Settings" subtitle="Sync intervals, visible and editable — no restart needed." />

      <Stack spacing={2} sx={{ maxWidth: 420 }}>
        <Typography variant="subtitle1">Sync intervals (minutes)</Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          How often each provider account is refreshed. Changes apply on the
          next cycle without a restart.
        </Typography>
        {accounts.data!.length === 0 && (
          <Alert severity="info">No provider accounts yet — add one under Credentials.</Alert>
        )}
        {accounts.data!.map(a => {
          const key = `sync_interval:${a.id}`;
          const val = form[key] ?? '';
          const bad = val !== '' && (!Number.isInteger(Number(val)) || Number(val) < 1);
          return (
            <TextField
              key={a.id}
              className="num"
              label={`${a.name} (${a.adapter})`}
              value={val}
              onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
              type="number" size="small"
              error={bad}
              helperText={bad ? 'a whole number ≥ 1' : undefined}
            />
          );
        })}
        {accounts.data!.length > 0 && (
          <>
            <Typography variant="subtitle1">Low-balance alert</Typography>
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              Overview warns when a prepaid account's balance drops below this
              amount (in the balance's own currency). Empty = no alert.
            </Typography>
            {accounts.data!.map(a => {
              const key = `billing_low_balance:${a.id}`;
              const val = form[key] ?? '';
              const bad = val !== '' && (Number.isNaN(Number(val)) || Number(val) < 0);
              return (
                <TextField key={key} className="num" label={`${a.name} (${a.adapter})`}
                           value={val} type="number" size="small" error={bad}
                           helperText={bad ? 'an amount ≥ 0, or empty' : undefined}
                           onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))} />
              );
            })}
            <Typography variant="subtitle1">IP acquisitions per 24 h</Typography>
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              Cap on IP adds and changes per account (every attempt counts) -
              stops a looping IP-change script from running up a bill.
              0 blocks them entirely.
            </Typography>
            {accounts.data!.map(a => {
              const key = `ip_change_daily_cap:${a.id}`;
              const val = form[key] ?? '';
              const bad = val === '' || !Number.isInteger(Number(val)) || Number(val) < 0;
              return (
                <TextField key={key} className="num" label={`${a.name} (${a.adapter})`}
                           value={val} type="number" size="small" error={bad}
                           helperText={bad ? 'a whole number ≥ 0' : undefined}
                           onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))} />
              );
            })}
          </>
        )}
        {accounts.data!.length > 0 && (
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <Button variant="contained" onClick={save} disabled={busy || invalid}>
              {busy ? 'Saving…' : 'Save'}
            </Button>
            <Button onClick={() => setForm(lastSynced.current ?? {})} disabled={busy || !dirty}>
              Reset
            </Button>
          </Stack>
        )}
        {error && <Alert severity="error">{error}</Alert>}
      </Stack>

      <UpdatePanel onToast={(m, sev) => setToast({ message: m, severity: sev })} />
      <ApiTokensPanel onToast={(m, sev) => setToast({ message: m, severity: sev })} />
      <TrafficHooksPanel onToast={(m, sev) => setToast({ message: m, severity: sev })} />
      <Toast msg={toast} onClose={() => setToast(null)} />
    </Stack>
  );
}

type TokenScope = 'full' | 'ip_change' | 'ip_read';

const SCOPE_HELP: Record<TokenScope, string | undefined> = {
  full: undefined,
  ip_change: 'Only the IP-change API (docs/ip-change.md)',
  ip_read: 'Only IP lookups - for the server whose IPs change',
};

interface TokenRow {
  id: number;
  name: string;
  scope: TokenScope;
  created_at: string;
  last_used_at: string | null;
}

/** Personal API tokens (developer access path). The plaintext is shown
 *  exactly once at creation - the server stores only its hash. */
function ApiTokensPanel({ onToast }: { onToast: (m: string, s?: 'success' | 'error') => void }) {
  const tokens = useQuery<TokenRow[]>({ queryKey: ['tokens'],
    queryFn: () => api<TokenRow[]>('/api/auth/tokens') });
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [scope, setScope] = useState<TokenScope>('full');
  const [created, setCreated] = useState<string | null>(null); // one-time reveal
  const [revoking, setRevoking] = useState<TokenRow | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const r = await post<{ token: string }>('/api/auth/tokens', { name: name.trim(), scope });
      setCreated(r.token);
      setName('');
      qc.invalidateQueries({ queryKey: ['tokens'] });
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack spacing={1.5} sx={{ maxWidth: 560 }}>
      <Typography variant="subtitle1">API tokens</Typography>
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        For scripts and tools hitting the panel's API. Shown once at creation,
        stored hashed — revoke anything you don't recognize. Usage:{' '}
        <code>Authorization: Bearer &lt;token&gt;</code>; interactive docs at{' '}
        <a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a>.
        AI agents connect over MCP at <code>{location.origin}/mcp</code> with the same
        header, e.g.{' '}
        <code>claude mcp add --transport http omnicloud {location.origin}/mcp --header
        "Authorization: Bearer &lt;token&gt;"</code>. A token carries its owner's role.
      </Typography>
      {created && (
        <Alert severity="success" icon={false}>
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
            <Typography variant="body2" className="num"
                        sx={{ wordBreak: 'break-all' }}>{created}</Typography>
            <Button size="small" onClick={() => navigator.clipboard.writeText(created)}>
              Copy
            </Button>
            <Button size="small" onClick={() => setCreated(null)}>Done</Button>
          </Stack>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Copy it now — it is never shown again.
          </Typography>
        </Alert>
      )}
      {(tokens.data ?? []).map(t => (
        <Stack key={t.id} direction="row" spacing={1}
               sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <Stack sx={{ minWidth: 0 }}>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>
              {t.name}{t.scope !== 'full' && (
                <Chip size="small" variant="outlined"
                      label={t.scope === 'ip_change' ? 'IP change only' : 'IP read only'}
                      sx={{ ml: 1, height: 18 }} />
              )}
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              created {new Date(t.created_at).toLocaleDateString()}
              {t.last_used_at && ` · last used ${new Date(t.last_used_at).toLocaleString()}`}
            </Typography>
          </Stack>
          <Button size="small" color="error" onClick={() => setRevoking(t)}>
            Revoke
          </Button>
        </Stack>
      ))}
      {tokens.isError && (
        <Alert severity="error"
               action={<Button size="small" onClick={() => tokens.refetch()}>Retry</Button>}>
          Token list unavailable: {(tokens.error as Error).message}
        </Alert>
      )}
      {tokens.data?.length === 0 && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          no tokens yet
        </Typography>
      )}
      <Stack direction="row" spacing={1}>
        <TextField size="small" label="Token name" value={name}
                   onChange={e => setName(e.target.value)} sx={{ width: 200 }} />
        <TextField select size="small" label="Scope" value={scope} sx={{ width: 170 }}
                   onChange={e => setScope(e.target.value as TokenScope)}
                   helperText={SCOPE_HELP[scope]}>
          <MenuItem value="full">Full (your role)</MenuItem>
          <MenuItem value="ip_change">IP change only</MenuItem>
          <MenuItem value="ip_read">IP read only</MenuItem>
        </TextField>
        <Button variant="contained" disabled={busy || !name.trim()} onClick={create}>
          {busy ? 'Creating…' : 'Create token'}
        </Button>
      </Stack>
      <ConfirmDialog
        open={revoking !== null}
        title="Revoke API token"
        serverName={revoking?.name ?? ''}
        body={`Revoke ${revoking?.name}? Anything using this token stops working immediately.`}
        confirmLabel="Revoke"
        confirming={busy}
        onConfirm={async () => {
          setBusy(true);
          try {
            await del(`/api/auth/tokens/${revoking!.id}`);
            setRevoking(null);
            qc.invalidateQueries({ queryKey: ['tokens'] });
            onToast('token revoked', 'success');
          } catch (e) {
            onToast((e as Error).message, 'error');
          } finally {
            setBusy(false);
          }
        }}
        onClose={() => setRevoking(null)}
      />
    </Stack>
  );
}

interface UpdateState {
  current: string; latest: string | null; repo: string;
  available: boolean; checked_at: string | null; notes: string | null;
  url: string | null; error: string | null; applying: boolean;
  update_method?: 'rebuild-image';
}

// ---- traffic hooks: webhook on allowance threshold ------------------------

interface HeaderRow { k: string; v: string }

interface HookForm {
  name: string; url: string; warnPct: string;
  warn: boolean; over: boolean;
  adapter: string; accountId: string; providerId: string;
  headerRows: HeaderRow[]; headersTouched: boolean;
  extraText: string;
}

const EMPTY_FORM: HookForm = {
  name: '', url: '', warnPct: '80', warn: true, over: true,
  adapter: '', accountId: '', providerId: '',
  headerRows: [], headersTouched: false, extraText: '',
};

function parseExtra(text: string): { ok: true; value: Record<string, unknown> | null }
  | { ok: false; error: string } {
  const t = text.trim();
  if (!t) return { ok: true, value: null };
  try {
    const v = JSON.parse(t);
    if (v === null || typeof v !== 'object' || Array.isArray(v))
      return { ok: false, error: 'extra must be a JSON object' };
    return { ok: true, value: v as Record<string, unknown> };
  } catch {
    return { ok: false, error: 'extra is not valid JSON' };
  }
}

/** List, create/edit and test traffic-allowance hooks, plus the delivery
 *  log. Header values are write-only: editing shows their names and a
 *  "replace headers" editor, never the stored secrets. */
function TrafficHooksPanel({ onToast }: { onToast: (m: string, s?: 'success' | 'error') => void }) {
  const qc = useQueryClient();
  const hooks = useQuery<HookRow[]>({ queryKey: ['hooks'],
    queryFn: () => api<HookRow[]>('/api/hooks') });
  const deliveries = useQuery<HookDeliveryRow[]>({ queryKey: ['hook-deliveries'],
    queryFn: () => api<HookDeliveryRow[]>('/api/hooks/deliveries?limit=25') });
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });

  const [editing, setEditing] = useState<HookRow | null>(null);   // hook being edited
  const [dialogOpen, setDialogOpen] = useState(false);
  const [form, setForm] = useState<HookForm>(EMPTY_FORM);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [testing, setTesting] = useState<HookRow | null>(null);
  const [deleting, setDeleting] = useState<HookRow | null>(null);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setDialogOpen(true);
  };
  const openEdit = (h: HookRow) => {
    setEditing(h);
    setForm({
      name: h.name, url: h.url, warnPct: String(h.warn_pct),
      warn: h.events.includes('warn'), over: h.events.includes('over'),
      adapter: h.adapter ?? '', accountId: h.account_id != null ? String(h.account_id) : '',
      providerId: h.provider_id ?? '',
      headerRows: [], headersTouched: false,
      extraText: h.extra ? JSON.stringify(h.extra, null, 2) : '',
    });
    setFormError(null);
    setDialogOpen(true);
  };

  const pct = Number(form.warnPct);
  const extra = parseExtra(form.extraText);
  const extraValue = extra.ok ? extra.value : null;
  const headerObject = Object.fromEntries(
    form.headerRows.filter(r => r.k.trim() && r.v.trim()).map(r => [r.k.trim(), r.v]));
  // mirrors the server's urlsplit rule (scheme + netloc) as early feedback;
  // the server rejects what slips past this
  const urlOk = /^https?:\/\/[^/\s]+/.test(form.url);
  const invalid = !form.name.trim() || !urlOk || !form.warn || !form.over
    || !(Number.isFinite(pct) && pct >= 1 && pct <= 100) || !extra.ok;

  const submit = async () => {
    setBusy(true); setFormError(null);
    try {
      const body: Record<string, unknown> = {
        name: form.name.trim(), url: form.url.trim(),
        events: [form.warn && 'warn', form.over && 'over'].filter(Boolean),
        warn_pct: pct,
        adapter: form.adapter || null,
        account_id: form.accountId ? Number(form.accountId) : null,
        provider_id: form.providerId.trim() || null,
        extra: extraValue,
      };
      if (!editing || form.headersTouched) body.headers = headerObject;
      if (editing) await patch(`/api/hooks/${editing.id}`, body);
      else await post('/api/hooks', body);
      setEditing(null);
      setDialogOpen(false);
      qc.invalidateQueries({ queryKey: ['hooks'] });
      onToast(editing ? 'hook updated' : 'hook created', 'success');
    } catch (e) {
      setFormError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleEnabled = async (h: HookRow) => {
    try {
      await patch(`/api/hooks/${h.id}`, { enabled: !h.enabled });
      qc.invalidateQueries({ queryKey: ['hooks'] });
    } catch (e) {
      onToast((e as Error).message, 'error');
    }
  };

  const runTest = async (h: HookRow) => {
    setTesting(null); setBusy(true);
    try {
      await post(`/api/hooks/${h.id}/test`);
      onToast(`test POST delivered to ${h.name}`, 'success');
      qc.invalidateQueries({ queryKey: ['hooks'] });
      qc.invalidateQueries({ queryKey: ['hook-deliveries'] });
    } catch (e) {
      onToast((e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Stack spacing={1.5} sx={{ maxWidth: 720 }}>
      <Typography variant="subtitle1">Traffic hooks</Typography>
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        POST an HTTP API of yours when a server's traffic allowance crosses a
        threshold (warn %, and 100%). Evaluated on every sync; fired once per
        upward crossing and re-armed when usage drops back below. Test any
        hook before relying on it.
      </Typography>

      {hooks.isError && (
        <Alert severity="error"
               action={<Button size="small" onClick={() => hooks.refetch()}>Retry</Button>}>
          Hooks unavailable: {(hooks.error as Error).message}
        </Alert>
      )}
      {(hooks.data ?? []).map(h => {
        const filter = [h.adapter, h.account_id != null ? `account ${h.account_id}` : null,
                         h.provider_id].filter(Boolean).join(' · ');
        return (
          <Stack key={h.id} spacing={0.5}
                 sx={{ border: 1, borderColor: 'divider', borderRadius: 1, p: 1.5 }}>
            <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
              <Stack sx={{ minWidth: 0, flex: 1 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}
                              className={h.enabled ? '' : 'muted'}>{h.name}</Typography>
                  {!h.enabled && (
                    <Chip size="small" variant="outlined" label="off" sx={{ height: 18 }} />
                  )}
                </Stack>
                <Typography variant="caption" className="num" noWrap
                            sx={{ color: 'text.secondary', display: 'block' }}>
                  {h.url}
                </Typography>
              </Stack>
              <Tooltip title={h.enabled ? 'Disable (keeps config and history)' : 'Enable'}>
                <Switch size="small" checked={h.enabled} onChange={() => toggleEnabled(h)} />
              </Tooltip>
            </Stack>
            <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', gap: 0.5, alignItems: 'center' }}>
              {h.events.map(ev => (
                <Chip key={ev} size="small" variant="outlined" sx={{ height: 18 }}
                      label={ev === 'warn' ? `warn ≥ ${h.warn_pct}%` : 'over ≥ 100%'} />
              ))}
              {filter && (
                <Chip size="small" variant="outlined" sx={{ height: 18 }} label={filter} />
              )}
              {h.header_names.length > 0 && (
                <Chip size="small" variant="outlined" sx={{ height: 18 }}
                      label={`${h.header_names.length} header${h.header_names.length > 1 ? 's' : ''}`} />
              )}
            </Stack>
            <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', justifyContent: 'space-between' }}>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {h.last_delivery
                  ? <>last {h.last_delivery.event} · {h.last_delivery.status}
                     {h.last_delivery.error ? ` (${h.last_delivery.error})` : ''} · {fmtRelative(h.last_delivery.created_at)}</>
                  : 'never delivered'}
              </Typography>
              <Stack direction="row" spacing={0.5}>
                <Tooltip title="Send one real test POST now (never arms)">
                  <IconButton size="small" aria-label={`Test ${h.name}`}
                              disabled={busy} onClick={() => setTesting(h)}>
                    <SendIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <IconButton size="small" aria-label={`Edit ${h.name}`} onClick={() => openEdit(h)}>
                  <EditOutlinedIcon fontSize="small" />
                </IconButton>
                <IconButton size="small" aria-label={`Delete ${h.name}`} color="error"
                            onClick={() => setDeleting(h)}>
                  <DeleteOutlineIcon fontSize="small" />
                </IconButton>
              </Stack>
            </Stack>
          </Stack>
        );
      })}
      {hooks.data?.length === 0 && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          no hooks yet
        </Typography>
      )}
      <Stack direction="row" spacing={1}>
        <Button size="small" variant="outlined" onClick={openCreate}>Add hook</Button>
      </Stack>

      {/* deliveries log */}
      <Divider sx={{ mt: 1 }} />
      <Typography variant="subtitle2">Recent deliveries</Typography>
      {deliveries.data && deliveries.data.length > 0 ? (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>When</TableCell>
              <TableCell>Hook</TableCell>
              <TableCell>Event</TableCell>
              <TableCell>Server</TableCell>
              <TableCell>Result</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {deliveries.data.map(d => (
              <TableRow key={d.id}>
                <TableCell className="num">{fmtRelative(d.created_at)}</TableCell>
                <TableCell>{d.hook_name ?? d.hook_id}</TableCell>
                <TableCell>{d.event}</TableCell>
                <TableCell>{d.server_name ?? d.provider_id}</TableCell>
                <TableCell>
                  {d.status === 'delivered'
                    ? `delivered (HTTP ${d.response_status})`
                    : `failed${d.response_status ? ` (HTTP ${d.response_status})` : ''}${d.error ? ` — ${d.error}` : ''}`}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>nothing delivered yet</Typography>
      )}

      {/* create / edit dialog */}
      <Dialog open={dialogOpen} onClose={() => setDialogOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{editing ? `Edit hook “${editing.name}”` : 'Add traffic hook'}</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ pt: 1 }}>
            <TextField size="small" label="Name" value={form.name}
                       onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
            <TextField size="small" label="Target URL" value={form.url} className="num"
                       placeholder="https://example.com/alert"
                       helperText="POSTed a JSON payload on each threshold crossing"
                       error={form.url !== '' && !urlOk}
                       onChange={e => setForm(f => ({ ...f, url: e.target.value }))} />
            <Stack direction="row" spacing={1}>
              <TextField size="small" className="num" label="Warn %" type="number"
                         sx={{ width: 130 }} value={form.warnPct}
                         onChange={e => setForm(f => ({ ...f, warnPct: e.target.value }))} />
              <FormControlLabel label="warn" control={<Switch checked={form.warn}
                onChange={e => setForm(f => ({ ...f, warn: e.target.checked }))} />} />
              <FormControlLabel label="over (100%)" control={<Switch checked={form.over}
                onChange={e => setForm(f => ({ ...f, over: e.target.checked }))} />} />
            </Stack>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              warn fires at the % above, over at 100% of the included allowance.
            </Typography>
            <TextField select size="small" label="Provider (optional)"
                       value={form.adapter} sx={{ maxWidth: 260 }}
                       onChange={e => setForm(f => ({ ...f, adapter: e.target.value }))}>
              <MenuItem value=""><em>any provider</em></MenuItem>
              {(adapters.data ?? []).map(a => (
                <MenuItem key={a.key} value={a.key}>{a.display_name}</MenuItem>
              ))}
            </TextField>
            <TextField select size="small" label="Account (optional)"
                       value={form.accountId} sx={{ maxWidth: 260 }}
                       onChange={e => setForm(f => ({ ...f, accountId: e.target.value }))}>
              <MenuItem value=""><em>any account</em></MenuItem>
              {(accounts.data ?? []).map(a => (
                <MenuItem key={a.id} value={String(a.id)}>{a.name} ({a.adapter})</MenuItem>
              ))}
            </TextField>
            <TextField size="small" label="Server id (optional)" className="num"
                       value={form.providerId}
                       helperText="provider_id of one server - the panel's own id, not its name"
                       onChange={e => setForm(f => ({ ...f, providerId: e.target.value }))} />
            <Divider>Headers{editing && ' (stored secret — edit replaces all)'}</Divider>
            {editing && !form.headersTouched && (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {editing.header_names.length
                  ? `current: ${editing.header_names.join(', ')} — adding rows below replaces them`
                  : 'none set'}
              </Typography>
            )}
            {form.headerRows.map((r, i) => (
              <Stack key={i} direction="row" spacing={1}>
                <TextField size="small" label="Name" sx={{ flex: 1 }}
                           value={r.k}
                           onChange={e => setForm(f => ({
                             ...f, headersTouched: true,
                             headerRows: f.headerRows.map((x, j) => j === i ? { ...x, k: e.target.value } : x),
                           }))} />
                <TextField size="small" label="Value" sx={{ flex: 1 }} type="password"
                           value={r.v}
                           onChange={e => setForm(f => ({
                             ...f, headersTouched: true,
                             headerRows: f.headerRows.map((x, j) => j === i ? { ...x, v: e.target.value } : x),
                           }))} />
                <Button size="small" color="error"
                        onClick={() => setForm(f => ({
                          ...f, headerRows: f.headerRows.filter((_, j) => j !== i),
                        }))}>×</Button>
              </Stack>
            ))}
            <Button size="small" variant="outlined" sx={{ alignSelf: 'start' }}
                    onClick={() => setForm(f => ({ ...f, headersTouched: true,
                                                   headerRows: [...f.headerRows, { k: '', v: '' }] }))}>
              Add header
            </Button>
            <TextField size="small" label="Extra payload (JSON object, optional)" multiline
                       minRows={2} className="num" value={form.extraText}
                       error={!extra.ok} helperText={extra.ok ? undefined : extra.error}
                       onChange={e => setForm(f => ({ ...f, extraText: e.target.value }))} />
          </Stack>
        </DialogContent>
        <DialogActions>
          {formError && <Typography variant="caption" color="error"
                                    sx={{ mr: 'auto', pl: 2 }}>{formError}</Typography>}
          <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
          <Button variant="contained" disabled={busy || invalid}
                  onClick={submit}>{busy ? 'Saving…' : editing ? 'Save' : 'Create'}</Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={testing !== null}
        title="Send test POST"
        serverName={testing?.name ?? ''}
        body={`Deliver a test event to ${testing?.name}'s URL right now? It never arms, so it is safe to repeat.`}
        confirmLabel="Send"
        confirming={busy}
        onConfirm={() => testing && runTest(testing)}
        onClose={() => setTesting(null)}
      />
      <ConfirmDialog
        open={deleting !== null}
        title="Delete hook"
        serverName={deleting?.name ?? ''}
        body={`Delete ${deleting?.name}? Its delivery history goes with it. This cannot be undone.`}
        confirmLabel="Delete"
        confirming={busy}
        onConfirm={async () => {
          setBusy(true);
          try {
            await del(`/api/hooks/${deleting!.id}`);
            setDeleting(null);
            qc.invalidateQueries({ queryKey: ['hooks'] });
            onToast('hook deleted', 'success');
          } catch (e) {
            onToast((e as Error).message, 'error');
          } finally {
            setBusy(false);
          }
        }}
        onClose={() => setDeleting(null)}
      />
    </Stack>
  );
}

function UpdatePanel({ onToast }: { onToast: (m: string, s?: 'success' | 'error') => void }) {
  const u = useQuery<UpdateState>({ queryKey: ['update'],
    queryFn: () => api<UpdateState>('/api/update/status') });
  const qc = useQueryClient();
  const [applying, setApplying] = useState(false);
  if (u.isPending) return null;
  // a failed status query must never fall through to u.data! (undefined ->
  // render throw -> app-wide ErrorBoundary replaces the whole dashboard)
  if (u.isError) return (
    <Stack spacing={1.5} sx={{ maxWidth: 560 }}>
      <Typography variant="subtitle1">Panel update</Typography>
      <Alert severity="error"
             action={<Button onClick={() => u.refetch()}>Retry</Button>}>
        Update status unavailable: {(u.error as Error).message}
      </Alert>
    </Stack>
  );
  const d = u.data!;
  const available = d.available;  // server-side semver compare, not string !=
  return (
    <Stack spacing={1.5} sx={{ maxWidth: 560 }}>
      <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center' }}>
        <Typography variant="subtitle1">Panel update</Typography>
        <Chip size="small" variant="outlined" className="num" label={`v${d.current}`} />
      </Stack>
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        Checks GitHub ({d.repo}) daily;{' '}
        {d.update_method === 'rebuild-image'
          ? 'this install updates by rebuilding the container image (see DEPLOY.md).'
          : 'the apply button pulls the latest code and restarts the panel.'}
      </Typography>
      {available && (
        <Alert severity="info" icon={false}>
          <Stack spacing={0.5}>
            <Typography variant="body2">
              <b>v{d.latest}</b> is available (running v{d.current})
              {safeHref(d.url) && <> — <a href={safeHref(d.url)} target="_blank" rel="noreferrer">release notes</a></>}
            </Typography>
            {d.update_method === 'rebuild-image' && (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                To update: pull the latest code on the host, rebuild the image,
                and recreate the container. Your data volume is untouched.
              </Typography>
            )}
            {d.notes && <Typography variant="caption" sx={{ color: 'text.secondary',
              whiteSpace: 'pre-wrap', maxHeight: 120, overflowY: 'auto', display: 'block' }}>
              {d.notes}
            </Typography>}
          </Stack>
        </Alert>
      )}
      {!available && d.checked_at && !d.error && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          up to date · last checked {new Date(d.checked_at).toLocaleString()}
        </Typography>
      )}
      {d.error && (
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          last check failed: {d.error}
        </Typography>
      )}
      <Stack direction="row" spacing={1}>
        <Button size="small" variant="outlined" disabled={d.applying || applying}
                onClick={async () => {
                  await post('/api/update/check');
                  qc.invalidateQueries({ queryKey: ['update'] });
                }}>
          Check now
        </Button>
        {available && d.update_method !== 'rebuild-image' && (
          <Tooltip title="Pulls the latest code, rebuilds the panel, and restarts it. Your data (database, credentials, settings) is untouched.">
            <Button size="small" variant="contained" color="primary"
                    disabled={applying || d.applying}
                    onClick={async () => {
                      setApplying(true);
                      try {
                        const r = await post<{ detail?: string }>('/api/update/apply');
                        onToast(r.detail ?? 'update running', 'success');
                      } catch (e) {
                        onToast((e as Error).message, 'error');
                        setApplying(false);
                      }
                    }}>
              {applying || d.applying ? 'Updating…' : `Update to v${d.latest}`}
            </Button>
          </Tooltip>
        )}
      </Stack>
    </Stack>
  );
}
