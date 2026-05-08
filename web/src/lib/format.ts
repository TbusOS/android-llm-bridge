/**
 * Shared formatting helpers.
 *
 * 5/08 cleanup: `formatBytes` / `formatSize` had three near-identical
 * implementations across UartCaptureView / ScreenshotTab / FilesTab
 * (toFixed precision drifted: 1 dp for KB everywhere, but UART/screenshot
 * used 2 dp for MB while FilesTab used 1 dp + had a GB branch). L-020
 * N=3 threshold triggers extraction; this is the single source.
 *
 * Convention: 1 dp for KB/MB (compact), 2 dp for GB (when the number is
 * already small enough that the extra digit is informative). Caller
 * passes raw bytes; output is e.g. "12.3 KB" / "4.2 MB" / "1.05 GB".
 */

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}
