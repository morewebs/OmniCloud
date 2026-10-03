import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import { api, put } from '../api';
import type { AccountRow } from '../types';

/** Sync intervals: visible, editable, live (no restart). */
export function SettingsView() {
  const qc = useQueryClient();
  const settings = useQuery<Record<string, string>>({ queryKey: ['settings'],
    queryFn: () => api<Record<string, string>>('/api/settings') });
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  const [form, setForm] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data) setForm(settings.data);
  }, [settings.data]);

  if (settings.isPending || accounts.isPending) return null;
  if (settings.isError) return <Alert severity="error">{(settings.error as Error).message}</Alert>;

  const save = async () => {
    await put('/api/settings', form);
    setSaved(true);
    qc.invalidateQueries({ queryKey: ['fleet'] });
  };

  return (
    <Stack spacing={3}>
      <Typography variant="h5">Settings</Typography>

      <Stack spacing={2} sx={{ maxWidth: 420 }}>
        <Typography variant="subtitle1">Sync intervals (minutes)</Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          How often each provider account is refreshed. Changes apply on the
          next cycle without a restart.
        </Typography>
        {accounts.data!.map(a => {
          const key = `sync_interval:${a.id}`;
          const val = form[key] ?? '5';
          return (
            <TextField
              key={a.id}
              className="num"
              label={`${a.name} (${a.adapter})`}
              value={val}
              onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
              type="number" size="small" slotProps={{ input: { inputProps: { min: 1 } } }}
            />
          );
        })}
        <Stack direction="row" spacing={1}>
          <Button variant="contained" onClick={save}>Save</Button>
          {saved && <Alert severity="success" sx={{ py: 0 }}>Saved</Alert>}
        </Stack>
      </Stack>
    </Stack>
  );
}
