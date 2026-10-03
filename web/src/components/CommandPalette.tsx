import { useEffect, useMemo, useRef, useState } from 'react';
import Dialog from '@mui/material/Dialog';
import InputBase from '@mui/material/InputBase';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { useQueryClient } from '@tanstack/react-query';
import type { FleetResponse } from '../types';

interface Item {
  label: string;
  hint: string;
  to: string; // route to navigate to; '/fleet?q=<name>' pre-searches
}

/** Subsequence fuzzy match: "fsn1" matches "srv-fsn1-01". */
function fuzzy(needle: string, haystack: string): boolean {
  let i = 0;
  const n = needle.toLowerCase(), h = haystack.toLowerCase();
  for (const ch of h) {
    if (i < n.length && ch === n[i]) i++;
  }
  return i === n.length;
}

/**
 * Ctrl+K command palette: navigation + server search, no dependency.
 * Servers come from the cached fleet query (no extra fetch).
 */
export function CommandPalette({ open, onClose, onNavigate }: {
  open: boolean;
  onClose: () => void;
  onNavigate: (to: string) => void;
}) {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) { setQ(''); setSel(0); setTimeout(() => inputRef.current?.focus(), 0); } }, [open]);

  const servers = useMemo(() => {
    const fleet = qc.getQueryData<FleetResponse>(['fleet']);
    return fleet?.accounts.flatMap(a => a.servers) ?? [];
  }, [qc, open]);

  const items = useMemo<Item[]>(() => {
    const nav: Item[] = [
      { label: 'Overview', hint: 'page', to: '/overview' },
      { label: 'Fleet', hint: 'page', to: '/fleet' },
      { label: 'Catalog', hint: 'page', to: '/catalog' },
      { label: 'Orders', hint: 'page', to: '/orders' },
      { label: 'Allowances & billing', hint: 'page', to: '/allowances' },
      { label: 'Credentials', hint: 'page', to: '/credentials' },
      { label: 'Adapters', hint: 'page', to: '/adapters' },
    ];
    const srv: Item[] = servers.map(s => ({
      label: s.name,
      hint: `${s.adapter} · ${s.ipv4 ?? 'no IP'}`,
      to: `/fleet?q=${encodeURIComponent(s.name)}`,
    }));
    const all = [...nav, ...srv];
    if (!q) return all.slice(0, 12);
    return all.filter(it => fuzzy(q, it.label) || fuzzy(q, it.hint)).slice(0, 12);
  }, [q, servers]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => (s + 1) % items.length); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => (s - 1 + items.length) % items.length); }
    if (e.key === 'Enter' && items[sel]) { onNavigate(items[sel].to); }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth
            slotProps={{ paper: { sx: { p: 0, overflow: 'hidden' } } }}>
      <Stack sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <InputBase
          inputRef={inputRef}
          placeholder="Search pages and servers…"
          value={q}
          onChange={e => { setQ(e.target.value); setSel(0); }}
          onKeyDown={onKey}
          sx={{ fontSize: '0.9375rem' }}
          autoFocus
        />
      </Stack>
      <List dense disablePadding sx={{ maxHeight: 360, overflowY: 'auto' }}>
        {items.length === 0 && (
          <ListItem>
            <ListItemText primary="No matches" slotProps={{ primary: { sx: { color: 'text.secondary' } } }} />
          </ListItem>
        )}
        {items.map((it, i) => (
          <ListItemButton key={it.to + it.label} selected={i === sel}
                          onClick={() => onNavigate(it.to)} disableGutters sx={{ px: 2 }}>
            <Stack sx={{ width: '100%', minWidth: 0 }}>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>{it.label}</Typography>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>{it.hint}</Typography>
            </Stack>
          </ListItemButton>
        ))}
      </List>
    </Dialog>
  );
}
