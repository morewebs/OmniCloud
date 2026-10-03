import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { post } from '../api';

/** Login (and first-run setup: creates the first admin, then closes forever). */
export function LoginView({ needsSetup }: { needsSetup: boolean }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      await post(needsSetup ? '/api/auth/setup' : '/api/auth/login', { username, password });
      location.href = '/';
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', p: 2 }}>
      <Card sx={{ width: '100%', maxWidth: 380 }}>
        <CardContent sx={{ p: 4 }}>
          <Stack spacing={2} component="form" onSubmit={submit}>
            <Typography variant="h5" component="h1">OmniCloud</Typography>
            {needsSetup ? (
              <Alert severity="info">
                First run. This creates the first admin account; setup closes
                once it exists.
              </Alert>
            ) : (
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                Sign in to the panel.
              </Typography>
            )}
            <TextField label="Username" value={username} onChange={e => setUsername(e.target.value)}
                       autoFocus required autoComplete="username" size="small" />
            <TextField label="Password" type="password" value={password}
                       onChange={e => setPassword(e.target.value)} required
                       autoComplete="current-password" size="small" />
            {error && <Alert severity="error">{error}</Alert>}
            <Button type="submit" variant="contained" disabled={busy}>
              {busy ? 'Working…' : needsSetup ? 'Create admin account' : 'Sign in'}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
