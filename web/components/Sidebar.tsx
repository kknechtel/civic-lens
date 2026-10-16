"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/app/contexts/AuthContext";
import { useMunicipality } from "@/app/contexts/MunicipalityContext";
import {
  FolderIcon, ChartBarIcon, Cog6ToothIcon, ArrowRightOnRectangleIcon,
  DocumentTextIcon, BuildingOfficeIcon, AcademicCapIcon, GlobeAltIcon,
  CalendarDaysIcon,
  ChevronLeftIcon, ChevronRightIcon, UserIcon,
  PresentationChartLineIcon,
  MicrophoneIcon, BellIcon, ShieldExclamationIcon,
} from "@heroicons/react/24/outline";

const defaultBrand = "#385854";

type Feature = "financial" | "meetings" | "events" | "presentations" | "contracts" | "alerts" | "integrity";

export const navItems: { name: string; href: string; icon: any; feature?: Feature; adminOnly?: boolean; children?: { name: string; href: string; icon: any }[] }[] = [
  { name: "Dashboard", href: "/", icon: ChartBarIcon },
  { name: "Documents", href: "/document-library", icon: FolderIcon },
  { name: "Integrity", href: "/integrity", icon: ShieldExclamationIcon, feature: "integrity" },
  {
    name: "Financials",
    href: "/financial-analysis",
    icon: DocumentTextIcon,
    feature: "financial",
    children: [
      { name: "Statements", href: "/financial-analysis/statements", icon: DocumentTextIcon },
      { name: "Town", href: "/financial-analysis?entity=town", icon: BuildingOfficeIcon },
      { name: "School", href: "/financial-analysis?entity=school", icon: AcademicCapIcon },
    ],
  },
  { name: "Calendar", href: "/calendar", icon: CalendarDaysIcon, feature: "events" },
  { name: "Meetings", href: "/meetings", icon: MicrophoneIcon, feature: "meetings" },
  { name: "Alerts", href: "/alerts", icon: BellIcon, feature: "alerts" },
  { name: "Presentations", href: "/presentations", icon: PresentationChartLineIcon, feature: "presentations" },
  { name: "Scraper", href: "/scraper", icon: GlobeAltIcon, adminOnly: true },
];

export default function Sidebar() {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const municipality = useMunicipality();
  const brandColor = municipality.branding.theme_color || defaultBrand;

  if (!user) return null;

  const sidebar = (
    <aside className={`bg-white border-r border-gray-200 flex flex-col transition-all ${collapsed ? "w-16" : "w-64"} h-full`}>
      {/* Header */}
      <div className="p-4 border-b border-gray-200">
        <div className="flex items-center justify-between">
          {!collapsed && (
            <div className="flex items-center gap-2.5">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/icon-192.png"
                alt=""
                className="w-10 h-10 object-contain flex-shrink-0"
              />
              <div>
                <h1 className="text-sm font-semibold text-gray-900 leading-tight">{municipality.short_name || municipality.branding.app_name}</h1>
                <p className="text-[10px] text-gray-500">Document Intelligence</p>
              </div>
            </div>
          )}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="p-1.5 rounded-md hover:bg-gray-100 text-gray-500 hover:text-gray-700"
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? (
              <ChevronRightIcon className="w-5 h-5" />
            ) : (
              <ChevronLeftIcon className="w-5 h-5" />
            )}
          </button>
        </div>
      </div>

      {/* Nav — Admin link moved to the bottom panel next to email/logout. */}
      <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
        {navItems.filter((item) => (!item.feature || municipality.features[item.feature]) && (!item.adminOnly || user?.is_admin)).map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
          const Icon = item.icon;

          return (
            <div key={item.name}>
              <Link href={item.href}
                className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm transition-all ${
                  isActive ? "text-white shadow-md" : "text-gray-700 hover:bg-gray-50"
                }`}
                style={isActive ? { backgroundColor: brandColor } : {}}
                title={collapsed ? item.name : undefined}
              >
                <Icon className="w-5 h-5 flex-shrink-0" />
                {!collapsed && <span className="font-medium">{item.name}</span>}
              </Link>
              {!collapsed && item.children && isActive && (
                <div className="ml-8 mt-0.5 space-y-0.5">
                  {item.children.map((child) => {
                    const childActive = pathname === child.href;
                    return (
                      <Link key={child.name} href={child.href}
                        className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs transition-colors ${
                          childActive ? "font-medium" : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                        }`}
                        style={childActive ? { color: brandColor } : {}}
                      >
                        <child.icon className="w-3.5 h-3.5" /><span>{child.name}</span>
                      </Link>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* User — admin link lives here next to the email so it's clearly an
          account-level surface, not part of the primary product nav. */}
      <div className="p-4 border-t border-gray-200">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-gray-200 rounded-full flex items-center justify-center flex-shrink-0">
            <UserIcon className="w-4 h-4 text-gray-600" />
          </div>
          {!collapsed && (
            <div className="flex-1 min-w-0">
              <Link href="/settings" className="block text-sm font-medium text-gray-900 truncate hover:underline" title="Settings & API keys">{user.email}</Link>
            </div>
          )}
          {user.is_admin && (
            <Link
              href="/admin"
              className={`p-1.5 rounded-md hover:bg-gray-100 text-gray-500 hover:text-gray-700 ${
                pathname === "/admin" || pathname.startsWith("/admin/") ? "bg-gray-100 text-gray-900" : ""
              }`}
              title="Admin"
            >
              <Cog6ToothIcon className="w-4 h-4" />
            </Link>
          )}
          <button onClick={logout} className="p-1.5 rounded-md hover:bg-gray-100 text-gray-500 hover:text-gray-700" title="Logout">
            <ArrowRightOnRectangleIcon className="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>
  );

  // Sidebar is only mounted in the desktop branch of AuthGate (Providers.tsx)
  // — mobile uses MobileNav instead. So we render the sidebar directly with
  // no mobile-specific overlay logic.
  return sidebar;
}
