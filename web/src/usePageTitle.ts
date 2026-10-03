import { useEffect } from 'react';

/** Tab title per view: "Fleet · OmniCloud". */
export function usePageTitle(page: string) {
  useEffect(() => {
    document.title = `${page} · OmniCloud`;
    return () => { document.title = 'OmniCloud'; };
  }, [page]);
}
