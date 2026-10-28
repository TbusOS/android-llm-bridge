/** Chat (v3). Placeholder until the page lands; baseline docs/webui-preview-v3-chat.html. */
import { PageHead } from "../../../components/v3/ui";
import { useT } from "../../../lib/i18n";

export function ChatPage3() {
  const t = useT();
  return <PageHead title={t("对话", "Chat")} />;
}
