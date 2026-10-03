import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import CssBaseline from '@mui/material/CssBaseline';
import { ThemeProvider } from '@mui/material/styles';
import App from './App';
import { lightTheme, darkTheme } from './theme';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

const STORAGE_KEY = 'omnicloud-theme';

function ThemedApp() {
  const [mode, setMode] = React.useState<'light' | 'dark'>(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  });
  const toggle = React.useCallback(() => {
    setMode(m => {
      const next = m === 'light' ? 'dark' : 'light';
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);
  return (
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider theme={mode === 'dark' ? darkTheme : lightTheme}>
          <CssBaseline />
          <BrowserRouter>
            <App themeMode={mode} onToggleTheme={toggle} />
          </BrowserRouter>
        </ThemeProvider>
      </QueryClientProvider>
    </React.StrictMode>
  );
}

createRoot(document.getElementById('root')!).render(<ThemedApp />);
