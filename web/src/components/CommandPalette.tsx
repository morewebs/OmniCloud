import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import Dialog from '@mui/material/Dialog';
import InputBase from '@mui/material/InputBase';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListSubheader from '@mui/material/ListSubheader';
import Typography from '@mui/material/Typography';
import Stack from '@mui/material/Stack';
import { useQueryClient } from '@tanstack/react-query';
import type { FleetResponse, OrderRow, Plan } from '../types';

interface Item {
  label: string;
  hint: string;
  to: string; // route to navigate to; '/fleet?q=<name>' pre-searches
  group: string;
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
 * Ctrl+K command palette: navigation + server, order, and plan search,
 * no dependency. All data comes from cached queries (no extra fetches).
 */
export function CommandPalette({ open, onClose, onNavigate, isAdmin }: {
  open: boolean;
  onClose: () => void;
  onNavigate: (to: string) => void;
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (open) { setQ(''); setSel(0); inputRef.current?.focus(); }
  }, [open]);

  const servers = useMemo(() => {
    const fleet = qc.getQueryData<FleetResponse>(['fleet']);
    return fleet?.accounts.flatMap(a => a.servers) ?? [];
  }, [qc, open]);

  const orders = useMemo(() =>
    qc.getQueryData<OrderRow[]>(['orders']) ?? [], [qc, open]);

  const plans = useMemo(() => {
    const cat = qc.getQueryData<{ plans: Record<string, Plan[]> }>(['catalog']);
    if (!cat) return [];
    const out: (Plan & { adapter: string })[] = [];
    for (const [adapter, list] of Object.entries(cat.plans)) {
      for (const p of list) out.push({ ...p, adapter });
    }
    return out;
  }, [qc, open]);

  const items = useMemo<Item[]>(() => {
    const nav: Item[] = [
      { label: 'Overview', hint: 'page', to: '/overview', group: 'Pages' },
      { label: 'Fleet', hint: 'page', to: '/fleet', group: 'Pages' },
      { label: 'Catalog', hint: 'page', to: '/catalog', group: 'Pages' },
      { label: 'Orders', hint: 'page', to: '/orders', group: 'Pages' },
      { label: 'Billing', hint: 'page', to: '/allowances', group: 'Pages' },
      { label: 'Credentials', hint: 'page', to: '/credentials', group: 'Pages' },
      { label: 'Adapters', hint: 'page', to: '/adapters', group: 'Pages' },
      ...(isAdmin ? [
        { label: 'Users', hint: 'page', to: '/users', group: 'Pages' },
        { label: 'Settings', hint: 'page', to: '/settings', group: 'Pages' },
      ] : []),
    ];
    const srv: Item[] = servers.map(s => ({
      label: s.name,
      hint: `${s.adapter} · ${s.ipv4 ?? 'no IP'}`,
      to: `/fleet?q=${encodeURIComponent(s.name)}`,
      group: 'Servers',
    }));
    const ord: Item[] = orders.map(o => ({
      label: `Order #${o.id} — ${o.plan_name}`,
      hint: `${o.adapter} · ${o.status}`,
      to: '/orders',
      group: 'Orders',
    }));
    const pln: Item[] = plans.slice(0, 400).map(p => ({
      label: `${p.name} · ${p.adapter}`,
      hint: p.price_monthly
        ? `${p.price_monthly.currency} ${p.price_monthly.amount}/mo · ${p.location}`
        : 'price not published',
      to: '/catalog',
      group: 'Plans',
    }));
    // empty query: pages first, then a handful of servers — never arbitrary soup
    const all = [...nav, ...srv.slice(0, 5), ...ord.slice(0, 3), ...pln.slice(0, 4)];
    if (!q) return all;
    return all.filter(it => fuzzy(q, it.label) || fuzzy(q, it.hint)).slice(0, 14);
  }, [q, servers, orders, plans, isAdmin]);

  // keep the keyboard selection inside the viewport while arrowing
  useEffect(() => {
    listRef.current
      ?.querySelectorAll('li[data-selected="true"]')[0]
      ?.scrollIntoView({ block: 'nearest' });
  }, [sel, items]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setSel(s => (s + 1) % items.length); }
    if (e.key === 'ArrowUp') { e.preventDefault(); setSel(s => (s - 1 + items.length) % items.length); }
    if (e.key === 'Enter' && items[sel]) { onNavigate(items[sel].to); }
  };

  // group headers only when the group actually changes
  let lastGroup = '';
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth
            slotProps={{ paper: { sx: { p: 0, overflow: 'hidden' } } }}>
      <Stack sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <InputBase
          inputRef={inputRef}
          placeholder="Search pages, servers, orders, plans…"
          aria-label="Command palette search"
          role="combobox"
          aria-expanded="true"
          aria-controls="omni-palette-list"
          value={q}
          onChange={e => { setQ(e.target.value); setSel(0); }}
          onKeyDown={onKey}
          sx={{ fontSize: '0.9375rem' }}
          autoFocus
        />
      </Stack>
      <List dense disablePadding ref={listRef} id="omni-palette-list"
            sx={{ maxHeight: 400, overflowY: 'auto' }}>
        {items.length === 0 && (
          <ListItemButton disabled>
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>No matches</Typography>
          </ListItemButton>
        )}
        {items.map((it, i) => {
          const header = it.group !== lastGroup ? it.group : null;
          lastGroup = it.group;
          return (
            <Fragment key={it.to + it.label}>
              {header && <ListSubheader sx={{ lineHeight: '24px', bgcolor: 'background.paper' }}>
                {header}
              </ListSubheader>}
              <ListItemButton selected={i === sel} data-selected={i === sel}
                              onClick={() => onNavigate(it.to)} disableGutters sx={{ px: 2 }}>
                <Stack sx={{ width: '100%', minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>{it.label}</Typography>
                  <Typography variant="caption" sx={{ color: 'text.secondary' }} noWrap>{it.hint}</Typography>
                </Stack>
              </ListItemButton>
            </Fragment>
          );
        })}
      </List>
    </Dialog>
  );
}
