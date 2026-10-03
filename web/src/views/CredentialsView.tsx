import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { api, del, post } from '../api';
import type { AccountRow, AdapterInfo } from '../types';

// Credential rows show metadata only - the secret is never retrievable.
export function CredentialsView() {
  const qc = useQueryClient();
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ role: string }>('/api/auth/me') });
  const isAdmin = me.data?.role === 'admin';
  const [addOpen, setAddOpen] = useState(false);

  if (accounts.isPending) return null;
  if (accounts.isError) return <Alert severity="error">{(accounts.error as Error).message}</Alert>;

  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography variant="h5">Credentials</Typography>
        {isAdmin && (
          <Button variant="contained" size="small" onClick={() => setAddOpen(true)}>
            Add account
          </Button>
        )}
      </Stack>

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Provider</TableCell>
            <TableCell>Account</TableCell>
            <TableCell>Token</TableCell>
            <TableCell>Scope</TableCell>
            <TableCell>Added</TableCell>
            <TableCell>Last used</TableCell>
            <TableCell>Last sync</TableCell>
            <TableCell>Status</TableCell>
            {isAdmin && <TableCell align="right">Actions</TableCell>}
          </TableRow>
        </TableHead>
        <TableBody>
          {(accounts.data ?? []).map(a => (
            <TableRow key={a.id}>
              <TableCell><Chip size="small" variant="outlined" label={a.adapter} /></TableCell>
              <TableCell>{a.name}</TableCell>
              <TableCell><span className="num">••••{a.last4}</span></TableCell>
              <TableCell>{a.scope ?? '—'}</TableCell>
              <TableCell>{a.cred_created?.slice(0, 10) ?? '—'}</TableCell>
              <TableCell>{a.last_used_at?.slice(0, 10) ?? '—'}</TableCell>
              <TableCell>
                {a.last_success_at
                  ? <span className="num">{a.last_success_at.slice(11, 16)}</span>
                  : '—'}
              </TableCell>
              <TableCell>
                {a.last_error
                  ? <Chip size="small" color="error" variant="outlined" label="sync error" />
                  : a.enabled ? <Chip size="small" color="success" variant="outlined" label="enabled" />
                  : <Chip size="small" variant="outlined" label="disabled" />}
              </TableCell>
              {isAdmin && (
                <TableCell align="right">
                  <Stack direction="row" spacing={1} sx={{ justifyContent: "flex-end" }}>
                    <Button size="small" onClick={() =>
                      post(`/api/accounts/${a.id}/sync`).then(() =>
                        qc.invalidateQueries({ queryKey: ['accounts'] }))}>
                      Sync
                    </Button>
                    <Button size="small" color="error" onClick={() => {
                      if (confirm(`Remove account ${a.name}? Its cache is deleted; the provider account is untouched.`)) {
                        del(`/api/accounts/${a.id}`).then(() =>
                          qc.invalidateQueries({ queryKey: ['accounts'] }));
                      }
                    }}>Remove</Button>
                  </Stack>
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {addOpen && (
        <AddAccountDialog
          adapters={adapters.data ?? []}
          onClose={() => setAddOpen(false)}
          onDone={() => { setAddOpen(false); qc.invalidateQueries(); }}
        />
      )}
    </Stack>
  );
}

function AddAccountDialog({ adapters, onClose, onDone }: {
  adapters: AdapterInfo[]; onClose: () => void; onDone: () => void;
}) {
  const [adapter, setAdapter] = useState(adapters[0]?.key ?? '');
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await post('/api/accounts', { adapter, name, token });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Add provider account</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField select label="Provider" value={adapter}
                     onChange={e => setAdapter(e.target.value)} size="small" required>
            {adapters.map(a => <MenuItem key={a.key} value={a.key}>{a.display_name}</MenuItem>)}
          </TextField>
          <TextField label="Account name" value={name}
                     onChange={e => setName(e.target.value)} size="small" required
                     helperText="A label, e.g. main or edge" />
          <TextField label="API token" type="password" value={token}
                     onChange={e => setToken(e.target.value)} size="small" required
                     helperText="Stored encrypted. Only the last 4 characters are ever shown." />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={busy || !adapter || !name || !token}
                onClick={submit}>{busy ? 'Adding…' : 'Add'}</Button>
      </DialogActions>
    </Dialog>
  );
}
