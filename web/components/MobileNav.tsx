"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChartBarIcon,
  FolderIcon,
  CalendarDaysIcon,
  MicrophoneIcon,
  EllipsisHorizontalIcon,
  PresentationChartLineIcon,
  DocumentTextIcon,
  ShieldExclamationIcon,
  GlobeAltIcon,
  Cog6ToothIcon,
  ChatBubbleLeftRightIcon,
  ArrowRightOnRectangleIcon,
} from "@heroicons/react/24/outline";
import {
  ChartBarIcon as ChartBarSolid,
  FolderIcon as FolderSolid,
  CalendarDaysIcon as CalendarSolid,
  MicrophoneIcon as MicrophoneSolid,
} from "@heroicons/react/24/solid";
import { useState } from "react";
import { useAuth } from "@/app/contexts/AuthContext";

const brandColor = "#385854";

const mainTabs = [
  { name: "Home", href: "/", icon: ChartBarIcon, activeIcon: ChartBarSolid },
  { name: "Docs", href: "/document-library", icon: FolderIcon, activeIcon: FolderSolid },
  { name: "Meetings", href: "/meetings", icon: MicrophoneIcon, activeIcon: MicrophoneSolid },
  { name: "Calendar", href: "/calendar", icon: CalendarDaysIcon, activeIcon: CalendarSolid },
  { name: "More", href: "#more", icon: EllipsisHorizontalIcon, activeIcon: EllipsisHorizontalIcon },
];

type MoreItem = { name: string; href: string; icon: typeof FolderIcon; adminOnly?: boolean };

const moreItems: MoreItem[] = [
  { name: "Integrity", href: "/integrity", icon: ShieldExclamationIcon },
  { name: "Financials", href: "/financial-analysis", icon: DocumentTextIcon },
  { name: "Presentations", href: "/presentations", icon: PresentationChartLineIcon },
  { name: "Scraper", href: "/scraper", icon: GlobeAltIcon, adminOnly: true },
  { name: "Admin", href: "/admin", icon: Cog6ToothIcon, adminOnly: true },
];

const DISMISSED_CHAT_KEY = "ah_chat_dismissed";

export default function MobileNav() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [showMore, setShowMore] = useState(false);

  const isAdmin = !!user?.is_admin;
  const visibleMoreItems = moreItems.filter(i => !i.adminOnly || isAdmin);

  return (
    <>
      {/* More menu overlay */}
      {showMore && (
        <div className="fixed inset-0 z-40 bg-black/40" onClick={() => setShowMore(false)}>
          <div
            className="absolute bottom-16 left-0 right-0 bg-white border-t border-gray-200 rounded-t-2xl shadow-xl safe-area-bottom"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex justify-center pt-2 pb-1">
              <div className="w-10 h-1 rounded-full bg-gray-300" />
            </div>

            <div className="px-4 pt-2 pb-3 grid grid-cols-3 gap-2">
              {visibleMoreItems.map(item => {
                const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                const Icon = item.icon;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setShowMore(false)}
                    className={`flex flex-col items-center gap-1.5 px-2 py-3 rounded-xl transition-colors min-h-[72px] justify-center ${
                      isActive ? "bg-gray-100" : "hover:bg-gray-50 active:bg-gray-100"
                    }`}
                    style={isActive ? { color: brandColor } : {}}
                  >
                    <Icon className="w-6 h-6" style={isActive ? { color: brandColor } : { color: "#4b5563" }} />
                    <span className="text-[11px] font-medium text-center leading-tight text-gray-700">
                      {item.name}
                    </span>
                  </Link>
                );
              })}
              <button
                onClick={() => {
                  // Touch counterpart to Cmd/Ctrl+/ on desktop. The chat
                  // listens for this event and re-opens itself.
                  localStorage.removeItem(DISMISSED_CHAT_KEY);
                  window.dispatchEvent(new Event("ah:show-chat"));
                  setShowMore(false);
                }}
                className="flex flex-col items-center gap-1.5 px-2 py-3 rounded-xl hover:bg-gray-50 active:bg-gray-100 transition-colors min-h-[72px] justify-center"
              >
                <ChatBubbleLeftRightIcon className="w-6 h-6 text-gray-600" />
                <span className="text-[11px] font-medium text-center leading-tight text-gray-700">
                  Show chat
                </span>
              </button>
            </div>

            {user && (
              <div className="px-4 py-3 border-t border-gray-100 flex items-center justify-between">
                <span className="text-xs text-gray-500 truncate flex-1 min-w-0">{user.email}</span>
                <button
                  onClick={() => {
                    setShowMore(false);
                    logout();
                  }}
                  className="ml-3 flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-gray-600 hover:bg-gray-100"
                >
                  <ArrowRightOnRectangleIcon className="w-4 h-4" />
                  Log out
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Bottom tab bar */}
      <nav className="fixed bottom-0 left-0 right-0 z-30 bg-white border-t border-gray-200 safe-area-bottom">
        <div className="flex items-center justify-around h-16 px-2">
          {mainTabs.map(tab => {
            const isMore = tab.href === "#more";
            const isActive = isMore ? showMore : (
              tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href)
            );
            const Icon = isActive ? tab.activeIcon : tab.icon;
            const baseClass = "flex flex-col items-center gap-0.5 px-3 py-1 min-w-0 min-h-[44px] justify-center";
            const labelClass = `text-[10px] font-medium ${isActive ? "" : "text-gray-400"}`;
            const labelStyle = isActive ? { color: brandColor } : {};
            const iconStyle = isActive ? { color: brandColor } : { color: "#9ca3af" };

            if (isMore) {
              return (
                <button
                  key={tab.name}
                  onClick={() => setShowMore(s => !s)}
                  className={baseClass}
                  aria-label="More navigation options"
                  aria-expanded={showMore}
                >
                  <Icon className="w-6 h-6" style={iconStyle} />
                  <span className={labelClass} style={labelStyle}>{tab.name}</span>
                </button>
              );
            }

            // Link uses Next router internally (no full page reload), so the
            // chat panel, scroll position, and React Query cache survive
            // navigation.
            return (
              <Link
                key={tab.name}
                href={tab.href}
                onClick={() => setShowMore(false)}
                className={baseClass}
                prefetch
              >
                <Icon className="w-6 h-6" style={iconStyle} />
                <span className={labelClass} style={labelStyle}>{tab.name}</span>
              </Link>
            );
          })}
        </div>
      </nav>
    </>
  );
}
