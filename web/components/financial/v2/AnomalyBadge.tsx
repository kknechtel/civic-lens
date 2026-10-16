"use client";

import { type AnomalyFlag } from "@/lib/api";
import { ExclamationTriangleIcon, InformationCircleIcon, ShieldExclamationIcon } from "@heroicons/react/24/outline";

const STYLES: Record<AnomalyFlag["severity"], string> = {
  info: "bg-blue-50 border-blue-200 text-blue-800",
  warn: "bg-amber-50 border-amber-300 text-amber-900",
  high: "bg-red-50 border-red-300 text-red-900",
};

const ICONS = {
  info: InformationCircleIcon,
  warn: ExclamationTriangleIcon,
  high: ShieldExclamationIcon,
};

export default function AnomalyBadge({ flag }: { flag: AnomalyFlag }) {
  const Icon = ICONS[flag.severity];
  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${STYLES[flag.severity]}`}>
      <Icon className="w-4 h-4 flex-shrink-0 mt-0.5" />
      <div className="flex-1">
        <div className="font-medium uppercase tracking-wide opacity-70 text-[10px]">{flag.code.replace(/_/g, " ")}</div>
        <div>{flag.message}</div>
      </div>
    </div>
  );
}
