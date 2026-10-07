import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { api, del, post, put, safeHref } from '../api';
import Chip from '@mui/material/Chip';
import MenuItem from '@mui/material/MenuItem';
import Tooltip from '@mui/material/Tooltip';
import type { AccountRow } from '../types';
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
