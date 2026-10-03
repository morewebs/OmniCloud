import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { api, post, put } from '../api';
import Chip from '@mui/material/Chip';
import Tooltip from '@mui/material/Tooltip';
import type { AccountRow } from '../types';
import { PageHeader } from '../components/PageHeader';
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastMsg>(null);

  useEffect(() => {
    if (settings.data) setForm(settings.data);
  }, [settings.data]);

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
    k.startsWith('sync_interval:') && (!Number.isInteger(Number(v)) || Number(v) < 1));

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

  const dirty = JSON.stringify(form) !== JSON.stringify(settings.data);

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
          const val = form[key] ?? '5';
          const bad = !Number.isInteger(Number(val)) || Number(val) < 1;
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
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <Button variant="contained" onClick={save} disabled={busy || invalid}>
              {busy ? 'Saving…' : 'Save'}
            </Button>
            <Button onClick={() => setForm(settings.data!)} disabled={busy || !dirty}>
              Reset
            </Button>
          </Stack>
        )}
        {error && <Alert severity="error">{error}</Alert>}
      </Stack>

      <UpdatePanel onToast={(m, sev) => setToast({ message: m, severity: sev })} />
      <Toast msg={toast} onClose={() => setToast(null)} />
    </Stack>
  );
}

interface UpdateState {
  current: string; latest: string | null; repo: string;
  checked_at: string | null; notes: string | null; url: string | null;
  error: string | null; applying: boolean;
}

function UpdatePanel({ onToast }: { onToast: (m: string, s?: 'success' | 'error') => void }) {
  const u = useQuery<UpdateState>({ queryKey: ['update'],
    queryFn: () => api<UpdateState>('/api/update/status') });
  const qc = useQueryClient();
  const [applying, setApplying] = useState(false);
  if (u.isPending) return null;
  const d = u.data!;
  const available = !!d.latest && d.latest !== d.current;
  return (
    <Stack spacing={1.5} sx={{ maxWidth: 560 }}>
      <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center' }}>
        <Typography variant="subtitle1">Panel update</Typography>
        <Chip size="small" variant="outlined" className="num" label={`v${d.current}`} />
      </Stack>
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        Checks GitHub ({d.repo}) daily; the apply button pulls the latest code
        and restarts the panel.
      </Typography>
      {available && (
        <Alert severity="info" icon={false}>
          <Stack spacing={0.5}>
            <Typography variant="body2">
              <b>v{d.latest}</b> is available (running v{d.current})
              {d.url && <> — <a href={d.url} target="_blank" rel="noreferrer">release notes</a></>}
            </Typography>
            {d.notes && <Typography variant="caption" sx={{ color: 'text.secondary',
              whiteSpace: 'pre-wrap', maxHeight: 120, overflowY: 'auto', display: 'block' }}>
              {d.notes}
            </Typography>}
          </Stack>
        </Alert>
      )}
      {!available && d.checked_at && (
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
        {available && (
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
