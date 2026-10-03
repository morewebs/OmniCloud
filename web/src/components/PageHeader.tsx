import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

/** One consistent page rhythm: title + subtitle left, actions right. */
export function PageHeader({ title, subtitle, actions }: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <Stack direction="row" sx={{ alignItems: 'flex-end', justifyContent: 'space-between', flexWrap: 'wrap', gap: 2 }}>
      <Stack>
        <Typography variant="h3" component="h1">{title}</Typography>
        {subtitle && (
          <Typography variant="body2" sx={{ color: 'text.secondary', mt: 0.5 }}>
            {subtitle}
          </Typography>
        )}
      </Stack>
      {actions && <Stack direction="row" spacing={1}>{actions}</Stack>}
    </Stack>
  );
}
