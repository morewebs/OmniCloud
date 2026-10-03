import React from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Stack from '@mui/material/Stack';

/**
 * Last line of defense: a render-time throw (bad data row, chart datum,
 * unexpected null) must never white-screen the whole panel for an operator
 * at 3am. The boundary contains it to a readable error + reload.
 */
export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    // full detail to the console for the bug report; nothing sensitive - the
    // app renders data, and tokens never reach the client
    console.error('OmniCloud render error:', error);
  }

  render() {
    if (this.state.error) {
      return (
        <Stack sx={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', p: 2 }}>
          <Stack spacing={2} sx={{ maxWidth: 480 }}>
            <Alert severity="error">
              Something broke in the panel. Your servers are untouched — this is
              a display error.
            </Alert>
            <Typography2 message={this.state.error.message} />
            <Button variant="contained" onClick={() => window.location.reload()}>
              Reload the panel
            </Button>
          </Stack>
        </Stack>
      );
    }
    return this.props.children;
  }
}

function Typography2({ message }: { message: string }) {
  return (
    <span style={{ fontFamily: '"Geist Mono", monospace', fontSize: 12, opacity: 0.7 }}>
      {message}
    </span>
  );
}
