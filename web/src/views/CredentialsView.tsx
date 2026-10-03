import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { api, del, fmtRelative, post } from '../api';
import type { AccountRow, AdapterInfo } from '../types';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Toast } from '../components/Toast';
import type { ToastMsg } from '../components/Toast';
import { usePageTitle } from '../usePageTitle';

// Credential rows show metadata only - the secret is never retrievable.
export function CredentialsView() {
  usePageTitle('Credentials');
  const qc = useQueryClient();
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ role: string }>('/api/auth/me') });
  const isAdmin = me.data?.role === 'admin';
  const [addOpen, setAddOpen] = useState(false);
  const [toast, setToast] = useState<ToastMsg>(null);
  const [removeAcct, setRemoveAcct] = useState<AccountRow | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  if (accounts.isPending) {
    return <Stack spacing={1.5}>
      <Skeleton variant="rounded" height={32} sx={{ maxWidth: 220 }} />
      {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} variant="rounded" height={44} />)}
    </Stack>;
  }
  if (accounts.isError) return (
    <Stack spacing={2}>
      <PageHeader title="Credentials" />
      <Alert severity="error"
             action={<Button onClick={() => accounts.refetch()}>Retry</Button>}>
        {(accounts.error as Error).message}
      </Alert>
    </Stack>
  );

  const doSync = async (a: AccountRow) => {
    setBusyId(a.id);
    try {
      await post(`/api/accounts/${a.id}/sync`);
      setToast({ message: `Syncing ${a.name} — results land in the next refresh`, severity: 'info' });
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['fleet'] });
    } catch (e) {
      setToast({ message: `${a.name}: ${(e as Error).message}`, severity: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  const doRemove = async (a: AccountRow) => {
    setBusyId(a.id);
    try {
      await del(`/api/accounts/${a.id}`);
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['fleet'] });
      setToast({ message: `${a.name} removed — its cached data is gone; the provider account is untouched`, severity: 'success' });
    } catch (e) {
      setToast({ message: `${a.name}: ${(e as Error).message}`, severity: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Stack spacing={3}>
      <PageHeader title="Credentials"
        subtitle="Provider API tokens, stored encrypted — only the last 4 characters ever come back out."
        actions={isAdmin && (
          <Button variant="contained" size="small" onClick={() => setAddOpen(true)}>
            Add account
          </Button>)} />

      {accounts.data!.length === 0 && (
        <Alert severity="info" icon={false}>
          No provider accounts yet. Add one to import its fleet — Hetzner and
          LeaseWeb adapters are ready; the other five providers feed the plan
          catalog without credentials.
        </Alert>
      )}

      {accounts.data!.length > 0 && (
        <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Provider</TableCell>
              <TableCell>Account</TableCell>
              <TableCell>Token</TableCell>
              <TableCell>Added</TableCell>
              <TableCell>Last sync</TableCell>
              <TableCell>Status</TableCell>
              {isAdmin && <TableCell align="right">Actions</TableCell>}
            </TableRow>
          </TableHead>
          <TableBody>
            {accounts.data!.map(a => (
              <TableRow key={a.id}>
                <TableCell><Chip size="small" variant="outlined" label={a.adapter} /></TableCell>
                <TableCell>{a.name}</TableCell>
                <TableCell><span className="num">••••{a.last4}</span></TableCell>
                <TableCell className="num">{a.cred_created?.slice(0, 10) ?? '—'}</TableCell>
                <TableCell>
                  <Tooltip title={a.last_success_at ?? 'never synced'}>
                    <span className="num">
                      {a.last_success_at ? fmtRelative(a.last_success_at) : '—'}
                    </span>
                  </Tooltip>
                </TableCell>
                <TableCell>
                  {a.last_error
                    ? <Tooltip title={a.last_error}>
                        <Chip size="small" color="error" variant="outlined" label="sync error" />
                      </Tooltip>
                    : a.enabled
                      ? <Chip size="small" color="success" variant="outlined" label="enabled" />
                      : <Chip size="small" variant="outlined" label="disabled" />}
                </TableCell>
                {isAdmin && (
                  <TableCell align="right">
                    <Stack direction="row" spacing={1} sx={{ justifyContent: "flex-end" }}>
                      <Button size="small" disabled={busyId === a.id}
                              onClick={() => doSync(a)}>
                        {busyId === a.id ? 'Syncing…' : 'Sync'}
                      </Button>
                      <Button size="small" color="error" disabled={busyId === a.id}
                              onClick={() => setRemoveAcct(a)}>
                        Remove
                      </Button>
                    </Stack>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      )}

      <ConfirmDialog
        open={!!removeAcct}
        title="Remove account"
        serverName={removeAcct?.name ?? ''}
        body={`Remove ${removeAcct?.name} from the panel. Its cached servers and history are deleted; the provider account itself is untouched.`}
        confirmLabel="Remove"
        confirming={busyId === removeAcct?.id}
        onConfirm={() => { if (removeAcct) doRemove(removeAcct); setRemoveAcct(null); }}
        onClose={() => setRemoveAcct(null)}
      />

      {addOpen && (
        <AddAccountDialog
          adapters={adapters}
          onClose={() => setAddOpen(false)}
          onDone={() => { setAddOpen(false); qc.invalidateQueries(); }}
        />
      )}
      <Toast msg={toast} onClose={() => setToast(null)} />
    </Stack>
  );
}

function AddAccountDialog({ adapters, onClose, onDone }: {
  adapters: { data?: AdapterInfo[]; isPending: boolean }; onClose: () => void; onDone: () => void;
}) {
  const list = adapters.data ?? [];
  const [adapter, setAdapter] = useState('');
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const effAdapter = adapter || list[0]?.key || '';

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await post('/api/accounts', { adapter: effAdapter, name, token });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog aria-labelledby="omni-dlg-212" open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle id="omni-dlg-212">Add provider account</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField select label="Provider" value={effAdapter}
                     onChange={e => setAdapter(e.target.value)} size="small" required
                     disabled={adapters.isPending}>
            {adapters.isPending && <MenuItem value="" disabled>Loading providers…</MenuItem>}
            {list.filter(a => !a.source).map(a =>
              <MenuItem key={a.key} value={a.key}>{a.display_name}</MenuItem>)}
          </TextField>
          {effAdapter === 'hetzner' && (
            <Alert severity="info" icon={false}>
              Create a read/write token at console.hetzner.com → Security →
              API tokens, then paste it here.
            </Alert>
          )}
          {effAdapter === 'leaseweb' && (
            <Alert severity="info" icon={false}>
              Create an API key at secure.leaseweb.com → API → API keys
              (read/write for full panel features).
            </Alert>
          )}
          <TextField label="Account name" value={name}
                     onChange={e => setName(e.target.value)} size="small" required
                     helperText="A label, e.g. main or edge" />
          <TextField label="API token" type={showToken ? 'text' : 'password'} value={token}
                     onChange={e => setToken(e.target.value)} size="small" required
                     helperText="Stored encrypted. Only the last 4 characters are ever shown."
                     slotProps={{ input: { endAdornment: (
                       <InputAdornment position="end">
                         <IconButton size="small" edge="end"
                                     aria-label={showToken ? 'Hide token' : 'Show token'}
                                     onClick={() => setShowToken(s => !s)}>
                           {showToken ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                         </IconButton>
                       </InputAdornment>
                     ) } }} />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained"
                disabled={busy || !effAdapter || !name || !token || adapters.isPending}
                onClick={submit}>{busy ? 'Adding…' : 'Add'}</Button>
      </DialogActions>
    </Dialog>
  );
}
