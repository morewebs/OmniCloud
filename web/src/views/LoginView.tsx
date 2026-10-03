import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { post } from '../api';

/** Login (and first-run setup: creates the first admin, then closes forever).
 * Split surface: the product states what it is; the form does one job. */
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
    <Box sx={{
      minHeight: '100dvh', display: 'grid',
      gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
    }}>
      <Box sx={{
        display: { xs: 'none', md: 'flex' }, flexDirection: 'column',
        justifyContent: 'space-between', p: 6,
        bgcolor: 'background.default',
        borderRight: 1, borderColor: 'divider',
      }}>
        <Typography variant="h5" sx={{ fontWeight: 600, letterSpacing: '-0.02em' }}>
          OmniCloud
        </Typography>
        <Stack spacing={2}>
          <Typography variant="h3" component="p" sx={{ maxWidth: 420 }}>
            Every server, every provider, one panel.
          </Typography>
          <Typography variant="body1" sx={{ color: 'text.secondary', maxWidth: 460 }}>
            One fleet list across Hetzner, LeaseWeb, OVHcloud, Gcore, Tube-hosting,
            Netlen, and LightNode — with each provider's traffic rules and billing
            written the way the provider actually bills.
          </Typography>
        </Stack>
        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          Open source · multi-user · self-hosted
        </Typography>
      </Box>

      <Box sx={{ display: 'grid', placeItems: 'center', p: 3, bgcolor: 'background.paper' }}>
        <Stack spacing={2.5} component="form" onSubmit={submit} sx={{ width: '100%', maxWidth: 340 }}>
          <Typography variant="h5" component="h1" sx={{ mb: 1 }}>
            {needsSetup ? 'Create the first admin' : 'Sign in'}
          </Typography>
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
          <Button type="submit" variant="contained" disabled={busy} size="large">
            {busy ? 'Signing in…' : needsSetup ? 'Create admin account' : 'Sign in'}
          </Button>
        </Stack>
      </Box>
    </Box>
  );
}
