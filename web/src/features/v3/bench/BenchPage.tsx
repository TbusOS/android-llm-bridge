/** Bench (v3). Placeholder until the page lands; baseline docs/webui-preview-v3-bench.html. */
import { PageHead } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";

export function BenchPage() {
  const t = useT();
  return <PageHead title={t("台架", "Bench")} />;
}
