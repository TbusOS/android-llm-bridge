/** Timeline (v3). Placeholder until the page lands; baseline docs/webui-preview-v3-timeline.html. */
import { PageHead } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";

export function TimelinePage() {
  const t = useT();
  return <PageHead title={t("时间轴", "Timeline")} />;
}
