/** Console (v3). Placeholder until the page lands; baseline docs/webui-preview-v3-console.html. */
import { PageHead } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";

export function ConsolePage() {
  const t = useT();
  return <PageHead title={t("控制台", "Console")} />;
}
