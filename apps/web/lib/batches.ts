/**
 * Split files into upload batches that stay under a request-body budget.
 *
 * The web origin proxies /api/* to the API, and the Next proxy buffers request
 * bodies up to `proxyClientMaxBodySize`. Keeping each multipart request small
 * (default 8 MB, at most 10 files) avoids truncated bodies and gives smoother
 * progress. A single file larger than the budget still goes alone in its own
 * batch; the proxy limit (next.config.ts) is set to cover the API's per-file max.
 */
export const DEFAULT_BATCH_BYTES = 8 * 1024 * 1024;
export const DEFAULT_BATCH_FILES = 10;

export function planBatches<T extends { size: number }>(
  files: readonly T[],
  maxBytes: number = DEFAULT_BATCH_BYTES,
  maxFiles: number = DEFAULT_BATCH_FILES,
): T[][] {
  const out: T[][] = [];
  let cur: T[] = [];
  let curBytes = 0;
  for (const f of files) {
    if (cur.length > 0 && (curBytes + f.size > maxBytes || cur.length >= maxFiles)) {
      out.push(cur);
      cur = [];
      curBytes = 0;
    }
    cur.push(f);
    curBytes += f.size;
  }
  if (cur.length) out.push(cur);
  return out;
}
