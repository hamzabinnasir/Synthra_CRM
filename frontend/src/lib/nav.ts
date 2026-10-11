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
      { label: "WhatsApp", href: "/inbox/whatsapp", icon: "chat", soon: true },
      { label: "Facebook", href: "/inbox/facebook", icon: "chat", soon: true },
      { label: "Instagram", href: "/inbox/instagram", icon: "chat", soon: true },
    ],
  },
  {
    title: "Comments",
    items: [
      { label: "Facebook", href: "/comments/facebook", icon: "comment", soon: true },
      { label: "Instagram", href: "/comments/instagram", icon: "comment", soon: true },
    ],
  },
  {
    title: "Bot",
    items: [
      { label: "Knowledge base", href: "/knowledge-base", icon: "book", soon: true },
      { label: "Analytics", href: "/analytics", icon: "chart", soon: true },
      { label: "Notifications", href: "/notifications", icon: "bell", soon: true },
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
