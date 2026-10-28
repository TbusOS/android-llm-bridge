/** Flash (v3). Placeholder until the page lands; baseline docs/webui-preview-v3-flash.html. */
import { PageHead } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";

export function FlashPage() {
  const t = useT();
  return <PageHead title={t("烧录", "Flash")} />;
}
