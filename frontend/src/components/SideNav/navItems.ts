export type MenuGroup = {
  title: string;
  url: string;
  className?: string;
  items: {
    title: string;
    url?: string;
    action?: string;
    isActive?: boolean;
  }[];
};

export const navItems: {
  versions: string[];
  navMain: MenuGroup[];
  adminMenu: MenuGroup[];
} = {
  versions: ["1.0.1", "1.1.0-alpha", "2.0.0-beta1"],
  navMain: [
    {
      title: "BeeLib",
      url: "#",
      items: [
        {
          title: "Dashboard",
          url: "/",
          isActive: true,
        },
        {
          title: "本棚",
          url: "#",
        },
        {
          title: "読書中",
          url: "#",
        },
        {
          title: "お気に入り",
          url: "#",
        },
      ],
    },
  ],
  adminMenu: [
    {
      title: "BeeLib",
      url: "#",
      className: "mt-auto",
      items: [
        {
          title: "管理画面",
          url: "#",
        },
        {
          title: "マイページ",
          url: "#",
        },
        {
          title: "ログアウト",
          action: "logout",
        },
      ],
    },
  ],
};
