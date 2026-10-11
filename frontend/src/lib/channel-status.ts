import type { BadgeTone } from "@/components/form";
import type { ChannelView } from "./settings-api";

/** Badge tone + label for a channel's connection state (backend status: not_configured | configured | connected | error). */
export function channelStatus(c: Pick<ChannelView, "status" | "enabled">): { tone: BadgeTone; label: string } {
  switch (c.status) {
    case "connected":
      return { tone: "success", label: c.enabled ? "Connected" : "Connected (paused)" };
    case "error":
      return { tone: "danger", label: "Connection failed" };
    case "configured":
      return { tone: "info", label: "Saved, not tested" };
    default:
      return { tone: "neutral", label: "Not set up" };
  }
}
