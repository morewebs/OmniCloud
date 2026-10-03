import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
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
import Typography from '@mui/material/Typography';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { api, patch, post } from '../api';
import type { UserRow } from '../types';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Toast } from '../components/Toast';
import type { ToastMsg } from '../components/Toast';
import { usePageTitle } from '../usePageTitle';

export function UsersView() {
  usePageTitle('Users');
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ id: number; role: string }>('/api/auth/me') });
  const users = useQuery<UserRow[]>({ queryKey: ['users'],
    queryFn: () => api<UserRow[]>('/api/users') });
  const [addOpen, setAddOpen] = useState(false);
  const [toast, setToast] = useState<ToastMsg>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [elevate, setElevate] = useState<UserRow | null>(null);

  if (users.isPending) {
    return <Stack spacing={1.5}>
      <Skeleton variant="rounded" height={32} sx={{ maxWidth: 200 }} />
      {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} variant="rounded" height={44} />)}
    </Stack>;
  }
  if (users.isError) return (
    <Stack spacing={2}>
      <PageHeader title="Users" />
      <Alert severity="error"
             action={<Button onClick={() => users.refetch()}>Retry</Button>}>
        {(users.error as Error).message}
      </Alert>
    </Stack>
  );

  const mut = async (u: UserRow, body: Record<string, unknown>, what: string) => {
    setBusyId(u.id);
    try {
      await patch(`/api/users/${u.id}`, body);
      await qc.invalidateQueries({ queryKey: ['users'] });
      setToast({ message: `${u.username}: ${what}`, severity: 'success' });
    } catch (e) {
      setToast({ message: `${u.username}: ${(e as Error).message}`, severity: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Stack spacing={3}>
      <PageHeader title="Users"
        subtitle="Who can sign in. Viewers read; admins can change infrastructure."
        actions={me.data?.role === 'admin' && (
          <Button variant="contained" size="small" onClick={() => setAddOpen(true)}>
            Add user
          </Button>)} />

      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Username</TableCell>
            <TableCell>Role</TableCell>
            <TableCell>Created</TableCell>
            <TableCell>Status</TableCell>
            <TableCell align="right">Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {users.data!.map(u => {
            const self = u.id === me.data?.id;
            const busy = busyId === u.id;
            return (
              <TableRow key={u.id} sx={u.disabled ? { opacity: 0.5 } : undefined}>
                <TableCell>
                  {u.username}{self && <Typography variant="caption" sx={{ color: 'text.secondary', ml: 1 }}>(you)</Typography>}
                </TableCell>
                <TableCell>
                  <Chip size="small" variant="outlined" label={u.role}
                        color={u.role === 'admin' ? 'primary' : 'default'} />
                </TableCell>
                <TableCell className="num">{u.created_at.slice(0, 10)}</TableCell>
                <TableCell>{u.disabled ? 'disabled' : 'active'}</TableCell>
                <TableCell align="right">
                  <Stack direction="row" spacing={1} sx={{ justifyContent: "flex-end" }}>
                    <Tooltip title={self ? 'You cannot change your own role' : ''}>
                      <span>
                        <Button size="small" disabled={busy || self}
                                onClick={() => {
                                  if (u.role === 'viewer') setElevate(u);
                                  else mut(u, { role: 'viewer' }, 'demoted to viewer');
                                }}>
                          Make {u.role === 'admin' ? 'viewer' : 'admin'}
                        </Button>
                      </span>
                    </Tooltip>
                    <Tooltip title={self ? 'You cannot disable your own account' : ''}>
                      <span>
                        <Button size="small" disabled={busy || self}
                                color={u.disabled ? 'primary' : 'error'}
                                onClick={() => mut(u, { disabled: !u.disabled },
                                  u.disabled ? 'enabled' : 'disabled')}>
                          {u.disabled ? 'Enable' : 'Disable'}
                        </Button>
                      </span>
                    </Tooltip>
                  </Stack>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      <ConfirmDialog
        open={!!elevate}
        title="Make admin"
        serverName={elevate?.username ?? ''}
        body={`Grant ${elevate?.username} full admin access: they can manage servers, order plans, and change every setting. This is a privilege escalation.`}
        confirmLabel="Make admin"
        onConfirm={() => { if (elevate) mut(elevate, { role: 'admin' }, 'promoted to admin'); setElevate(null); }}
        onClose={() => setElevate(null)}
      />

      {addOpen && <AddUserDialog onClose={() => setAddOpen(false)}
                                 onDone={() => { setAddOpen(false);
                                   qc.invalidateQueries({ queryKey: ['users'] }); }} />}
      <Toast msg={toast} onClose={() => setToast(null)} />
    </Stack>
  );
}

function AddUserDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('viewer');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPw, setShowPw] = useState(false);

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await post('/api/users', { username, password, role });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog aria-labelledby="omni-dlg-177" open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle id="omni-dlg-177">Add user</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField label="Username" value={username}
                     onChange={e => setUsername(e.target.value)} size="small" required />
          <TextField label="Password" type={showPw ? 'text' : 'password'} value={password}
                     onChange={e => setPassword(e.target.value)} size="small" required
                     helperText="At least 8 characters"
                     slotProps={{ input: { endAdornment: (
                       <InputAdornment position="end">
                         <IconButton size="small" aria-label={showPw ? 'Hide password' : 'Show password'}
                                     onClick={() => setShowPw(s => !s)} edge="end">
                           {showPw ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                         </IconButton>
                       </InputAdornment>
                     ) } }} />
          <TextField select label="Role" value={role} onChange={e => setRole(e.target.value)}
                     size="small">
            <MenuItem value="viewer">Viewer - read-only</MenuItem>
            <MenuItem value="admin">Admin - can make changes</MenuItem>
          </TextField>
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained" disabled={busy || !username || password.length < 8}
                onClick={submit}>
          {busy ? 'Adding…' : 'Add'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
