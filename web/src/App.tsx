import { useEffect } from 'react';
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useQuery, useQueryClient } from '@tanstack/react-query';
import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Drawer from '@mui/material/Drawer';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Toolbar from '@mui/material/Toolbar';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import CloudSyncIcon from '@mui/icons-material/CloudSync';
import DnsIcon from '@mui/icons-material/Dns';
import RefreshIcon from '@mui/icons-material/Refresh';
import KeyIcon from '@mui/icons-material/Key';
import SettingsIcon from '@mui/icons-material/Settings';
import PeopleIcon from '@mui/icons-material/People';
import ExtensionIcon from '@mui/icons-material/Extension';
import LogoutIcon from '@mui/icons-material/Logout';
import { api, post, subscribeStream } from './api';
import { FleetView } from './views/FleetView';
import { AllowancesView } from './views/AllowancesView';
import { CredentialsView } from './views/CredentialsView';
import { AdaptersView } from './views/AdaptersView';
import { UsersView } from './views/UsersView';
import { SettingsView } from './views/SettingsView';
import { LoginView } from './views/LoginView';
import type { FleetResponse } from './types';

const DRAWER_W = 240;
const NAV = [
  { to: '/', label: 'Fleet', icon: <DnsIcon /> },
  { to: '/allowances', label: 'Allowances & billing', icon: <CloudSyncIcon /> },
  { to: '/credentials', label: 'Credentials', icon: <KeyIcon /> },
  { to: '/adapters', label: 'Adapters', icon: <ExtensionIcon /> },
];

function Shell({ user }: { user: { id: number; username: string; role: string } }) {
  const qc = useQueryClient();
  const fleet = useQuery<FleetResponse>({
    queryKey: ['fleet'],
    queryFn: () => api<FleetResponse>('/api/fleet'),
  });
  // SSE -> invalidate: live values swap in place, no polling loops needed.
  useEffect(() => subscribeStream((ev) => {
    if (ev === 'servers_updated') qc.invalidateQueries({ queryKey: ['fleet'] });
  }), [qc]);

  const errCount = Object.values(fleet.data?.sync ?? {}).filter(s => s.last_error).length;

  return (
    <Box sx={{ display: 'flex' }}>
      <AppBar position="fixed" color="inherit" elevation={0}
              sx={{ borderBottom: 1, borderColor: 'divider', zIndex: t => t.zIndex.drawer + 1 }}>
        <Toolbar sx={{ minHeight: 56, gap: 1 }}>
          <Typography variant="h6" sx={{ mr: 2 }}>OmniCloud</Typography>
          <Box sx={{ flex: 1 }} />
          {errCount > 0 && (
            <Tooltip title={Object.entries(fleet.data?.sync ?? {})
              .filter(([, s]) => s.last_error)
              .map(([id, s]) => `Account ${id}: ${s.last_error}`).join('\n')}>
              <Chip size="small" color="warning" variant="outlined" label={`${errCount} sync error${errCount > 1 ? 's' : ''}`} />
            </Tooltip>
          )}
          <Button
            size="small"
            startIcon={<RefreshIcon />}
            onClick={() => qc.invalidateQueries()}
            disabled={fleet.isFetching}
          >
            Refresh
          </Button>
          <Chip size="small" variant="outlined" label={`${user.username} · ${user.role}`} />
          <IconButton size="small" onClick={() => post('/api/auth/logout').then(() => location.reload())}
                      aria-label="Sign out">
            <LogoutIcon fontSize="small" />
          </IconButton>
        </Toolbar>
      </AppBar>
      <Drawer variant="permanent" sx={{ width: DRAWER_W, flexShrink: 0,
        '& .MuiDrawer-paper': { width: DRAWER_W, boxSizing: 'border-box', pt: '64px' } }}>
        <List sx={{ pt: 1 }}>
          {NAV.map(n => (
            <ListItemButton key={n.to} component={NavLink} to={n.to}
                            sx={{ '&.active': { bgcolor: 'action.selected' } }}>
              <ListItemIcon sx={{ minWidth: 40 }}>{n.icon}</ListItemIcon>
              <ListItemText primary={n.label} slotProps={{ primary: { sx: { fontSize: 14 } } }} />
            </ListItemButton>
          ))}
          {user.role === 'admin' && (
            <>
              <Divider sx={{ my: 1 }} />
              <ListItemButton component={NavLink} to="/users"
                              sx={{ '&.active': { bgcolor: 'action.selected' } }}>
                <ListItemIcon sx={{ minWidth: 40 }}><PeopleIcon /></ListItemIcon>
                <ListItemText primary="Users" slotProps={{ primary: { sx: { fontSize: 14 } } }} />
              </ListItemButton>
              <ListItemButton component={NavLink} to="/settings"
                              sx={{ '&.active': { bgcolor: 'action.selected' } }}>
                <ListItemIcon sx={{ minWidth: 40 }}><SettingsIcon /></ListItemIcon>
                <ListItemText primary="Settings" slotProps={{ primary: { sx: { fontSize: 14 } } }} />
              </ListItemButton>
            </>
          )}
        </List>
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, p: 3, pt: '72px', maxWidth: 1600, minWidth: 0 }}>
        <Routes>
          <Route path="/" element={<FleetView />} />
          <Route path="/allowances" element={<AllowancesView />} />
          <Route path="/credentials" element={<CredentialsView />} />
          <Route path="/adapters" element={<AdaptersView />} />
          {user.role === 'admin' && <Route path="/users" element={<UsersView />} />}
          {user.role === 'admin' && <Route path="/settings" element={<SettingsView />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Box>
    </Box>
  );
}

export default function App() {
  
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api<{ id: number; username: string; role: string }>('/api/auth/me'),
    retry: false,
  });
  const status = useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api<{ needs_setup: boolean }>('/api/auth/status'),
    retry: false,
  });

  if (me.isPending || status.isPending) return null;
  if (me.isError) {
    return <LoginView needsSetup={!!status.data?.needs_setup} />;
  }
  return <Shell user={me.data!} />;
}
