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
import { api, patch, post } from '../api';
import type { UserRow } from '../types';

export function UsersView() {
  const qc = useQueryClient();
  const users = useQuery<UserRow[]>({ queryKey: ['users'],
    queryFn: () => api<UserRow[]>('/api/users') });
  const [addOpen, setAddOpen] = useState(false);

  if (users.isPending) return null;
  if (users.isError) return <Alert severity="error">{(users.error as Error).message}</Alert>;

  const setRole = (u: UserRow, role: string) =>
    patch(`/api/users/${u.id}`, { role }).then(() =>
      qc.invalidateQueries({ queryKey: ['users'] }));
  const setDisabled = (u: UserRow, disabled: boolean) =>
    patch(`/api/users/${u.id}`, { disabled }).then(() =>
      qc.invalidateQueries({ queryKey: ['users'] }));

  return (
    <Stack spacing={2}>
      <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <Typography variant="h5">Users</Typography>
        <Button variant="contained" size="small" onClick={() => setAddOpen(true)}>
          Add user
        </Button>
      </Stack>

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
          {users.data!.map(u => (
            <TableRow key={u.id}>
              <TableCell>{u.username}</TableCell>
              <TableCell>
                <Chip size="small" variant="outlined" label={u.role}
                      color={u.role === 'admin' ? 'primary' : 'default'} />
              </TableCell>
              <TableCell>{u.created_at.slice(0, 10)}</TableCell>
              <TableCell>{u.disabled ? 'disabled' : 'active'}</TableCell>
              <TableCell align="right">
                <Stack direction="row" spacing={1} sx={{ justifyContent: "flex-end" }}>
                  <Button size="small"
                          onClick={() => setRole(u, u.role === 'admin' ? 'viewer' : 'admin')}>
                    Make {u.role === 'admin' ? 'viewer' : 'admin'}
                  </Button>
                  <Button size="small" color={u.disabled ? 'primary' : 'error'}
                          onClick={() => setDisabled(u, !u.disabled)}>
                    {u.disabled ? 'Enable' : 'Disable'}
                  </Button>
                </Stack>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      {addOpen && <AddUserDialog onClose={() => setAddOpen(false)}
                                 onDone={() => { setAddOpen(false);
                                   qc.invalidateQueries({ queryKey: ['users'] }); }} />}
    </Stack>
  );
}

function AddUserDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('viewer');
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    try {
      await post('/api/users', { username, password, role });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Add user</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField label="Username" value={username}
                     onChange={e => setUsername(e.target.value)} size="small" required />
          <TextField label="Password" type="password" value={password}
                     onChange={e => setPassword(e.target.value)} size="small" required />
          <TextField select label="Role" value={role} onChange={e => setRole(e.target.value)}
                     size="small">
            <MenuItem value="viewer">Viewer - read-only</MenuItem>
            <MenuItem value="admin">Admin - can make changes</MenuItem>
          </TextField>
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!username || !password} onClick={submit}>
          Add
        </Button>
      </DialogActions>
    </Dialog>
  );
}
