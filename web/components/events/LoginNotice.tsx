"use client";

// LoginNotice — banner shown on auth-gated events-app screens when the
// visitor is browsing anonymously. Pages stay readable (feed/board show
// up), but write actions need an account, so we surface a clear CTA at
// the top instead of letting buttons silently 401.

import Link from "next/link";
import { LockClosedIcon } from "@heroicons/react/24/outline";

const eventsBrand = "#1d7a6c";

export default function LoginNotice({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <div
      className="rounded-lg border p-3 flex items-start gap-3"
      style={{ borderColor: `${eventsBrand}40`, backgroundColor: `${eventsBrand}08` }}
    >
      <LockClosedIcon
        className="w-5 h-5 flex-shrink-0 mt-0.5"
        style={{ color: eventsBrand }}
      />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-semibold text-gray-900">{title}</div>
        <div className="text-xs text-gray-600 mt-0.5">{detail}</div>
        <div className="flex flex-wrap gap-2 mt-2">
          <Link
            href="/login"
            className="px-3 py-1.5 text-xs font-medium rounded-md text-white"
            style={{ backgroundColor: eventsBrand }}
          >
            Sign in
          </Link>
          <Link
            href="/signup"
            className="px-3 py-1.5 text-xs rounded-md border border-gray-300 text-gray-700 hover:bg-gray-50"
          >
            Create account
          </Link>
        </div>
      </div>
    </div>
  );
}
