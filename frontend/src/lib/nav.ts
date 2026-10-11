import type { IconName } from "@/components/icons";

export interface NavItem {
  label: string;
  href: string;
  icon: IconName;
  /** Route not built yet: rendered disabled with a "Soon" badge. Flip to false as each stage lands. */
  soon?: boolean;
}

export interface NavGroup {
  title?: string;
  items: NavItem[];
}

/** Single source of truth for the sidebar. Channel ids match the backend registry. */
export const NAV: NavGroup[] = [
  { items: [{ label: "Overview", href: "/dashboard", icon: "home" }] },
  {
    title: "Inboxes",
    items: [
      { label: "WhatsApp", href: "/inbox/whatsapp", icon: "chat" },
      { label: "Facebook", href: "/inbox/facebook", icon: "chat" },
      { label: "Instagram", href: "/inbox/instagram", icon: "chat" },
    ],
  },
  {
    title: "Comments",
    items: [
      { label: "Facebook", href: "/comments/facebook", icon: "comment" },
      { label: "Instagram", href: "/comments/instagram", icon: "comment" },
    ],
  },
  {
    title: "Bot",
    items: [
      { label: "Knowledge base", href: "/knowledge-base", icon: "book" },
      { label: "Analytics", href: "/analytics", icon: "chart" },
      { label: "Notifications", href: "/notifications", icon: "bell" },
    ],
  },
  {
    title: "Settings",
    items: [
      { label: "Database", href: "/settings/database", icon: "database" },
      { label: "Channels", href: "/settings/channels", icon: "plug" },
    ],
  },
];
